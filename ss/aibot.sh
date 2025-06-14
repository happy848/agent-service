#!/bin/bash

# Docker 启动 AI Agent Service 脚本
# 简化版本，专门用于Docker部署

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 项目根目录
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查Docker
check_docker() {
    if ! command -v docker &> /dev/null; then
        log_error "Docker 未安装"
        exit 1
    fi
    
    if ! docker compose version &> /dev/null; then
        log_error "需要 Docker Compose v2.23.0 或更高版本"
        exit 1
    fi
}

# 检查和创建.env文件
setup_env() {
    if [[ ! -f ".env" ]]; then
        log_warning ".env 文件不存在"
        if [[ -f ".env.example" ]]; then
            log_info "复制 .env.example 到 .env"
            cp .env.example .env
            log_warning "请编辑 .env 文件并添加必要的API密钥（如OPENAI_API_KEY）"
            echo
            echo "主要需要配置的环境变量："
            echo "- OPENAI_API_KEY=your_openai_api_key"
            echo "- GROQ_API_KEY=your_groq_api_key (可选，用于内容审核)"
            echo "- LANGSMITH_API_KEY=your_langsmith_key (可选，用于追踪)"
            echo
            read -p "是否现在编辑 .env 文件? (y/n): " edit_env
            if [[ "$edit_env" == "y" || "$edit_env" == "Y" ]]; then
                ${EDITOR:-nano} .env
            fi
        else
            log_error "请创建 .env 文件"
            cat << EOF > .env
# 至少需要一个LLM API密钥
OPENAI_API_KEY=your_openai_api_key_here

# 可选配置
# GROQ_API_KEY=your_groq_api_key
# LANGSMITH_API_KEY=your_langsmith_key
# LANGSMITH_TRACING=true
EOF
            log_info "已创建基础 .env 文件，请编辑并添加你的API密钥"
            exit 1
        fi
    fi
}

# 启动服务
start_services() {
    log_info "启动 AI Agent Service (Docker)..."
    
    # 构建并启动服务
    log_info "构建并启动Docker容器..."
    docker compose up --build -d
    
    # 等待服务启动
    log_info "等待服务启动..."
    sleep 10
    
    # 检查服务状态
    if docker compose ps | grep -q "Up"; then
        log_success "服务启动成功！"
        echo
        echo "🚀 AI Agent Service 已启动"
        echo "📊 Streamlit应用: http://localhost:8501"
        echo "🔧 FastAPI文档: http://localhost:8080/redoc"
        echo "📈 API信息: http://localhost:8080/info"
        echo
        echo "查看日志: docker compose logs -f"
        echo "停止服务: docker compose down"
    else
        log_error "服务启动失败"
        docker compose logs
        exit 1
    fi
}

# 开发模式启动（支持文件监控）
start_dev() {
    log_info "启动开发模式 (Docker Watch)..."
    
    log_info "使用 docker compose watch 启动服务..."
    log_info "文件变更将自动重新加载服务"
    docker compose watch
}

# 停止服务
stop_services() {
    log_info "停止 AI Agent Service..."
    docker compose down
    log_success "服务已停止"
}

# 查看状态
show_status() {
    echo "=== Docker 容器状态 ==="
    docker compose ps
    echo
    echo "=== 服务健康检查 ==="
    if curl -s http://localhost:8080/info > /dev/null 2>&1; then
        echo "✅ FastAPI服务: 正常"
    else
        echo "❌ FastAPI服务: 异常"
    fi
    
    if curl -s http://localhost:8501 > /dev/null 2>&1; then
        echo "✅ Streamlit应用: 正常"
    else
        echo "❌ Streamlit应用: 异常"
    fi
}

# 查看日志
show_logs() {
    echo "=== 服务日志 (按 Ctrl+C 退出) ==="
    docker compose logs -f
}

# 重启服务
restart_services() {
    log_info "重启服务..."
    docker compose restart
    log_success "服务已重启"
}

# 完全重建
rebuild() {
    log_info "完全重建服务..."
    docker compose down
    docker compose build --no-cache
    docker compose up -d
    log_success "服务重建完成"
}

# 测试服务接口
test_service() {
    log_info "测试服务接口..."
    
    # 检查服务是否运行
    if ! docker compose ps | grep -q "Up"; then
        log_error "服务未运行，请先启动服务: $0 start"
        exit 1
    fi
    
    # 测试 /test 接口
    log_info "请求 http://localhost:8080/test 接口..."
    
    if response=$(curl -s -w "\n%{http_code}" http://localhost:8080/test 2>/dev/null); then
        http_code=${response##*$'\n'}
        response_body=${response%$'\n'*}
        
        if [[ "$http_code" == "200" ]]; then
            log_success "测试接口响应成功 (HTTP $http_code)"
            echo "响应内容:"
            echo "$response_body"
        else
            log_warning "测试接口响应异常 (HTTP $http_code)"
            echo "响应内容:"
            echo "$response_body"
        fi
    else
        log_error "无法连接到测试接口"
        log_info "请确认服务是否正常运行: $0 status"
        exit 1
    fi
}

# 显示帮助
show_help() {
    cat << EOF
Docker AI Agent Service 启动脚本

用法: $0 <command>

命令:
  start           启动服务（生产模式）
  dev             启动开发模式（支持文件监控）
  stop            停止服务
  restart         重启服务
  status          查看服务状态
  logs            查看服务日志
  rebuild         完全重建服务
  test            测试服务接口（请求 8080/test）
  help            显示帮助信息

快速开始:
  1. $0 start     # 首次启动
  2. 访问 http://localhost:8501

开发模式:
  $0 dev          # 支持文件变更自动重载

服务地址:
  - Streamlit应用: http://localhost:8501
  - FastAPI文档: http://localhost:8080/redoc
  - API信息: http://localhost:8080/info

注意事项:
  - 确保已安装 Docker 和 Docker Compose v2.23.0+
  - 需要配置 .env 文件中的API密钥
  - 首次启动可能需要较长时间下载镜像
EOF
}

# 主函数
main() {
    # 检查Docker
    check_docker
    
    case "${1:-help}" in
        start)
            setup_env
            start_services
            ;;
        dev|watch)
            setup_env
            start_dev
            ;;
        stop)
            stop_services
            ;;
        restart)
            restart_services
            ;;
        status)
            show_status
            ;;
        logs)
            show_logs
            ;;
        rebuild)
            setup_env
            rebuild
            ;;
        test)
            test_service
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "未知命令: $1"
            echo
            show_help
            exit 1
            ;;
    esac
}

# 运行主函数
main "$@" 