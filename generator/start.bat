@echo off
rem skytask_gen starter for Windows. Double-click = run. Or: start.bat status | start.bat export | start.bat preview
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment in .venv ...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo Could not create a virtual environment. Install Python 3.10+ from python.org and try again.
    if "%~1"=="" pause
    exit /b 1
  )
)
if not exist ".venv\.installed" (
  echo Installing requirements ...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo pip install failed - see the messages above.
    if "%~1"=="" pause
    exit /b 1
  )
  echo ok> ".venv\.installed"
)
if not "%~1"=="" (
  ".venv\Scripts\python.exe" -m skytask_gen %*
  exit /b
)
:run
".venv\Scripts\python.exe" -m skytask_gen run
if errorlevel 4 goto failed
if errorlevel 3 (
  echo Generator was stuck on a network call - restarting in 10 seconds...
  timeout /t 10 /nobreak >nul
  goto run
)
if errorlevel 1 goto failed
exit /b 0
:failed
pause
