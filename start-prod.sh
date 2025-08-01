#!/bin/bash

echo "🚀 启动生产环境..."

echo "停止并清理所有相关容器..."
docker compose -f compose-prod.yaml down

# 强制删除可能残留的容器
docker rm -f prod-agent-service prod-streamlit-app 2>/dev/null || true

# 检查并构建基础镜像
echo "检查基础镜像..."
if ! docker image inspect agent-service-base:latest >/dev/null 2>&1; then
    echo "基础镜像不存在，正在构建..."
    docker build -f docker/Dockerfile.base -t agent-service-base:latest .
    echo "✅ 基础镜像构建完成"
else
    echo "✅ 基础镜像已存在"
fi

echo "启动生产环境..."
docker compose -f compose-prod.yaml up -d --build

echo "✅ 生产环境已启动"
echo "📊 Agent Service: http://localhost:18080"
echo "🎨 Streamlit App: http://localhost:18501" 