"""
用户信息工具模块
提供与用户信息相关的API请求功能
支持多实例，每个实例绑定一个user_token
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass
from datetime import datetime

import aiohttp
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


@dataclass
class UserInfoConfig:
    """用户信息配置"""
    api_base: str = "https://agentsben.com/api"
    bot_token: str = "anodguqSD2#$45680!g#$%^$Yfsdghhrth,.ghDFGeryGhyulFHGJdfgrwadfgdfDFGdfger#$%56dfWEyhj9*h"
    timeout: int = 120
    max_retries: int = 3

class UserInfoResponse(BaseModel):
    """用户信息响应模型"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


class UserOrdersResponse(BaseModel):
    """用户订单响应模型"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


class UserParcelsResponse(BaseModel):
    """用户包裹响应模型"""
    success: bool
    data: Optional[List[Any]] = None
    message: Optional[str] = None


class UserInfoClient:
    """用户信息客户端 - 支持多实例，每个实例绑定一个user_token"""
    
    def __init__(
        self, 
        user_token: str,
    ):
        """
        初始化用户信息客户端
        
        Args:
            user_token: 用户token，绑定到此实例
        """
        self.user_token = user_token
        self.config = UserInfoConfig()  # 使用默认配置
        self.session: Optional[aiohttp.ClientSession] = None
        self._is_closed = False
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self._ensure_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()
    
    async def _ensure_session(self):
        """确保session已创建"""
        if self.session is None and not self._is_closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                headers={
                    "Content-Type": "application/json"
                }
            )
    
    async def close(self):
        """关闭客户端连接"""
        if self.session and not self._is_closed:
            await self.session.close()
            self._is_closed = True
    
    async def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        基础请求函数
        
        Args:
            method: HTTP方法
            endpoint: API端点
            data: 请求数据
            
        Returns:
            响应数据字典
            
        Raises:
            Exception: 请求失败异常
        """
        await self._ensure_session()
        
        if self._is_closed:
            raise RuntimeError("Client is closed")
        
        url = f"{self.config.api_base}{endpoint}"
        
        # 构建请求头，将userToken放在Authorization中
        headers = {
            "Authorization": f"Bearer {self.user_token}",
            "Content-Type": "application/json"
        }
        
        # 请求数据不需要包含userToken
        request_data = data or {}
        
        try:
            async with self.session.request(
                method=method,
                url=url,
                headers=headers,
                json=request_data
            ) as response:
                response.raise_for_status()
                result = await response.json()
                logger.info(f"Request result: {result}")
                return result
                
        except Exception as e:
            logger.error(f"Unexpected error in request: {e}", exc_info=True)
            raise
    
    async def get_user_info(self):
        """
        获取当前用户的基本信息
        
        Returns:
            用户基本信息
        """
        result = await self._make_request("POST", "/bot/user/info")
        return result
    
    async def get_user_orders(
        self, 
        status_alias: Optional[str] = None
    ) -> List[Any]:
        """
        获取当前用户的订单信息
        
        Args:
            status_alias: 订单状态别名，可选值：
                - 'WaitingForPayment': 等待付款
                - 'ShippingToWarehouse': 发往仓库
                - 'InWarehouse': 在仓库中
                - 'ShippingToMyAddress': 发往我的地址
                - 'Refunding': 退款中
                - 'Refunded': 已退款
                - 'Progressing': 处理中（包含多个状态）
                
        Returns:
            订单列表
        """
        data = {}
        if status_alias:
            data["status_alias"] = status_alias
            
        result = await self._make_request("POST", "/bot/user/orders", data)
        return result
    
    async def get_user_parcels(self) -> List[Any]:
        """
        获取当前用户的包裹信息
        
        Returns:
            包裹列表（默认最近10个）
        """
        return await self._make_request("POST", "/bot/user/parcels")
    
    async def get_user_summary(self) -> Dict[str, Any]:
        """
        获取用户信息摘要（包含基本信息、订单统计、包裹统计）
        
        Returns:
            用户信息摘要
        """
        try:
            # 并行获取所有信息
            user_info_task = self.get_user_info()
            orders_task = self.get_user_orders()
            parcels_task = self.get_user_parcels()
            
            user_info, orders, parcels = await asyncio.gather(
                user_info_task, orders_task, parcels_task
            )
            
            # 按状态统计订单
            order_stats = {}
            for order in orders:
                status = order.status_alias
                order_stats[status] = order_stats.get(status, 0) + 1
            
            return {
                "user_info": user_info,
                "order_stats": order_stats,
                "total_orders": len(orders),
                "total_parcels": len(parcels),
                "orders": orders,
                "parcels": parcels
            }
            
        except Exception as e:
            logger.error(f"Failed to get user summary: {e}")
            raise


class UserInfoManager:
    """用户信息管理器 - 管理多个用户客户端实例"""
    
    def __init__(self):
        """
        初始化用户信息管理器
        """
        self.config = UserInfoConfig()  # 使用默认配置
        self._clients: Dict[str, UserInfoClient] = {}
    
    def get_client(self, user_token: str) -> UserInfoClient:
        """
        获取或创建用户客户端实例
        
        Args:
            user_token: 用户token
            
        Returns:
            用户客户端实例
        """
        if user_token not in self._clients:
            self._clients[user_token] = UserInfoClient(user_token)
        
        return self._clients[user_token]
    
    async def close_all(self):
        """关闭所有客户端连接"""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close_all()


# 便捷函数 - 使用单例模式
_manager: Optional[UserInfoManager] = None


def get_manager() -> UserInfoManager:
    """
    获取全局用户信息管理器实例
    
    Returns:
        用户信息管理器实例
    """
    global _manager
    if _manager is None:
        _manager = UserInfoManager()
    return _manager


async def get_user_info(user_token: str):
    """
    便捷函数：获取用户基本信息
    
    Args:
        user_token: 用户token
        
    Returns:
        用户基本信息
    """
    manager = get_manager()
    client = manager.get_client(user_token)
    async with client:
        return await client.get_user_info()


async def get_user_orders(
    user_token: str, 
    status_alias: Optional[str] = None
) -> List[Any]:
    """
    便捷函数：获取用户订单信息
    
    Args:
        user_token: 用户token
        status_alias: 订单状态别名
        
    Returns:
        订单列表
    """
    manager = get_manager()
    client = manager.get_client(user_token)
    async with client:
        return await client.get_user_orders(status_alias)


async def get_user_parcels(
    user_token: str
) -> List[Any]:
    """
    便捷函数：获取用户包裹信息
    
    Args:
        user_token: 用户token
        
    Returns:
        包裹列表
    """
    manager = get_manager()
    client = manager.get_client(user_token)
    async with client:
        return await client.get_user_parcels()


# 使用示例
async def example_usage():
    """使用示例"""
    
    # 方式1：使用管理器管理多个用户
    async with UserInfoManager() as manager:
        # 用户1
        client1 = manager.get_client("user_token_1")
        async with client1:
            user_info1 = await client1.get_user_info()
            orders1 = await client1.get_user_orders("Progressing")
            print(f"用户1 - 邮箱: {user_info1.email}, 处理中订单: {len(orders1)}")
        
        # 用户2
        client2 = manager.get_client("user_token_2")
        async with client2:
            summary2 = await client2.get_user_summary()
            print(f"用户2 - 总订单: {summary2['total_orders']}, 总包裹: {summary2['total_parcels']}")
    
    # 方式2：使用便捷函数
    try:
        user_info = await get_user_info("user_token_3")
        print(f"用户3 - VIP等级: {user_info.vip_level}")
        
        orders = await get_user_orders("user_token_3", "InWarehouse")
        print(f"用户3 - 在仓库订单: {len(orders)}")
        
    except Exception as e:
        print(f"错误: {e}")


async def example_multiple_users():
    """多用户并发处理示例"""
    user_tokens = ["token1", "token2", "token3", "token4", "token5"]
    
    async with UserInfoManager() as manager:
        # 并发获取多个用户的信息
        tasks = []
        for token in user_tokens:
            client = manager.get_client(token)
            task = asyncio.create_task(client.get_user_info())
            tasks.append((token, task))
        
        # 等待所有任务完成
        results = {}
        for token, task in tasks:
            try:
                user_info = await task
                results[token] = user_info
                print(f"用户 {token}: {user_info.email} - VIP{user_info.vip_level}")
            except Exception as e:
                print(f"用户 {token} 获取失败: {e}")
