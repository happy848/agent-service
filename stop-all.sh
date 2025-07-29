#!/bin/bash

echo "🛑 停止所有环境..."

echo "停止开发环境..."
docker compose down

echo "停止生产环境..."
docker compose -f compose-prod.yaml down

echo "✅ 所有环境已停止" 