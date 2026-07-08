@echo off
REM 启动作战群控制台（Windows）。首次请先：python -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.txt
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo  -^> http://127.0.0.1:8777
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8777
