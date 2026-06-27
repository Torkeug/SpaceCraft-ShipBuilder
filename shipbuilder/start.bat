@echo off
cd /d "%~dp0"
title Ship Builder

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  echo Ship Builder running at http://localhost:8765
  echo Close this window to stop.
  start "" http://localhost:8765
  python -m http.server 8765
  goto :eof
)

where node >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  echo Ship Builder running at http://localhost:8765
  echo Close this window to stop.
  start "" http://localhost:8765
  npx --yes serve -p 8765 .
  goto :eof
)

echo Neither Python nor Node.js was found.
echo Install either from python.org or nodejs.org, then try again.
pause
