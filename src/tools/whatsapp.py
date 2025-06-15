"""
WhatsApp Messages Handler

处理WhatsApp消息的核心模块：
1. 检查并获取未读消息
2. 调用chatbot agent处理消息
3. 发送AI回复给用户
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph.state import CompiledStateGraph

from agents import get_agent
from core import settings
from service.browser_service import get_whatsapp_client
from client.whatsapp_client import WhatsAppBrowserClient

# Configure logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


class WhatsAppTool:
    """WhatsApp消息处理器"""
    
    def __init__(
        self, 
    ):
        """
        初始化消息处理器
        
        Args:
            whatsapp_client: WhatsApp客户端实例
            agent_id: 使用的代理ID，默认为"chatbot"
            max_reply_length: 最大回复长度，默认300字符
            enable_auto_reply: 是否启用自动回复
        """
        self.max_reply_length = 300
        self.enable_auto_reply = True
        
        # 创建异步锁，确保WhatsApp操作串行执行
        self._lock = asyncio.Lock()
        
        # 获取chatbot代理
        try:
            self.agent: CompiledStateGraph = get_agent('chatbot')
        except Exception as e:
            logger.error(f"Failed to load agent chatbot: {e}")
            raise
        
        
    @staticmethod
    def normalize_contact_name(contact_name: str) -> str:
        """
        标准化联系人名称，移除加号
        
        Args:
            contact_name: 原始联系人名称
            
        Returns:
            处理后的联系人名称
        """
        if not contact_name:
            return contact_name
        return contact_name.replace("+", "")
    
    async def get_unread_messages(self) -> List[Dict[str, Any]]:
        """
        获取未读消息
        """
        async with self._lock:
            client = await get_whatsapp_client()
            return await client.get_unread_messages()
    

    async def get_contact_chat_list(self, contact_name: str) -> List[Dict[str, Any]]:
        """
        获取指定联系人的聊天列表
        """
        async with self._lock:
            client = await get_whatsapp_client()
            normalized_contact_name = self.normalize_contact_name(contact_name)
            return await client.get_contact_chat_list(normalized_contact_name)
    
    async def send_message_to_contact(self, contact_name: str, message: str) -> Dict[str, Any]:
        """
        发送消息到指定联系人
        """
        async with self._lock:
            client = await get_whatsapp_client()
            normalized_contact_name = self.normalize_contact_name(contact_name)
            return await client.send_message_to_contact(normalized_contact_name, message)
    
  
    async def generate_contact_reply_message(self, contact_name: str) -> Dict[str, Any]:
        """
        生成AI客服回复 (公开方法)
        
        Args:
            chat_messages: 对话消息列表
            
        Returns:
            包含AI回复的结果字典
        """
        async with self._lock:
            client = await get_whatsapp_client()
            normalized_contact_name = self.normalize_contact_name(contact_name)
            chat_list = await client.get_contact_chat_list(normalized_contact_name)
            return await self._generate_contact_reply_message(chat_list)


    async def auto_reply_contact_message(self, contact_name: str) -> Dict[str, Any]:
        """
        自动回复未读消息
        """
        async with self._lock:
            client = await get_whatsapp_client()
            normalized_contact_name = self.normalize_contact_name(contact_name)
            chat_list = await client.get_contact_chat_list(normalized_contact_name)
            
            if not chat_list:
                return {
                    "contact_name": contact_name,
                    "error": "No chat list found"
                }
            
            reply_result = await self._generate_contact_reply_message(chat_list)
            
            if not reply_result.get("success") or not reply_result.get("ai_reply_message"):
                return {
                    "contact_name": contact_name,
                    "error": reply_result.get("error", "Failed to generate AI reply")
                }
            
            reply_message = reply_result["ai_reply_message"]
            res = await client.send_message_to_contact(normalized_contact_name, reply_message)
            
            return res

    async def _generate_contact_reply_message(self, chat_messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        处理新的未读消息
        
        Returns:
            处理结果字典
        """
        result = {
            "chat_messages": chat_messages,
            "ai_reply_message": None,
            "error": None,
            "timestamp": datetime.now().isoformat(),
        }
        
        try:
            if not chat_messages:
                return result
            
            ai_reply_message = await self._generate_ai_reply(chat_messages)
            if ai_reply_message:
                result["ai_reply_message"] = ai_reply_message
                result["success"] = True
            else:
                result["success"] = False
                result["error"] = "AI代理生成回复失败"
                
        except Exception as e:
            error_msg = f"消息处理过程中发生错误: {str(e)}"
            result["error"] = error_msg
            logger.error(error_msg, exc_info=True)
        
        return result
    
    def _extract_message_content(self, message_data: Dict[str, Any]) -> Optional[str]:
        """
        从消息数据中提取文本内容
        
        Args:
            message_data: 消息数据字典
            
        Returns:
            提取的消息文本，如果无法提取则返回None
        """
        if not isinstance(message_data, dict):
            return str(message_data) if message_data else None
        
        # 尝试多种可能的字段名
        possible_fields = [
            'text', 'content', 'message', 'body', 
            'message_content', 'text_content', 'last_message'
        ]
        
        for field in possible_fields:
            if field in message_data and message_data[field]:
                content = message_data[field].strip()
                if content:
                    return content
        
        # 如果没有找到标准字段，尝试返回整个字典的字符串表示
        return str(message_data)
    
    async def _generate_ai_reply(self, chat_messages: list[Dict[str, Any]]) -> Optional[str]:
        """
        使用AI代理生成回复
        
        Args:
            chat_messages: 用户对话消息内容列表
            
        Returns:
            AI生成的回复文本，失败时返回None
        """
        try:
            logger.info("调用AI代理生成回复...")
            
            # 创建消息对象
            system_message = SystemMessage(content=f"我们的业务是国际代理采购，用户在我们平台下单，然后我们采购商品发送给他. 这是现在的聊天记录：{chat_messages}")
            human_message = HumanMessage(content=f"你是whatsapp的客服销售，请根据用户的消息内容生成合适回复，匹配用户语言，不要超过{self.max_reply_length}字符, 表现的更加像一个人类，尽量使用口语化回复")
            
            # 准备输入数据
            inputs = {
                "messages": [
                    system_message,
                    human_message
                ]
            }
            
            # 生成配置
            config = {
                "configurable": {
                    "thread_id": str(uuid.uuid4()),
                    "model": settings.DEFAULT_MODEL
                }
            }
            
            # 调用代理
            response = await self.agent.ainvoke(inputs, config=config)
            
            logger.info(f"AI代理生成回复: {response}")
            
            # 提取回复内容
            if response and "messages" in response:
                messages = response["messages"]
                if messages and len(messages) > 0:
                    last_message = messages[-1]
                    reply_content = last_message.content if hasattr(last_message, 'content') else str(last_message)
                    
                    # 限制回复长度
                    if len(reply_content) > self.max_reply_length:
                        reply_content = reply_content[:self.max_reply_length-3] + "..."
                        logger.info(f"回复内容被截断至{self.max_reply_length}字符")
                    
                    logger.info(f"AI代理生成回复成功: {reply_content[:50]}...")
                    return reply_content
            
            logger.warning("AI代理响应格式异常")
            return None
            
        except Exception as e:
            logger.error(f"AI代理生成回复失败: {str(e)}")
            return None


whatsapp_tool = WhatsAppTool()

