@echo off
cd /d "%~dp0"
if exist .venv\Scripts\python.exe goto check_python

set "CR_PYTHON=py"
set "CR_PYTHON_ARGS=-3"
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 goto create_environment
set "CR_PYTHON=python"
set "CR_PYTHON_ARGS="
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 goto create_environment
set "CR_PYTHON=python3"
python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 goto create_environment
echo Install Python 3.13 from https://www.python.org/downloads/ and select Add Python to PATH. Standard mode needs Python 3.10 or newer; optional Fast mode needs 3.13 or newer. Then open this file again.
goto failed

:create_environment
echo Creating your studio environment with: %CR_PYTHON% %CR_PYTHON_ARGS%
%CR_PYTHON% %CR_PYTHON_ARGS% --version
%CR_PYTHON% %CR_PYTHON_ARGS% -m venv .venv
if errorlevel 1 (
  echo Python could not create the studio environment. Check the error above, then reopen this launcher.
  goto failed
)

:check_python
.venv\Scripts\python.exe -c "import sys; print('Studio Python:', sys.version.split()[0]); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo This studio's .venv uses Python older than 3.10. Install Python 3.13, rename .venv to .venv-old, then reopen this launcher. Your output folder and saved accounts stay in place.
  goto failed
)

if not exist .venv\.cr-installed (
  echo Setting up ChannelRecipe. This first download may take a few minutes.
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 (
    echo The studio libraries could not finish installing. Check the error above and your connection, then reopen this launcher.
    goto failed
  )
  .venv\Scripts\python.exe -m playwright install chromium
  if errorlevel 1 (
    echo The graphics browser could not finish installing. Check the error above and your connection, then reopen this launcher.
    goto failed
  )
  type nul > .venv\.cr-installed
)
.venv\Scripts\python.exe cr_studio.py
set "CR_EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %CR_EXIT_CODE%

:failed
pause
exit /b 1
