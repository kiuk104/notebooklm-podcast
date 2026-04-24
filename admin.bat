@echo off
chcp 65001 >nul 2>&1
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

echo.
echo ============================================
echo  NotebookLM 팟캐스트 관리자 페이지
echo  http://127.0.0.1:8080  (자동으로 열림)
echo  창을 닫으려면 Ctrl+C
echo ============================================
echo.

%PYTHON% src\admin.py
