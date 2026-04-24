@echo off
setlocal

cd /d "%~dp0"

echo.
echo ============================================
echo  NotebookLM podcast - Task Scheduler register
echo  Registers 'notebooklm-podcast-daily' at 09:00
echo ============================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\register-scheduled-task.ps1"

set EXITCODE=%ERRORLEVEL%

echo.
if %EXITCODE%==0 (
    echo [OK] Registered. You can close this window.
    echo      To trigger immediately, run run-now.bat
) else (
    echo [FAIL] exit code: %EXITCODE%
    echo        Check PowerShell execution policy and file paths.
)
echo.
pause
