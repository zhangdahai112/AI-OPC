@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ═══════════════════════════════════════════════════════════
REM  作战群控制台 — Windows 一键开发启动
REM  同时启动后端 (FastAPI) + 前端 (Vite) + 依赖检查
REM ═══════════════════════════════════════════════════════════

cd /d "%~dp0"
set ROOT=%CD%

echo [INFO] 作战群控制台 — 开发启动

REM ── 1. Python 虚拟环境 ──
echo [INFO] 检查 Python 环境…
if exist ".venv\Scripts\python.exe" (
  set PY=.venv\Scripts\python.exe
) else (
  where python >nul 2>&1
  if !ERRORLEVEL! equ 0 (
    set PY=python
    echo [WARN] 未找到 .venv，使用系统 Python
  ) else (
    echo [FAIL] 未找到 Python 安装
    pause & exit /b 1
  )
)

REM ── 2. Python 依赖 ──
echo [INFO] 检查 Python 依赖…
%PY% -c "import fastapi" 2>nul
if errorlevel 1 (
  echo [WARN] 安装 Python 依赖…
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [FAIL] Python 依赖安装失败
    pause & exit /b 1
  )
  echo [OK] Python 依赖已安装
)

REM ── 3. .env 文件 ──
echo [INFO] 检查 .env 配置…
if not exist ".env" (
  if exist ".env.example" (
    copy .env.example .env >nul
    echo [WARN] .env 已从 .env.example 创建，请编辑填入 ANTHROPIC_API_KEY
  ) else (
    echo [WARN] 未找到 .env，LLM 功能不可用
  )
)

REM ── 4. 数据目录 ──
if not exist "data\memory" mkdir data\memory
if not exist "workspaces" mkdir workspaces

REM ── 5. 前端依赖 ──
echo [INFO] 检查前端依赖…
if not exist "frontend\node_modules" (
  echo [WARN] 安装前端依赖…
  cd frontend
  call npm install
  if errorlevel 1 (
    echo [FAIL] 前端依赖安装失败
    pause & exit /b 1
  )
  cd %ROOT%
  echo [OK] 前端依赖已安装
)

REM ── 6. 启动后端 ──
echo [INFO] 启动后端 (FastAPI) → http://127.0.0.1:8777
set PYTHONIOENCODING=utf-8
set WARROOM_DATA=%ROOT%\data
start "warroom-backend" "%PY%" -m uvicorn app.main:app --app-dir "%ROOT%/backend" --host 127.0.0.1 --port 8777 --reload

REM 健康检查
echo [INFO] 等待后端就绪…
for /l %%i in (1,1,15) do (
  >nul 2>&1 curl -sf http://127.0.0.1:8777/api/health && (
    echo [OK] 后端就绪
    goto backend_ok
  )
  timeout /t 1 /nobreak >nul
)
echo [WARN] 后端健康检查超时，请确认后端已启动

:backend_ok

REM ── 7. 启动前端 ──
echo [INFO] 启动前端 (Vite) → http://localhost:5173
cd frontend
start "warroom-frontend" cmd /c "npx vite --host 127.0.0.1"
cd %ROOT%

echo.
echo ═══════════════════════════════════════════════════════
echo   作战群控制台 已启动！
echo   前端:  http://localhost:5173
echo   后端:  http://127.0.0.1:8777
echo   API:   http://127.0.0.1:8777/docs
echo   关闭本窗口 = 停止所有服务
echo ═══════════════════════════════════════════════════════
echo.
echo 按任意键停止所有服务...
pause >nul

REM ── 清理 ──
echo [INFO] 正在停止服务…
taskkill /f /fi "WINDOWTITLE eq warroom-backend*" 2>nul
taskkill /f /fi "WINDOWTITLE eq warroom-frontend*" 2>nul
echo [OK] 服务已停止
