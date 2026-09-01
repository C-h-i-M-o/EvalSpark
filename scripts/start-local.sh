#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

log() {
  printf "\033[1;34m[EvalSpark Docker]\033[0m %s\n" "$1"
}

fail() {
  printf "\033[1;31m[EvalSpark Docker]\033[0m %s\n" "$1" >&2
  exit 1
}

if [[ ! -f "${ENV_FILE}" ]]; then
  fail "未找到环境变量文件：${ENV_FILE}。请先复制 .env.example 为 .env 并完成配置。"
fi

if grep -Eq '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=.*CHANGE_ME' "${ENV_FILE}"; then
  fail ".env 中仍存在 CHANGE_ME 占位值，请完成配置后再启动。"
fi

if ! command -v docker >/dev/null 2>&1; then
  fail "未找到 Docker。请先安装并启动 Docker Desktop。"
fi

if ! docker info >/dev/null 2>&1; then
  fail "Docker daemon 不可用，请确认 Docker Desktop 已启动。"
fi

if ! docker compose version >/dev/null 2>&1; then
  fail "当前 Docker 未提供 Compose 插件，请安装或升级 Docker Desktop。"
fi

cd "${PROJECT_ROOT}"

log "校验 Docker Compose 配置..."
docker compose config --quiet

log "构建并启动 MySQL、迁移、后端和 React 前端..."
log "默认前端：http://127.0.0.1:5174；默认后端：http://127.0.0.1:8000；如 .env 覆盖端口请以 Compose 映射为准。"
log "按 Ctrl+C 停止本次开发环境；普通停止不会删除数据库卷。"
docker compose up --build
