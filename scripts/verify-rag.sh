#!/usr/bin/env bash
set -euo pipefail

mode="${1:-unit}"
if [[ "$mode" != unit && "$mode" != integration ]] || [[ $# -gt 1 ]]; then
  echo '用法：bash scripts/verify-rag.sh [unit|integration]' >&2
  exit 2
fi
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
compose=(docker compose --project-directory "$repo_root" --project-name evalspark-rag-test
  --env-file "$repo_root/docker/rag-test.env" -f "$repo_root/docker-compose.rag-test.yml")
trap 'echo "隔离验收失败；测试服务和数据保留，不自动清理。" >&2' ERR

if ! command -v docker >/dev/null 2>&1 || ! docker info --format '{{.ServerVersion}}'; then
  echo '请在资源充足设备安装并手动启动 Docker 后再验收。' >&2
  exit 1
fi
echo "隔离验收：项目 evalspark-rag-test；数据库 mysql-test / multichateval_rag_test；模式 $mode"
"${compose[@]}" --profile unit --profile lifecycle config --quiet
if [[ "$mode" == unit ]]; then
  "${compose[@]}" --profile unit build unit-runner frontend-test
  "${compose[@]}" run --rm --no-deps unit-runner python -m pip check
  "${compose[@]}" run --rm --no-deps unit-runner
  "${compose[@]}" run --rm --no-deps frontend-test
  "${compose[@]}" run --rm --no-deps frontend-test pnpm build
else
  if ! docker volume inspect evalspark_rag_model_cache --format '{{.Name}}' >/dev/null 2>&1; then
    echo '缺少固定模型缓存卷。确认下载授权后运行 docker volume create evalspark_rag_model_cache，再重新验收。禁止删除或替换已有卷。' >&2
    exit 1
  fi
  "${compose[@]}" build runner
  "${compose[@]}" --profile lifecycle stop worker-test
  "${compose[@]}" up -d --wait --wait-timeout 180 mysql-test
  # 先迁移测试库；不能在旧 schema 上启动 Worker 恢复循环。
  "${compose[@]}" run --rm --no-deps runner
  "${compose[@]}" --profile lifecycle up -d --wait --wait-timeout 1800 embedding-test qdrant-test redis-test model-test
  "${compose[@]}" --profile lifecycle up -d worker-test
  "${compose[@]}" run --rm --no-deps acceptance-runner
fi
echo '当前档自动检查已结束。浏览器、真实付费模型、业务升级及性能验收仍需单独记录；测试服务与数据保留。'
