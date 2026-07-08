#!/usr/bin/env bash
# 启动作战群控制台。首次运行前请确保已安装依赖（见 README）。
set -e
cd "$(dirname "$0")"

if [ -f ".venv/Scripts/python.exe" ]; then
  PY=".venv/Scripts/python.exe"     # Windows
elif [ -f ".venv/bin/python" ]; then
  PY=".venv/bin/python"             # macOS / Linux
else
  PY="python"
fi

export PYTHONIOENCODING=utf-8
echo "→ http://127.0.0.1:8777"
exec "$PY" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8777
