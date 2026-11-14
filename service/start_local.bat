@echo off
rem Sky Task service, local: API at http://localhost:8080/v1 , website at http://localhost:8080/
rem Dev inspector routes (/v1/dev/...) are ON here. Never use this script on a server.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv || goto :error
)
".venv\Scripts\python.exe" -m pip install -q -r requirements-dev.txt || goto :error
if not exist "settings.local.toml" copy /y "settings.local.example.toml" "settings.local.toml" >nul
set SKYTASK_DEV=1
echo.
echo   Sky Task local service:  http://localhost:8080/   (API docs: http://localhost:8080/docs)
echo   Stop with Ctrl+C
echo.
".venv\Scripts\python.exe" -m uvicorn --factory app.main:create_app --host 127.0.0.1 --port 8080 || goto :error
goto :eof
:error
echo.
echo Something failed (see above). Is Python 3.10+ installed and on PATH? Is the path in settings.local.toml right?
pause
exit /b 1
