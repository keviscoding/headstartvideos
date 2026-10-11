@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 (
    echo Install Python 3.12 from https://www.python.org/downloads/ then open this file again.
    pause
    exit /b 1
  )
)
if not exist .venv\.cr-installed (
  echo Setting up ChannelRecipe. This first download may take a few minutes.
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto failed
  .venv\Scripts\python.exe -m playwright install chromium
  if errorlevel 1 goto failed
  type nul > .venv\.cr-installed
)
.venv\Scripts\python.exe cr_studio.py
:failed
pause
