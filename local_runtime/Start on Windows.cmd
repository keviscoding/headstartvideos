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
echo Install Python 3.13 from https://www.python.org/downloads/ and select Add Python to PATH. Standard mode needs Python 3.10 or newer; optional Fast mode needs 3.13 or newer. Then open this file again.
goto failed

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

:python_failed
echo This studio's Python environment cannot run. Standard mode needs Python 3.10 or newer; optional Fast mode needs Python 3.13 or newer.
echo Install Python 3.13, rename .venv to .venv-old, then reopen this launcher. Your output folder and saved accounts stay in place.
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
