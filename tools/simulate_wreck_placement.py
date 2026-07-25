"""Placement-faithful simulation of SpaceCraft wreck generation.

Mirrors the decoded bytecode of logic.gen.PlanetRes (post-2026-07 findices):
  - getResGroupCount@11234: count = min + MIN(U1,U2) over {0..max-min};
    explodingChance branch: 2*min + MIN(U1,U2) over {0..3*max-2*min}.
  - generateGroup@11232 Groups arm: whole-group gate (sum-of-radii vs group
    size), per-entry count roll, direct-res 0->1 bump when the resource id
    hasn't been generated this run, 50-attempt instance retry with rollback,
    scatter d = sqrt(U)*2*sizeHint*groupDensity, sizeHint running average,
    anchor recentering to size-weighted centroid after each instance.
  - generateGroup Overrides arm: weighted pick, placed AT the given point,
    no gate, no scatter; failure bubbles to the parent attempt loop (fresh
    pick per attempt = the re-roll mechanic).
  - generateResource@11233: collision iff any partition node with
    dist < node.size + resSize; resource visible to centroid math
    immediately but added to the collision partition only AFTER its
    resGroupSpawn recursion completes (the partition-blind window).
"""
import random, math, collections

R = {  # getResSize = generation.size * (visuals.scale or 1)
    'Core':0.1,'Hull':7.0,'P1':25.0,'P2':20.0,'SP':15.0,
    'BlackBox':0.75,'Crate':0.75,
    'SteelScraps':0.4,'SteelHullScraps':0.4,'SystemScraps':0.2,
    'SteelBeam':0.28,'Plate1':0.4,'Microchip':0.2,'IronSheet':0.32,
    'CopperTube':0.25,'Coil':0.3,'Wire':0.28,'Motor':0.48,'SolarCell':0.12,
}
BASIC_LOOT = [('SteelScraps',52),('SteelHullScraps',80),('SystemScraps',15),
    ('SteelBeam',24),('Plate1',10),('Microchip',3),('IronSheet',10),
    ('CopperTube',5),('Coil',5),('Wire',5),('Motor',12),('SolarCell',10)]
BL_TOT = sum(w for _,w in BASIC_LOOT)

def roll_count(mn,mx,expl):
    if random.random()<expl:
        n=3*mx-2*mn
        return 2*mn+min(random.randint(0,n),random.randint(0,n))
    n=mx-mn
    return mn+min(random.randint(0,n),random.randint(0,n))

class Sim:
    def __init__(s):
        s.res=[]        # placed: (name,x,y,size)  - centroid sees these
        s.part=[]       # collision partition: indices into s.res
        s.generated=collections.Counter()
    def collide(s,x,y,r):
        for i in s.part:
            n=s.res[i]
            if (n[1]-x)**2+(n[2]-y)**2 < (n[3]+r)**2: return True
        return False
    def place_res(s,name,x,y,secondary=None):
        r=R[name]
        if s.collide(x,y,r): return False
        s.res.append((name,x,y,r)); idx=len(s.res)-1
        s.generated[name]+=1
        if secondary:               # resGroupSpawn: partition-blind window
            s.gen_group(secondary,x,y,0.0,)
        s.part.append(idx)
        return True
    def rollback(s,n):
        s.res=s.res[:n]; s.part=[i for i in s.part if i<n]
    def gen_group(s,G,ax,ay,size_hint,top=False):
        if G.get('overrides'):
            tot=sum(w for w,_ in G['overrides']); r=random.random()*tot
            for w,br in G['overrides']:
                r-=w
                if r<0:
                    if isinstance(br,str): return s.place_res(br,ax,ay)
                    return s.gen_group(br,ax,ay,size_hint)
            return False
        gsize=G['size']
        if not top and s.collide(ax,ay,gsize): return False
        expl=G.get('expl',0.0); dens=G.get('dens',1.0)
        start=len(s.res)
        for e in G['entries']:
            cnt=roll_count(e['min'],e['max'],expl)
            if e.get('res') and cnt==0 and e['max']>0 and s.generated[e['res']]==0:
                cnt=1
            if cnt==0: continue
            for _ in range(cnt):
                esize=R[e['res']] if e.get('res') else e['group'].get('size',1.0)
                n=len(s.res)-start
                size_hint=(size_hint*math.sqrt(n)+esize)/math.sqrt(n+1)
                saved=len(s.res); ok=False
                for _a in range(50):
                    d=math.sqrt(random.random())*2*size_hint*dens
                    th=random.random()*2*math.pi
                    px,py=ax+d*math.cos(th),ay+d*math.sin(th)
                    if e.get('res'):
                        ok=s.place_res(e['res'],px,py,e.get('secondary'))
                    else:
                        ok=s.gen_group(e['group'],px,py,size_hint)
                    if ok: break
                    s.rollback(saved)
                if not ok: return False
                placed=s.res[start:]
                tw=sum(p[3] for p in placed) or 1.0
                ax=sum(p[1]*p[3] for p in placed)/tw
                ay=sum(p[2]*p[3] for p in placed)/tw
        return True

def basicloot_group():
    return {'overrides':[(w,name) for name,w in BASIC_LOOT]}
def rareloot_group():
    return {'overrides':[(40,basicloot_group()),(25,'Crate')]}
def junk_group():
    return {'size':8.0,'dens':1.5,'expl':0.1,'entries':[
        {'min':20,'max':60,'group':basicloot_group()},
        {'min':0,'max':2,'group':rareloot_group()}]}
def junk_group_bb():
    return {'size':8.0,'dens':1.5,'entries':[
        {'min':20,'max':60,'group':basicloot_group()},
        {'min':0,'max':1,'res':'BlackBox'},
        {'min':0,'max':1,'group':rareloot_group()}]}
def dismantled_small():
    return {'size':7.0,'dens':1.0,'entries':[
        {'min':0,'max':4,'res':'Crate'},
        {'min':20,'max':60,'group':basicloot_group()}]}
def dismantled_big():
    return {'size':7.0,'dens':1.0,'entries':[
        {'min':3,'max':15,'group':rareloot_group()},
        {'min':20,'max':60,'group':basicloot_group()}]}

def small_root():
    return {'size':50.0,'dens':1.2,'entries':[
        {'min':1,'max':1,'res':'Core'},
        {'min':1,'max':1,'res':'Hull','secondary':dismantled_small()},
        {'min':1,'max':1,'group':junk_group_bb()},
        {'min':1,'max':2,'group':junk_group()}]}
def big_root(p2_secondary):
    return {'size':100.0,'dens':1.4,'entries':[
        {'min':1,'max':1,'res':'Core'},
        {'min':1,'max':1,'res':'P1','secondary':dismantled_big()},
        {'min':1,'max':1,'res':'P2','secondary':dismantled_big() if p2_secondary else None},
        {'min':1,'max':1,'res':'SP'},
        {'min':1,'max':1,'res':'SP'},
        {'min':1,'max':1,'group':junk_group_bb()},
        {'min':5,'max':10,'group':junk_group()}]}

def run(root_fn,N):
    tot=collections.Counter(); glued=collections.defaultdict(collections.Counter)
    junk_n=[]; bb=0; bbd=[]; rejects=0
    for _ in range(N):
        # run@11230 (PlanetRes.hx:261-298): failed attempts are fully rolled
        # back (cancelResources) and the wreck rerolled elsewhere until it
        # places completely - real wrecks are conditioned on total success.
        while True:
            s=Sim()
            if s.gen_group(root_fn,0,0,0.0,top=True): break
            rejects+=1
        crates=[r for r in s.res if r[0]=='Crate']
        tot[len(crates)]+=1
        pieces=[r for r in s.res if r[0] in ('Hull','P1','P2')]
        for p in pieces:
            g=sum(1 for c in crates if math.dist(p[1:3],c[1:3])<4)
            glued[p[0]][g]+=1
        junk_n.append(sum(1 for r in s.res if r[0] in dict(BASIC_LOOT)))
        boxes=[r for r in s.res if r[0]=='BlackBox']
        if boxes:
            bb+=1
            ref=pieces[0] if pieces else s.res[0]
            bbd.append(math.dist(boxes[0][1:3],ref[1:3]))
    n=N
    import statistics
    print(f"  crate total: mean={sum(k*v for k,v in tot.items())/n:.2f} dist={ {k:round(v/n,3) for k,v in sorted(tot.items())} }")
    for piece,c in glued.items():
        m=sum(c.values())
        print(f"  glued<4u at {piece}: {{ {', '.join(f'{k}:{v/m:.2f}' for k,v in sorted(c.items()))} }}")
    print(f"  junk/wreck mean={statistics.mean(junk_n):.0f}   blackbox present={bb/n:.2f} dist~{statistics.median(bbd):.0f}u   reroll rate={rejects/(n+rejects):.2f}")

random.seed(11)
print("SMALL wreck (client data.cdb as-is):"); run(small_root(),4000)
print("BIG wreck, P1 secondary only (client data.cdb):"); run(big_root(False),2000)
print("BIG wreck, P1+P2 secondaries (server hypothesis):"); run(big_root(True),2000)
