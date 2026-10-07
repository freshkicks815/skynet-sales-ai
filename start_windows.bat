@echo off
title Skynet Sales AI
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto :error
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error
start "" "http://127.0.0.1:5000/finder"
".venv\Scripts\python.exe" app.py
goto :eof
:error
echo The app could not start. Copy the error above and send it for help.
pause
