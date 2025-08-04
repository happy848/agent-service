import asyncio
import logging
from typing import Dict, Any, Optional, List
from uuid import uuid4

import aiohttp
from pydantic import BaseModel

from client.models import MessageItem

# Configure logger
logger = logging.getLogger(__name__)

# API配置
generate_chat_response_url = "https://agentsben.com/api/agent/chatbot-web/chat"


class ChatResponse(BaseModel):
    """聊天响应模型"""
    message: str
    data: Optional[Dict[str, Any]] = None

async def handle_customer_message(messages_dict: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    使用客户服务智能体处理客户消息。
    
    Args:
        messages: 包含消息信息的MessageItem dict对象
        
    Returns:
        包含响应信息的字典:
        {
            "success": bool,
            "ai_reply_message": str,
            "error": Optional[str]
        }
    """
    try:
        request_data = {
            "sign": "sadfsaSDGF245346HYJm&^*hjkOHMHJLHJmh456kghkTYUTY",
            "messages": messages_dict,
        }
        
        # 设置请求头
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Requested-With": "XMLHttpRequest"
        }
        
        # 发送请求
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                generate_chat_response_url,
                json=request_data,
                headers=headers
            ) as response:
                logger.info(f"API响应状态: {response.status}")
                
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"API请求失败: {response.status} - {error_text}")
                    return {
                        "success": False,
                        "ai_reply_message": "",
                        "error": f"API请求失败: {response.status}"
                    }
                
                # 解析响应
                response_data = await response.json()
                logger.info(f"API响应数据: {response_data}")
                
                # 验证响应格式
                try:
                    chat_response = ChatResponse(**response_data)
                except Exception as e:
                    logger.error(f"响应数据格式错误: {e}")
                    return {
                        "success": False,
                        "ai_reply_message": "",
                        "error": f"响应格式错误: {e}"
                    }
                
                # 检查响应状态
                if chat_response.message != "success":
                    logger.error(f"API返回错误: {chat_response.message}")
                    return {
                        "success": False,
                        "ai_reply_message": "",
                        "error": chat_response.message
                    }
                
                # 提取AI回复
                if chat_response.data and "responseText" in chat_response.data:
                    ai_reply_message = chat_response.data["responseText"]
                    return {
                        "success": True,
                        "ai_reply_message": ai_reply_message,
                        "error": None
                    }
                else:
                    logger.error("响应数据中缺少responseText字段")
                    return {
                        "success": False,
                        "ai_reply_message": "",
                        "error": "响应数据格式不完整"
                    }
                    
    except aiohttp.ClientError as e:
        logger.error(f"网络请求错误: {e}")
        return {
            "success": False,
            "ai_reply_message": "",
            "thread_id": str(uuid4()),
            "error": f"网络请求错误: {e}"
        }
    except Exception as e:
        logger.error(f"处理客户消息时发生未知错误: {e}")
        return {
            "success": False,
            "ai_reply_message": "",
            "thread_id": str(uuid4()),
            "error": f"未知错误: {e}"
        }


