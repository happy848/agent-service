"""
用户信息工具模块
提供与用户信息相关的API请求功能
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

import aiohttp

@dataclass
class UserInfoConfig:
    """用户信息配置"""
    api_base: str = "https://agentsben.com/api"
    auth_secret: str = "anodguqSD2#$45680!g#$%^$Yfsdghhrth,.ghDFGeryGhyulFHGJdfgrwadfgdfDFGdfger#$%56dfWEyhj9*h"
    timeout: int = 120
    max_retries: int = 3

# 参考curl命令（正确格式）:
# curl 'https://agentsben.com/api/bot/user/info' \
#   -H 'Accept: application/json, text/plain, */*' \
#   -H 'Accept-Language: zh-CN,zh;q=0.9,en;q=0.8' \
#   -H 'Authorization: Bearer anodguqSD2#$45680!g#$%^$Yfsdghhrth,.ghDFGeryGhyulFHGJdfgrwadfgdfDFGdfger#$%56dfWEyhj9*h' \
#   -H 'Content-Type: application/json' \
#   -b 'x-hng=lang=zh-CN&domain=agentsben.com; _ga=GA1.1.1447635580.1746716198; _ga_J2FQXJQXWZ=GS2.1.s1747277037$o9$g1$t1747277972$j0$l0$h0' \
#   -H 'Origin: https://agentsben.com' \
#   -H 'Proxy-Connection: keep-alive' \
#   -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36' \
#   -d '{"userToken":"3449ab69-8813-4db5-836c-3b0f047626e3"}' \
#   --insecure
  
# {"success":true,"data":{"email":"ben-service@agentsben.com","email_verification":true,"currency_unit":"EUR","balance_cny":"211.87","vip_level":0,"service_rate":0.08,"created_at":"2025-03-13T08:33:45.000Z","updated_at":"2025-04-29T12:26:08.000Z"}}

# 全局默认配置
DEFAULT_CONFIG = UserInfoConfig()


async def _make_request(
    userToken: str,
    method: str, 
    endpoint: str, 
    data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    基础请求函数
    
    Args:
        userToken: 用户token
        method: HTTP方法
        endpoint: API端点
        data: 请求数据
            
    Returns:
        响应数据字典
        
    Raises:
        Exception: 请求失败异常
    """
    url = f"{DEFAULT_CONFIG.api_base}{endpoint}"
    
    # 构建请求头，根据curl命令添加完整的headers
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Authorization": f"Bearer {DEFAULT_CONFIG.auth_secret}",
        "Content-Type": "application/json",
        "Origin": "https://agentsben.com",
        "Proxy-Connection": "keep-alive",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    }
    
    # 请求数据包含userToken
    request_data = data or {}
    request_data["userToken"] = userToken
    
    timeout = aiohttp.ClientTimeout(total=DEFAULT_CONFIG.timeout)
    
    # 添加调试信息
    logging.info(f"Making request to: {url}")
    logging.info(f"Method: {method}")
    logging.info(f"Headers: {headers}")
    logging.info(f"Request data: {request_data}")
    
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=request_data
                ) as response:
                    logging.info(f"Response status: {response.status}")
                    logging.info(f"Response headers: {dict(response.headers)}")
                    
                    response.raise_for_status()
                    result = await response.json()
                    logging.info(f"Request result: {result}")
                    
                    # 检查响应是否包含错误信息
                    if isinstance(result, dict) and result.get('message') == 'Error occurred.':
                        logging.error(f"API returned error for endpoint {endpoint}: {result}")
                        # 尝试获取更详细的错误信息
                        error_details = result.get('details', result.get('error', 'Unknown error'))
                        raise Exception(f"API Error: {error_details}")
                    
                    # 检查响应是否包含错误状态
                    if isinstance(result, dict) and result.get('error'):
                        logging.error(f"API returned error for endpoint {endpoint}: {result}")
                        raise Exception(f"API Error: {result.get('error', 'Unknown error')}")
                    
                    return result
                    
            except aiohttp.ClientResponseError as e:
                logging.error(f"HTTP error in _make_request: {e.status} - {e.message}")
                # 尝试读取错误响应体
                try:
                    error_body = await e.response.text()
                    logging.error(f"Error response body: {error_body}")
                except:
                    pass
                raise Exception(f"HTTP Error {e.status}: {e.message}")
            except aiohttp.ClientError as e:
                logging.error(f"Client error in _make_request: {e}")
                raise Exception(f"Client Error: {str(e)}")
            except Exception as e:
                logging.error(f"Unexpected error in _make_request: {e}", exc_info=True)
                raise
                
    except Exception as e:
        logging.error(f"Unexpected error in request: {e}", exc_info=True)
        raise


async def get_user_info(userToken: str):
    """
    获取当前用户的基本信息
    
    Args:
        userToken: 用户token
        
    Returns:
        用户基本信息
    """
    try:
        # 首先尝试POST方法
        return await _make_request(userToken, "POST", "/bot/user/info")
    except Exception as e:
        logging.warning(f"POST request failed, trying GET: {e}")
        try:
            # 如果POST失败，尝试GET方法
            return await _make_request(userToken, "GET", "/bot/user/info")
        except Exception as e2:
            logging.error(f"All methods failed for user info: {e2}")
            # 返回一个默认的用户信息结构，避免完全失败
            return {
                "error": f"API Error: {str(e2)}",
                "user_info": {
                    "id": None,
                    "name": "Unknown User",
                    "email": None,
                    "status": "error"
                }
            }


async def get_user_orders(
    userToken: str, 
    status_alias: Optional[str] = None
) -> List[Any]:
    """
    获取当前用户的订单信息
    
    Args:
        userToken: 用户token
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
    try:
        data = {}
        if status_alias:
            data["status_alias"] = status_alias
            
        return await _make_request(userToken, "POST", "/bot/user/orders", data)
    except Exception as e:
        logging.error(f"Failed to get user orders for token {userToken[:10]}...: {e}")
        # 返回空列表作为fallback
        return []


async def get_user_parcels(userToken: str) -> List[Any]:
    """
    获取当前用户的包裹信息
    
    Args:
        userToken: 用户token
        
    Returns:
        包裹列表（默认最近10个）
    """
    try:
        return await _make_request(userToken, "POST", "/bot/user/parcels")
    except Exception as e:
        logging.error(f"Failed to get user parcels for token {userToken[:10]}...: {e}")
        # 返回空列表作为fallback
        return []


async def get_user_summary(userToken: str) -> Dict[str, Any]:
    """
    获取用户信息摘要（包含基本信息、订单统计、包裹统计）
    
    Args:
        userToken: 用户token
        
    Returns:
        用户信息摘要
    """
    try:
        # 并行获取所有信息
        logging.info(f"get_user_summary userToken: {userToken}")
        
        # 使用return_exceptions=True来避免一个失败导致全部失败
        results = await asyncio.gather(
            get_user_info(userToken),
            get_user_orders(userToken),
            get_user_parcels(userToken),
            return_exceptions=True
        )
        
        user_info, orders, parcels = results
        
        logging.info(f"get_user_summary user_info: {user_info}")
        logging.info(f"get_user_summary orders: {orders}")
        logging.info(f"parcels {parcels}")
       
        return {
            "user_info": user_info,
            "orders": orders,
            "parcels": parcels
        }
        
    except Exception as e:
        logging.error(f"Failed to get user summary: {e}", exc_info=True)
        raise
