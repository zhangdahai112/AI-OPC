#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  Codespace 自动初始化脚本
#  在容器创建后自动运行，配置环境、安装依赖
# ═══════════════════════════════════════════════════════════
set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $1"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }

# 回到项目根目录
cd "$(dirname "$0")/.."
ROOT="$PWD"

info "🚀 开始初始化 AI-OPC Codespace 环境..."
echo ""

# ── 1. 检查 Python 环境 ──
info "检查 Python 版本..."
python3 --version
ok "Python 就绪"

# ── 2. 创建虚拟环境 ──
if [ ! -d ".venv" ]; then
  info "创建 Python 虚拟环境..."
  python3 -m venv .venv
  ok "虚拟环境已创建"
else
  ok "虚拟环境已存在"
fi

# ── 3. 激活虚拟环境并安装依赖 ──
info "安装 Python 依赖..."
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
ok "Python 依赖已安装"

# ── 4. 创建 .env 文件（如果不存在）──
if [ ! -f ".env" ]; then
  info "创建 .env 配置文件..."
  cp .env.example .env
  warn "⚠️  请编辑 .env 文件并填入 ANTHROPIC_API_KEY"
  ok ".env 文件已创建"
else
  ok ".env 文件已存在"
fi

# ── 5. 创建必要的目录 ──
info "创建数据目录..."
mkdir -p data/memory workspaces
ok "数据目录已创建"

# ── 6. 检查前端依赖 ──
if [ -d "frontend" ]; then
  info "安装前端依赖..."
  cd frontend
  
  # 检查 npm/node
  if ! command -v npm &> /dev/null; then
    warn "npm 未安装，跳过前端依赖安装"
  else
    if [ ! -d "node_modules" ]; then
      npm install
      ok "前端依赖已安装"
    else
      ok "前端依赖已存在"
    fi
  fi
  
  cd "$ROOT"
fi

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ Codespace 初始化完成！${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""
echo "📝 接下来的步骤："
echo "  1. 编辑 .env 文件并填入 ANTHROPIC_API_KEY："
echo "     ${CYAN}code .env${NC}"
echo ""
echo "  2. 启动应用："
echo "     ${CYAN}bash dev.sh${NC}"
echo ""
echo "  3. 访问应用："
echo "     - 前端: http://localhost:5173"
echo "     - 后端: http://127.0.0.1:8777"
echo "     - API 文档: http://127.0.0.1:8777/docs"
echo ""
echo -e "${YELLOW}💡 提示：${NC}所有依赖已自动安装，可以直接运行 bash dev.sh"
echo ""
