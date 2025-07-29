#!/bin/bash

echo "🚀 启动生产环境..."

echo "停止并清理所有相关容器..."
docker compose -f compose-prod.yaml down

# 强制删除可能残留的容器
docker rm -f prod-agent-service prod-streamlit-app 2>/dev/null || true

echo "启动生产环境..."
docker compose -f compose-prod.yaml up -d

echo "✅ 生产环境已启动"
echo "📊 Agent Service: http://localhost:18080"
echo "🎨 Streamlit App: http://localhost:18501" 