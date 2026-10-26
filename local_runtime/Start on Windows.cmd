@echo off
setlocal DisableDelayedExpansion
if /i "%~1"=="--cr-window" goto launch_studio

rem Keep the diagnostic window open even if Windows rejects a later command.
set "CR_WINDOWS_LAUNCHER=%~f0"
start "ChannelRecipe Studio" "%ComSpec%" /d /k ""%%CR_WINDOWS_LAUNCHER%%" --cr-window"
if errorlevel 1 goto window_failed
exit /b 0

:window_failed
echo Windows could not open the studio window. Open Command Prompt and run this file there.
pause
exit /b 1

:launch_studio
title ChannelRecipe Studio
cd /d "%~dp0"
if errorlevel 1 goto folder_failed
echo ChannelRecipe Studio
echo Keep this window open while using the studio.
echo.
if not exist "cr_studio.py" goto incomplete_download
if not exist "requirements.txt" goto incomplete_download
if exist ".venv\Scripts\python.exe" goto check_python

set "CR_PYTHON=py"
set "CR_PYTHON_ARGS=-3.13"
py -3.13 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 goto create_environment

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
goto no_python

:create_environment
echo Creating your studio environment with: %CR_PYTHON% %CR_PYTHON_ARGS%
%CR_PYTHON% %CR_PYTHON_ARGS% --version
%CR_PYTHON% %CR_PYTHON_ARGS% -m venv .venv
if errorlevel 1 goto environment_failed

:check_python
".venv\Scripts\python.exe" -c "import sys; print('Studio Python:', sys.version.split()[0]); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto python_failed

if exist ".venv\.cr-installed" goto run_studio
echo Setting up ChannelRecipe. This first download may take a few minutes.
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto libraries_failed
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto browser_failed
type nul > ".venv\.cr-installed"
if errorlevel 1 goto marker_failed

:run_studio
rem The graphics browser is checked on every start, not only the first: a missing one is installed again here.
".venv\Scripts\python.exe" -c "import os,sys;from playwright.sync_api import sync_playwright as s;p=s().start();ok=os.path.exists(p.chromium.executable_path);p.stop();sys.exit(0 if ok else 1)" >nul 2>&1
if errorlevel 1 goto install_browser
goto start_studio

:install_browser
echo Installing the graphics browser. This can take a few minutes.
".venv\Scripts\python.exe" -m playwright install chromium

:start_studio
".venv\Scripts\python.exe" cr_studio.py
set "CR_EXIT_CODE=%ERRORLEVEL%"
echo.
echo The studio stopped with exit code %CR_EXIT_CODE%. Any error is shown above.
pause
exit /b %CR_EXIT_CODE%

:folder_failed
echo Windows could not open this download folder. Extract the full ZIP into a local folder and try again.
goto failed

:incomplete_download
echo The studio files are missing beside this launcher.
echo Right-click the downloaded ZIP, choose Extract All, then open Start on Windows.cmd inside the extracted ChannelRecipe folder.
goto failed

:environment_failed
echo Python could not create the studio environment. Check the error above, then reopen this launcher.
goto failed

:no_python
rem Python is the one tool Studio can't install for itself (it runs on it). FFmpeg, Node.js and Claude Code are
rem installed from Studio's setup page (Fix it), so this only ever asks for Python.
echo ChannelRecipe Studio needs Python 3.10 or newer, and this PC doesn't have it yet.
echo It's the one thing you install yourself:
echo.
echo   1. Your browser is opening python.org. Download the newest Python 3 for Windows, 3.13 or newer.
echo      If it didn't open, go to https://www.python.org/downloads/windows/
echo   2. Run the installer. On its first screen tick "Add python.exe to PATH", then press Install Now.
echo   3. Close this window and double-click Start on Windows.cmd again.
echo.
echo FFmpeg, Node.js and Claude Code don't need installing by hand: Studio's setup page installs them, one click each.
start "" "https://www.python.org/downloads/windows/"
goto failed

:python_failed
echo This Studio folder's Python environment (.venv) can't run: it needs Python 3.10 or newer (optional Fast mode needs 3.13 or newer).
echo To fix it:
echo   1. Install Python 3.13 or newer from https://www.python.org/downloads/windows/
echo   2. In this folder, rename the .venv folder to .venv-old.
echo   3. Open this launcher again. Your output folder and saved accounts stay in place.
goto failed

:libraries_failed
echo The studio libraries could not finish installing. Check the error above and your connection, then reopen this launcher.
goto failed

:browser_failed
echo The graphics browser could not finish installing. Check the error above and your connection, then reopen this launcher.
goto failed

:marker_failed
echo Windows could not save setup completion in this folder. Check its permissions and try again.
goto failed

:failed
echo.
echo Setup did not finish. This window stays open so you can read the error.
pause
exit /b 1
