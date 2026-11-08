#!/usr/bin/env bash
# TickFlow 生产部署脚本
# 作用：把 Vite dev server(3011，暴露源码) 换成 nginx 托管 dist + 反代 /api 到 3018
# 用法：在服务器上执行  sudo bash deploy-nginx.sh [监听端口，默认 8080]
set -euo pipefail

# 项目根目录：优先环境变量 PROJECT_DIR，其次按脚本所在位置推断，最后回退到服务器默认路径
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${PROJECT_DIR:-$(dirname "$SCRIPT_DIR")}"
if [ ! -d "$PROJECT/frontend" ]; then
  PROJECT=/root/mycode/tickflow-stock-panel
fi
FRONTEND=$PROJECT/frontend
WEBROOT=${WEBROOT:-/var/www/tickflow}
# 后端端口跟随 .env 的 PORT，取不到再用 3018
BACKEND_PORT="${BACKEND_PORT:-$(grep -E '^PORT=' "$PROJECT/.env" 2>/dev/null | cut -d= -f2 | tr -d ' ')}"
BACKEND_PORT="${BACKEND_PORT:-3018}"
LISTEN_PORT=${1:-8080}

echo "[1/6] 检查构建工具"
cd "$FRONTEND"
if ! command -v pnpm >/dev/null 2>&1; then
  echo "  未找到 pnpm，用 npm 安装"
  npm install -g pnpm
fi

echo "[2/6] 安装依赖并构建前端"
pnpm install --frozen-lockfile || pnpm install
pnpm build
[ -f "$FRONTEND/dist/index.html" ] || { echo "构建失败：dist/index.html 不存在"; exit 1; }

echo "[3/6] 发布静态文件到 $WEBROOT"
mkdir -p "$WEBROOT"
rsync -a --delete "$FRONTEND/dist/" "$WEBROOT/"
chown -R www-data:www-data "$WEBROOT" 2>/dev/null || true
chmod -R a+rX "$WEBROOT"

echo "[4/6] 写入 nginx 配置（监听 $LISTEN_PORT）"
cat > /etc/nginx/sites-available/tickflow <<EOF
server {
    listen $LISTEN_PORT;
    server_name _;
    server_tokens off;

    root $WEBROOT;
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location /assets/ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location /api/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # SSE 长连接必需，否则实时行情会卡住
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location /health {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
    }
}
EOF

ln -sf /etc/nginx/sites-available/tickflow /etc/nginx/sites-enabled/tickflow
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "[5/6] 关闭 3011 dev server"
if ss -lptn 'sport = :3011' 2>/dev/null | grep -q ':3011'; then
  fuser -k 3011/tcp || true
  echo "  已关闭 3011"
else
  echo "  3011 未在监听，跳过"
fi

echo "[6/6] 自检"
curl -sI "http://127.0.0.1:$LISTEN_PORT/" | head -3
echo ""
echo "部署完成。浏览器访问： http://124.220.172.223:$LISTEN_PORT"
echo "记得在腾讯云控制台：放行 $LISTEN_PORT，关闭 3011 和 3018"
