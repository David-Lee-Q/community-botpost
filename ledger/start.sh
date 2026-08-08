#!/usr/bin/env bash
set -euo pipefail

PORT=8090
DIR="$(cd "$(dirname "$0")" && pwd)"

if ! command -v ss >/dev/null 2>&1; then
  echo "错误: 依赖 ss (iproute2) 不存在" >&2
  exit 2
fi

pid="$(ss -ltnp "sport = :$PORT" 2>&1 | grep -oP 'pid=\K[0-9]+' | head -1 || true)"
if [ -n "$pid" ]; then
  echo "服务已在运行 (port=$PORT, PID=$pid)"
  exit 0
fi

cd "$DIR"
PORT="$PORT" nohup python3 server.py >> http_server.log 2>&1 &

for i in $(seq 1 20); do
  if curl -sf "http://localhost:$PORT/" -o /dev/null; then
    echo "启动成功 (port=$PORT)"
    exit 0
  fi
  sleep 0.5
done

echo "启动失败 (port=$PORT 无法访问)" >&2
exit 2
