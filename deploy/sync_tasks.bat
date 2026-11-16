@echo off
rem Push new generator tasks to the cloud every 10 minutes. Leave this window open. Ctrl+C to stop.
cd /d "%~dp0\.."
python deploy\sync_tasks.py --loop 600 %*
pause
