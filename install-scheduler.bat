@echo off
chcp 65001 >nul 2>&1
setlocal

cd /d "%~dp0"

echo.
echo ============================================
echo  NotebookLM 팟캐스트 - 작업 스케줄러 등록
echo  매일 09:00 에 자동 업데이트 실행
echo ============================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\register-scheduled-task.ps1"

set EXITCODE=%ERRORLEVEL%

echo.
if %EXITCODE%==0 (
    echo [완료] 등록되었습니다. 이 창은 닫으셔도 됩니다.
    echo        즉시 테스트하려면 run-now.bat 을 실행하세요.
) else (
    echo [실패] 종료 코드: %EXITCODE%
    echo        PowerShell 실행 정책이나 파일 경로를 확인하세요.
)
echo.
pause
