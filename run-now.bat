@echo off
setlocal

cd /d "%~dp0"

echo.
echo ============================================
echo  NotebookLM podcast - Run scheduled task now
echo ============================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-ScheduledTask -TaskName 'notebooklm-podcast-daily'; Start-Sleep -Seconds 2; Get-ScheduledTaskInfo -TaskName 'notebooklm-podcast-daily' | Select-Object LastRunTime, LastTaskResult, NextRunTime"

set EXITCODE=%ERRORLEVEL%

echo.
if %EXITCODE%==0 (
    echo [OK] Task started. Check logs\daily-update-*.log for progress.
) else (
    echo [FAIL] Task 'notebooklm-podcast-daily' is probably not registered.
    echo        Run install-scheduler.bat first.
)
echo.
pause
