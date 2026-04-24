@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

echo.
echo ============================================
echo  NotebookLM podcast admin
echo  URL: http://127.0.0.1:8080  (opens automatically)
echo  Press Ctrl+C to stop the server.
echo ============================================
echo.

"%PYTHON%" src\admin.py

echo.
echo ============================================
echo  Server stopped. Check the log above for errors.
echo ============================================
pause
