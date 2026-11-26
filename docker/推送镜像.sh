#!/usr/bin/env bash
set -euo pipefail
REPO="${1:-}"
[ -n "$REPO" ] || { echo "用法: bash 推送镜像.sh <仓库地址>   例: registry.cn-hangzhou.aliyuncs.com/pictf/webui"; exit 1; }
REG="$(echo "$REPO" | cut -d/ -f1)"
docker login "$REG"
docker tag pictf-webui:0.1.1 "$REPO":0.1.1
docker push "$REPO":0.1.1
echo "[√] 完成。朋友那边： docker pull $REPO:0.1.1"
