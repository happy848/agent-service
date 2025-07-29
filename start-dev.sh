#!/bin/bash

echo "🚀 启动开发环境..."
echo "停止相关容器..."
docker compose -f compose.yaml down

# 强制删除可能残留的容器
docker rm -f agent-service-dev-agent agent-service-dev-streamlit 2>/dev/null || true

echo "启动开发环境..."
docker compose up -d

echo "✅ 开发环境已启动"
echo "📊 Agent Service: http://localhost:8080"
echo "🎨 Streamlit App: http://localhost:8501" 