#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  作战群控制台 — 一键开发启动
#  同时启动后端 (FastAPI) + 前端 (Vite) + 依赖检查
# ═══════════════════════════════════════════════════════════
set -e
cd "$(dirname "$0")"
ROOT="$PWD"

# ── 颜色 ──
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $1"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $1"; exit 1; }

cleanup() {
  echo ""
  info "正在停止服务…"
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null && ok "后端已停止"
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null && ok "前端已停止"
  exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# ── 1. Python 虚拟环境检查 ──
info "检查 Python 环境…"
if [ -f ".venv/Scripts/python.exe" ]; then
  PY=".venv/Scripts/python.exe"
elif [ -f ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python &>/dev/null; then
  PY="python"
  warn "未找到 .venv，使用系统 Python"
else
  fail "未找到 Python 安装"
fi

# ── 2. 依赖安装（如果缺失） ──
info "检查 Python 依赖…"
"$PY" -c "import fastapi" 2>/dev/null || {
  warn "安装 Python 依赖…"
  "$PY" -m pip install -r requirements.txt
  ok "Python 依赖已安装"
}

# ── 3. .env 文件检查 ──
info "检查 .env 配置…"
if [ ! -f ".env" ]; then
  if [ -f ".env.example" ]; then
    cp .env.example .env
    warn ".env 不存在，已从 .env.example 创建，请编辑填入 ANTHROPIC_API_KEY"
  else
    warn "未找到 .env 和 .env.example，LLM 功能将不可用"
  fi
fi

# ── 4. 缓存 / 数据目录 ──
mkdir -p data/memory workspaces

# ── 5. 前端依赖检查 ──
info "检查前端依赖…"
if [ ! -d "frontend/node_modules" ]; then
  warn "安装前端依赖…"
  cd frontend && npm install && cd "$ROOT"
  ok "前端依赖已安装"
fi

# ── 6. 启动后端 ──
info "启动后端 (FastAPI) → http://127.0.0.1:8777"
export PYTHONIOENCODING=utf-8
export WARROOM_DATA="$ROOT/data"
"$PY" -m uvicorn app.main:app --app-dir "$ROOT/backend" \
  --host 127.0.0.1 --port 8777 --reload &
BACKEND_PID=$!
sleep 2  # 等后端起来

# 健康检查
for i in $(seq 1 10); do
  if curl -sf http://127.0.0.1:8777/api/health >/dev/null 2>&1; then
    ok "后端就绪"
    break
  fi
  if [ $i -eq 10 ]; then fail "后端启动超时"; fi
  sleep 1
done

# ── 7. 启动前端 ──
info "启动前端 (Vite) → http://localhost:5173"
cd frontend
npx vite --host 127.0.0.1 &
FRONTEND_PID=$!
cd "$ROOT"

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  作战群控制台 已启动！${NC}"
echo -e "${GREEN}  前端:  http://localhost:5173${NC}"
echo -e "${GREEN}  后端:  http://127.0.0.1:8777${NC}"
echo -e "${GREEN}  API:   http://127.0.0.1:8777/docs${NC}"
echo -e "${GREEN}  按 Ctrl+C 停止所有服务${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""

# 保持前台，等待子进程
wait
