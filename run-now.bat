@echo off
chcp 65001 >nul 2>&1
setlocal

cd /d "%~dp0"

echo.
echo ============================================
echo  NotebookLM 팟캐스트 - 즉시 실행 (테스트)
echo ============================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-ScheduledTask -TaskName 'notebooklm-podcast-daily'; ^
   Start-Sleep -Seconds 2; ^
   Get-ScheduledTaskInfo -TaskName 'notebooklm-podcast-daily' | Select-Object LastRunTime, LastTaskResult, NextRunTime"

set EXITCODE=%ERRORLEVEL%

echo.
if %EXITCODE%==0 (
    echo [시작됨] 작업이 백그라운드에서 실행 중입니다.
    echo         진행 상황은 logs\daily-update-*.log 에서 확인하세요.
) else (
    echo [실패] 'notebooklm-podcast-daily' 작업이 등록되지 않았을 수 있습니다.
    echo        먼저 install-scheduler.bat 을 실행하세요.
)
echo.
pause
