#!/usr/bin/env bash
set -euo pipefail

PORT=8090

if ! command -v ss >/dev/null 2>&1; then
  echo "错误: 依赖 ss (iproute2) 不存在" >&2
  exit 2
fi

pid="$(ss -ltnp "sport = :$PORT" 2>&1 | grep -oP 'pid=\K[0-9]+' | head -1 || true)"
if [ -z "$pid" ]; then
  echo "服务未运行 (port=$PORT)"
  exit 0
fi

kill "$pid"

for i in $(seq 1 20); do
  if ! ss -ltn "sport = :$PORT" 2>&1 | grep -q ":$PORT\b"; then
    echo "已停止 (PID=$pid, port=$PORT)"
    exit 0
  fi
  sleep 0.5
done

echo "停止超时 (port=$PORT 仍被占用)" >&2
exit 2
