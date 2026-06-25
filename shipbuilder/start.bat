@echo off
cd /d "%~dp0"
echo Starting Ship Builder...
start "" http://localhost:8765
python -m http.server 8765
