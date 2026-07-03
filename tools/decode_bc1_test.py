"""Quick test: decode the top mip level of a suspected BC1/DXT1 texture
(128-byte header + 1024x1024 BC1 data + mipchain) and save as PNG to inspect."""
import struct
import sys
from PIL import Image


def decode_bc1_block(data, off):
    c0, c1 = struct.unpack_from('<HH', data, off)
    indices = struct.unpack_from('<I', data, off + 4)[0]

    def unpack565(c):
        r = (c >> 11) & 0x1F
        g = (c >> 5) & 0x3F
        b = c & 0x1F
        return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)

    r0, g0, b0 = unpack565(c0)
    r1, g1, b1 = unpack565(c1)
    colors = [(r0, g0, b0), (r1, g1, b1)]
    if c0 > c1:
        colors.append(((2*r0+r1)//3, (2*g0+g1)//3, (2*b0+b1)//3))
        colors.append(((r0+2*r1)//3, (g0+2*g1)//3, (b0+2*b1)//3))
    else:
        colors.append(((r0+r1)//2, (g0+g1)//2, (b0+b1)//2))
        colors.append((0, 0, 0))

    pixels = []
    for i in range(16):
        idx = (indices >> (i * 2)) & 3
        pixels.append(colors[idx])
    return pixels


def decode_bc1(data, off, w, h):
    img = Image.new('RGB', (w, h))
    px = img.load()
    bx, by = (w + 3) // 4, (h + 3) // 4
    pos = off
    for by_i in range(by):
        for bx_i in range(bx):
            block = decode_bc1_block(data, pos)
            pos += 8
            for i in range(16):
                x = bx_i * 4 + (i % 4)
                y = by_i * 4 + (i // 4)
                if x < w and y < h:
                    px[x, y] = block[i]
    return img


if __name__ == '__main__':
    path = sys.argv[1]
    header_size = int(sys.argv[2]) if len(sys.argv) > 2 else 128
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 1024
    h = int(sys.argv[4]) if len(sys.argv) > 4 else 1024
    data = open(path, 'rb').read()
    img = decode_bc1(data, header_size, w, h)
    out = sys.argv[5] if len(sys.argv) > 5 else 'decoded.png'
    img.save(out)
    print('saved', out)
