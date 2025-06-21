from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.messages import (
    ChatMessage as LangchainChatMessage,
)

from schema import ChatMessage
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def convert_message_content_to_string(content: Any) -> str:
    """Convert message content to string."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        return "".join(convert_message_content_to_string(item) for item in content)
    elif hasattr(content, "text"):
        return content.text
    else:
        return str(content)


def langchain_to_chat_message(message: BaseMessage) -> Dict[str, Any]:
    """Convert LangChain message to chat message format."""
    content = convert_message_content_to_string(message.content)
    
    if hasattr(message, "tool_calls") and message.tool_calls:
        return {
            "type": "tool",
            "content": content,
            "tool_calls": message.tool_calls,
        }
    elif hasattr(message, "name") and message.name:
        return {
            "type": "tool",
            "content": content,
            "name": message.name,
        }
    elif hasattr(message, "type") and message.type == "ai":
        return {
            "type": "ai",
            "content": content,
        }
    else:
        return {
            "type": "human",
            "content": content,
        }


def remove_tool_calls(content: Any) -> Any:
    """Remove tool calls from message content."""
    if isinstance(content, list):
        return [item for item in content if not hasattr(item, "type") or item.type != "tool_use"]
    return content


class PerformanceMetrics:
    """性能指标收集器"""
    
    def __init__(self):
        self.metrics = {}
    
    def record_metric(self, name: str, value: float, unit: str = "ms"):
        """记录性能指标"""
        if name not in self.metrics:
            self.metrics[name] = []
        self.metrics[name].append({"value": value, "unit": unit})
    
    def get_summary(self) -> Dict[str, Any]:
        """获取性能指标摘要"""
        summary = {}
        for name, values in self.metrics.items():
            if values:
                numeric_values = [v["value"] for v in values]
                summary[name] = {
                    "count": len(values),
                    "min": min(numeric_values),
                    "max": max(numeric_values),
                    "avg": sum(numeric_values) / len(numeric_values),
                    "unit": values[0]["unit"]
                }
        return summary
    
    def reset(self):
        """重置指标"""
        self.metrics.clear()


# 全局性能指标实例
performance_metrics = PerformanceMetrics()


def log_performance_metrics(request_path: str, method: str, status_code: int, 
                          process_time: float, additional_metrics: Optional[Dict[str, Any]] = None):
    """记录性能指标到日志"""
    metrics_data = {
        "path": request_path,
        "method": method,
        "status_code": status_code,
        "process_time_ms": int(process_time * 1000),
        "process_time_s": round(process_time, 4)
    }
    
    if additional_metrics:
        metrics_data.update(additional_metrics)
    
    # 记录到性能指标收集器
    performance_metrics.record_metric("request_time", process_time * 1000, "ms")
    
    # 记录到日志
    logger.info(f"Performance: {json.dumps(metrics_data, ensure_ascii=False)}")
    
    # 如果处理时间超过阈值，记录警告
    if process_time > 5.0:  # 超过5秒
        logger.warning(f"Slow request detected: {request_path} took {process_time:.2f}s")
    elif process_time > 1.0:  # 超过1秒
        logger.info(f"Moderate request time: {request_path} took {process_time:.2f}s")
