@echo off
rem Deploy the Sky Task API to Cloud Run (tasks come from Firestore; nothing is baked in). Run from anywhere:  deploy\deploy_api.bat
setlocal
cd /d "%~dp0\.."
set PROJECT=sky-task-c4048
set REGION=us-central1
call gcloud run deploy skytask-api --source . --project %PROJECT% --region %REGION% ^
  --service-account skytask-api@%PROJECT%.iam.gserviceaccount.com ^
  --allow-unauthenticated --max-instances 1 --min-instances 1 --memory 512Mi --cpu 1 --timeout 60 ^
  --quiet || goto :error
echo.
echo Done. Test it:  curl -X POST https://YOUR-RUN-URL/v1/sessions -H "Content-Type: application/json" -d "{\"site_key\":\"demo\"}"
exit /b 0
:error
echo.
echo Deploy failed - copy the error above to Claude.
pause
exit /b 1
