@echo off
rem Deploy the demo pages + widget to Firebase Hosting. /v1/** is forwarded to the Cloud Run API (firebase.json).
setlocal
cd /d "%~dp0\.."
call firebase deploy --only hosting --project sky-task-c4048 || goto :error
echo.
echo Done: https://sky-task-c4048.web.app
exit /b 0
:error
echo.
echo Deploy failed - copy the error above to Claude.
pause
exit /b 1
