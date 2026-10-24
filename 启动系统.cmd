@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  python -m venv .venv
  if errorlevel 1 goto failure
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failure
echo.
echo 系统即将启动，请在浏览器访问 http://127.0.0.1:8000
echo 关闭此窗口即可停止服务。
.venv\Scripts\python.exe run.py
goto end
:failure
echo 启动失败，请检查 Python 3.12+ 和网络连接。
:end
pause
