#!/bin/bash

# 显示帮助信息
show_help() {
    echo "Usage: $0 [OPTIONS] PACKAGE..."
    echo
    echo "Options:"
    echo "  -h, --help          显示帮助信息"
    echo "  -d, --dev           安装为开发依赖"
    echo "  -v, --version       指定包版本 (格式: package==version)"
    echo
    echo "Examples:"
    echo "  $0 fastapi pydantic         # 安装生产依赖"
    echo "  $0 -d pytest pytest-cov     # 安装开发依赖"
    echo "  $0 -v fastapi==0.100.0     # 安装指定版本"
}

# 参数解析
DEV_FLAG=""
PACKAGES=()

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -d|--dev)
            DEV_FLAG="--dev"
            shift
            ;;
        *)
            PACKAGES+=("$1")
            shift
            ;;
    esac
done

# 检查是否提供了包名
if [ ${#PACKAGES[@]} -eq 0 ]; then
    echo "错误: 请指定至少一个包名"
    show_help
    exit 1
fi

# 执行安装命令
echo "正在安装以下包: ${PACKAGES[*]}"
if [ -n "$DEV_FLAG" ]; then
    echo "作为开发依赖安装"
fi

# 使用docker运行uv命令
docker run -it --rm \
    -v "$(pwd)":/app \
    python-uv \
    uv add $DEV_FLAG "${PACKAGES[@]}"

# 检查命令执行状态
if [ $? -eq 0 ]; then
    echo "✅ 安装成功"
    
    # 重新构建基础镜像
    echo "正在重新构建基础镜像..."
    if ./docker/build.sh; then
        echo "✅ 基础镜像重建成功"
    else
        echo "❌ 基础镜像重建失败"
        exit 1
    fi
else
    echo "❌ 安装失败"
    exit 1
fi