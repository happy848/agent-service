"""
Browser client for WhatsApp Web screenshot capture using Playwright.
所有操作必须模拟真人操作，随机的停顿，鼠标位置的移动和点击
每次点击的时候鼠标先移动到该位置，然后点击，记录当前位置，下次点击的时候这个位置作为移动的起始位置
鼠标移动的曲线模拟人类正常使用Windows 浏览器的移动曲线，同时加上随机时间
"""

import asyncio
import json
import logging
import os
import random
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List, Tuple, Literal
from enum import Enum
from functools import wraps
from pydantic import BaseModel, Field
from uuid import uuid4

from client.browser_client import BrowserManager
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from playwright.async_api import Page

from client.models import MessageItem
from client.whatsapp_client_handler import handle_customer_message


# Configure logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Add console handler if not already present
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

# 全局锁，用于确保auto_reply_message函数同一时间只能有一个实例执行
_global_auto_reply_lock = asyncio.Lock()


global_get_img_base64_code = """
    (blobUrl) => {
        return new Promise((resolve, reject) => {
            fetch(blobUrl)
                .then(response => {
                    if (!response.ok) {
                        resolve(null);
                        return;
                    }
                    return response.blob();
                })
                .then(blob => {
                    if (!blob) {
                        resolve(null);
                        return;
                    }
                    
                    const reader = new FileReader();
                    reader.onloadend = () => {
                        resolve(reader.result);
                    };
                    reader.onerror = () => {
                        resolve(null);
                    };
                    reader.readAsDataURL(blob);
                })
                .catch(error => {
                    resolve(null);
                });
        });
    };
    """

class TaskType(Enum):
    """Task type enumeration"""
    SCREENSHOT = "screenshot"
    AUTO_REPLY = "auto_reply"
    CUSTOM = "custom"

def can_auto_replay_contact_message(contact_name: str) -> bool:
    """
    判断是否可以自动回复联系人的消息
    """
    if contact_name == 'WhatsApp':
        return False
    return True
    # return contact_name in ['ben-service@agentsben.com']

class MonitorTask:
    """Monitor task definition"""
    
    def __init__(
        self,
        task_type: TaskType,
        task_func: Callable,
        name: str,
        enabled: bool = True,
        **kwargs
    ):
        self.task_type = task_type
        self.task_func = task_func
        self.name = name
        self.enabled = enabled
        self.kwargs = kwargs
        self.last_execution = None
        self.execution_count = 0
        self.error_count = 0


def screenshot_state_recorder(func):
    """
    装饰器：在方法执行前后记录屏幕截图
    """
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        method_name = func.__name__
        try:
            # 执行方法
            result = await func(self, *args, **kwargs)
            
            # 执行后截图
            await self.take_screenshot(f"{method_name}_after")
            
            return result
            
        except Exception as e:
            # 发生异常时也记录截图
            await self.take_screenshot(f"{method_name}_error")
            raise e
            
    return wrapper


class WhatsAppBrowserClient:
    """Browser client for capturing WhatsApp Web screenshots."""
    
    def __init__(
        self, 
        headless: bool = True, 
        monitor_interval: int = 10,
        user_data_dir: Optional[str] = None,
        profile_name: str = "whatsapp"
    ):
        self.browser_manager = BrowserManager(
            headless=headless, 
            user_data_dir=user_data_dir, 
            profile_name=profile_name
        )
        self.page: Optional[Page] = None
        self.monitor_interval = monitor_interval
        self.monitor: Optional['WhatsAppMonitor'] = None
        self._monitoring_task: Optional[asyncio.Task] = None
        
        # Continue button check tracking
        self._continue_check_attempts = 0
        self._max_continue_attempts = 3
        self._skip_continue_check = False
        
        # 添加当前对话联系人的最新消息缓存
        self._current_chat_info: Optional[MessageItem] = None
        
    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
        
    async def start(self):
        """Start the browser and navigate to WhatsApp Web."""
        # Start browser manager
        # if os.getenv('ENV_MODE') != 'prod':
        #     return

        # Start the browser manager first
        await self.browser_manager.start()
        
        # Create new page with anti-detection
        self.page = await self.browser_manager.new_page()
        
        await self.goto_whatsapp_web()
        
        await self._start_monitoring()
        
        await self.page.wait_for_timeout(3000)
        await self.take_screenshot("wa_start_after")
        # Handle new WhatsApp Web interface modal dialog and other UI changes
        await self._handle_whatsapp_ui_changes()
        
        await self.page.wait_for_timeout(3000)
        await self.take_screenshot("wa_start_after_2")
        
        await self._save_conversation_html_to_log(self.page)
        
    async def goto_whatsapp_web(self):
        """Navigate to WhatsApp Web."""
        # Navigate to WhatsApp Web
        
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        whats_app_web_url = 'https://web.whatsapp.com/'
        
        if self.page.url != whats_app_web_url:
            await self.page.goto(whats_app_web_url)
            await self.page.wait_for_load_state('networkidle')
        else:
            return self.page
        
        await self.page.wait_for_timeout(5000)

    async def _handle_whatsapp_modal_dialog(self):
        """
        Handle the new WhatsApp Web interface modal dialog that appears when the interface changes.
        This handles the dialog with title "WhatsApp 网页版焕然一新" and "继续" button.
        """
        try:
            # Wait for the modal dialog to appear (if it exists)
            await self.page.wait_for_timeout(2000)
            
            # Try multiple selectors for the continue button in the new interface
            continue_button_selectors = [
                'text=继续',
                '[data-testid="modal-continue-button"]',
                'button:has-text("继续")',
                '[role="button"]:has-text("继续")',
                'div[role="button"]:has-text("继续")',
                '//button[contains(text(), "继续")]',
                '//div[contains(text(), "继续")]',
                '[aria-label*="继续"]',
                '[title*="继续"]'
            ]
            
            modal_dialog_selectors = [
                '[data-testid="modal-dialog"]',
                '[role="dialog"]',
                '.modal',
                '[class*="modal"]',
                '[class*="dialog"]',
                'div[class*="Modal"]',
                'div[class*="Dialog"]'
            ]
            
            # Check if modal dialog exists
            modal_found = False
            for selector in modal_dialog_selectors:
                try:
                    modal_element = await self.page.wait_for_selector(selector, timeout=3000)
                    if modal_element:
                        modal_found = True
                        logger.info(f"Found WhatsApp modal dialog with selector: {selector}")
                        break
                except Exception:
                    continue
            
            if not modal_found:
                logger.info("No WhatsApp modal dialog found, continuing normally")
                return
            
            # Try to click the continue button
            for selector in continue_button_selectors:
                try:
                    # Wait a bit for the button to be clickable
                    await self.page.wait_for_timeout(1000)
                    
                    # Check if button exists and is visible
                    button = await self.page.wait_for_selector(selector, timeout=100)
                    if button:
                        # Check if button is visible and clickable
                        is_visible = await button.is_visible()
                        if is_visible:
                            # Move mouse to button and click
                            await button.hover()
                            await self.page.wait_for_timeout(200)
                            await button.click()
                            logger.info(f"Successfully clicked continue button with selector: {selector}")
                            break
                except Exception as e:
                    logger.debug(f"Failed to click button with selector {selector}: {e}")
                    continue
            
        except Exception as e:
            logger.error(f"Error handling WhatsApp modal dialog: {e}")
            # Continue execution even if modal handling fails
    
    async def _handle_whatsapp_ui_changes(self):
        """
        Handle various WhatsApp Web UI changes and popups that might appear.
        This includes the new interface modal, cookie notices, and other dialogs.
        """
        try:
            # Handle the new interface modal dialog
            await self._handle_whatsapp_modal_dialog()
            
            # Handle cookie consent dialogs
            await self._handle_cookie_consent()
            
        except Exception as e:
            logger.error(f"Error handling WhatsApp UI changes: {e}")
    
    async def _handle_cookie_consent(self):
        """Handle cookie consent dialogs if they appear."""
        try:
            cookie_button_selectors = [
                'text=Accept',
                'text=Accept All',
                'text=同意',
                'text=接受',
                '[data-testid="cookie-accept"]',
                'button:has-text("Accept")',
                'button:has-text("同意")',
                '[class*="cookie"] button',
                '[class*="consent"] button'
            ]
            
            for selector in cookie_button_selectors:
                try:
                    button = await self.page.wait_for_selector(selector, timeout=100)
                    if button and await button.is_visible():
                        await button.click()
                        logger.info(f"Clicked cookie consent button: {selector}")
                        break
                except Exception:
                    continue
                    
        except Exception as e:
            logger.debug(f"Error handling cookie consent: {e}")
 
    async def close(self):
        """Close the browser and cleanup."""
        # Stop monitoring if running
        if self.monitor and self.monitor.is_running:
            self.monitor.stop_monitoring()
        
        # Cancel monitoring task if exists
        if self._monitoring_task and not self._monitoring_task.done():
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
        
        await self.browser_manager.close()

    async def _start_monitoring(self):
        """Start monitoring tasks in background."""
        if self.monitor is None:
            self.monitor = WhatsAppMonitor(self, self.monitor_interval)
            
        # Cancel existing monitoring task if running
        if self._monitoring_task and not self._monitoring_task.done():
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass
            
        # Start monitoring in background task
        self._monitoring_task = asyncio.create_task(self.monitor.start_monitoring())
        
        # Add error handling for the background task
        def handle_monitoring_done(task):
            try:
                task.result()
            except Exception as e:
                logger.error(f"WhatsApp monitoring failed with error: {e}", exc_info=True)
                
        self._monitoring_task.add_done_callback(handle_monitoring_done)
    
    def stop_monitoring(self):
        """Stop the monitoring tasks."""
        if self.monitor:
            self.monitor.stop_monitoring()
        
        if self._monitoring_task and not self._monitoring_task.done():
            self._monitoring_task.cancel()
            
    def get_monitoring_status(self) -> Dict[str, Any]:
        """Get monitoring status."""
        if self.monitor:
            return self.monitor.get_status()
        return {'is_running': False, 'monitor_exists': False}
        
    async def auto_reply_message(self) -> List[MessageItem]:
        """Check new messages with lock protection"""
        async with _global_auto_reply_lock:
            if not self.page:
                raise RuntimeError("Browser not started. Call start() first.")
            
            unread_contact_messages = await self.get_unread_messages()
            
            # 保存未读消息日志到本地目录
            await self._save_unread_messages_log(unread_contact_messages)
            
            for contact_message in unread_contact_messages:
                if can_auto_replay_contact_message(contact_message.sender):
                    # Use the new customer service agent to handle messages
                    recently_messages = await self.get_contact_chat_list(contact_message.sender)
                    recently_messages_dict = [message.model_dump() for message in recently_messages]
            
                    latest_message = recently_messages_dict[-1]
                    logger.info('----------------1----------------')
                    logger.info(f"Handling Latest message: {latest_message}")
                    reply = await handle_customer_message(recently_messages_dict)
                    if reply['success']:
                        messages_to_send = reply['ai_reply_message']
                        messages_to_send = [x.strip() for x in messages_to_send.split('\n\n') if x.strip()]
                        
                        logger.info('----------------2----------------')
                        logger.info(f"Messages to send: {messages_to_send}")
                        if len(messages_to_send) == 0:
                            logger.error(f"No messages to send for contact: {contact_message.sender}")
                            continue
                        for message in messages_to_send:
                            await self.send_message_to_contact(contact_message.sender, message.replace('\n', ' '))
                    else:
                        logger.error(f"Failed to generate reply: {reply['error']}")
            
            
            
            return unread_contact_messages
            
    async def take_screenshot(self, filename_prefix: str = "") -> str:
        """Take a screenshot and save it with timestamp."""
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
            
        # Ensure images directory exists
        images_dir = Path("/app/docker/logs")
        images_dir.mkdir(exist_ok=True)
        
        # Generate filename with UTC+8 timestamp in reverse chronological format
        utc_plus_8 = timezone(timedelta(hours=8))
        current_time = datetime.now(utc_plus_8)
        # Use a shorter reverse timestamp format (YYYYMMDDHHMMSS)
        reverse_timestamp = f"{99999999999999 - int(current_time.strftime('%Y%m%d%H%M%S'))}"
        filename = f"{reverse_timestamp}_{filename_prefix}.png"  # Changed extension to .png
        filepath = images_dir / filename
        
        # Take screenshot with PNG format
        await self.page.screenshot(
            path=str(filepath),
            full_page=True,
            type='png'
        )
        
        return str(filepath)
    
    async def _save_unread_messages_log(self, unread_messages: List[MessageItem]) -> None:
        """
        保存未读消息日志到本地目录
        
        Args:
            unread_messages: 未读消息列表
        """
        if not unread_messages:
            logger.info("没有未读消息需要保存")
            return
            
        try:
            # 确保日志目录存在
            logs_dir = Path("/app/docker/logs")
            logs_dir.mkdir(exist_ok=True)
            
            # 生成带时间戳的文件名
            utc_plus_8 = timezone(timedelta(hours=8))
            current_time = datetime.now(utc_plus_8)
            timestamp = current_time.strftime('%Y%m%d_%H%M%S')
            filename = f"unread_messages_{timestamp}.json"
            filepath = logs_dir / filename
            
            # 准备日志数据
            log_data = {
                "timestamp": current_time.isoformat(),
                "total_unread_messages": len(unread_messages),
                "unread_messages": []
            }
            
            # 转换消息为可序列化的格式
            for message in unread_messages:
                message_dict = message.model_dump()
                log_data["unread_messages"].append(message_dict)
            
            # 写入JSON文件
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"未读消息日志已保存到: {filepath}")
            logger.info(f"共保存 {len(unread_messages)} 条未读消息")
            
        except Exception as e:
            logger.error(f"保存未读消息日志失败: {e}")
    
    async def get_target_contact_chat_active(self, target_contact: str) -> Dict[str, Any]:
        """
        Activate (switch to) the chat for a specific contact.
        
        Args:
            target_contact: The name of the contact to activate chat for
            
        Returns:
            Dictionary with activation result:
            {
                "success": bool,
                "contact_found": bool,
                "method_used": "current_chat" | "chat_list" | "search" | "failed",
                "contact_name": "actual contact name found",
                "error": "error message if failed",
                "timestamp": "ISO format timestamp"
            }
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        result = {
            "success": False,
            "contact_found": False,
            "method_used": "failed",
            "contact_name": "",
            "error": None,
            "timestamp": datetime.now().isoformat()
        }
        
        if not target_contact or not target_contact.strip():
            result["error"] = "Target contact name cannot be empty"
            logger.error(result["error"])
            return result
        
        target_contact = target_contact.strip()
        logger.info(f"Activating chat for contact: {target_contact}")
        
        try:
            # Step 1: Check if current conversation is the target user
            current_contact_name = await self._get_current_chat_contact_name()
            contact_info = None
            
            # If current chat matches target, we're already in the right chat
            if current_contact_name and self._is_contact_match(current_contact_name, target_contact):
                result["success"] = True
                result["contact_found"] = True
                result["method_used"] = "current_chat"
                result["contact_name"] = current_contact_name
                return result
            
            # Step 2: Try to find contact in chat list
            contact_info = await self._find_contact_in_chat_list(target_contact)
            if contact_info:
                result["method_used"] = "chat_list"
                result["contact_name"] = contact_info['name']
            else:
                # Step 3: Search for contact
                search_result = await self._search_and_select_contact(target_contact)
                if search_result["success"]:
                    result["method_used"] = "search"
                    result["contact_name"] = search_result["contact_name"]
                    # After search, the contact should be selected, no need to click again
                    contact_info = {"name": search_result["contact_name"], "element": None}
                else:
                    result["error"] = search_result.get("error", "Contact not found in search")
                    logger.error(result["error"])
                    return result
        
            if not contact_info:
                result["error"] = "Contact not found in chat list or search results"
                logger.error(result["error"])
                return result
                
            result["contact_found"] = True
            
            # Only click if we need to switch chats (element exists means we need to click)
            if contact_info["element"] is not None:
                click_success = await self._click_chat_contact(contact_info)
                if not click_success:
                    result["error"] = "Failed to click on contact"
                    logger.error(result["error"])
                    return result
            
            # Verify we're now in the correct chat
            await self.page.wait_for_timeout(random.randint(300, 500))  # Wait for chat to load
            current_contact_name = await self._get_current_chat_contact_name()
            if not current_contact_name or not self._is_contact_match(current_contact_name, target_contact):
                result["error"] = "Failed to activate target contact chat"
                logger.error(result["error"])
                return result
            
            result["success"] = True
            result["contact_name"] = current_contact_name
            logger.info(f"Successfully activated chat for contact '{current_contact_name}' using method: {result['method_used']}")
            
        except Exception as e:
            result["error"] = f"Error activating chat for contact '{target_contact}': {e}"
            logger.error(result["error"])
            
        return result
        
    async def get_contact_chat_list(self, contact_name: str) -> List[Dict[str, Any]]:
        """
        Get chat list for a specific contact.
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        active_chat_result = await self.get_target_contact_chat_active(contact_name)
        if not active_chat_result["success"]:
            return []
        
        return await self.get_chat_messages()
    
    async def get_contacts_list(self, offset: int = 0, limit: int = 5) -> Dict[str, Any]:
        """
        获取联系人列表（分页）
        
        Args:
            offset: 起始位置（从0开始）
            limit: 获取的联系人数量（1-5之间）
            
        Returns:
            Dictionary containing:
            {
                "contacts": List[Dict[str, Any]],  # 联系人信息列表
                    # 每个联系人包含:
                    #   - contact_name: str - 联系人名称
                    #   - phone_number: Optional[str] - 电话号码（如果可获取）
                    #   - message_preview: str - 最后一条消息预览
                    #   - unread_count: int - 未读消息数量
                    #   - is_muted: bool - 是否静音
                    #   - index: int - 在列表中的索引
                "total_count": int,  # 总联系人数量（如果可获取）
                "offset": int,  # 当前偏移量
                "limit": int,  # 本次获取的数量
                "has_more": bool  # 是否还有更多联系人
            }
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        # 限制 limit 在 1-5 之间
        limit = max(1, min(5, limit))
        offset = max(0, offset)
        
        result = {
            "contacts": [],
            "total_count": 0,
            "offset": offset,
            "limit": limit,
            "has_more": False
        }
        
        try:
            # 确保在聊天列表页面
            try:
                await self.page.wait_for_selector('#side', timeout=3000)
            except:
                logger.warning("WhatsApp sidebar not found, page may not be fully loaded")
                return result
            
            # 等待聊天列表可见
            chat_list_selectors = [
                '#pane-side',
                '[aria-label="聊天列表"]',
                '[aria-label="对话列表"]',
                '[aria-label="Chat list"]', 
            ]
            
            chat_list_found = False
            for selector in chat_list_selectors:
                try:
                    await self.page.wait_for_selector(selector, timeout=2000)
                    chat_list_found = True
                    break
                except:
                    continue
            
            if not chat_list_found:
                logger.error("Could not find chat list with any known selector")
                return result
            
            # 获取聊天列表的滚动容器
            scroll_container_selectors = [
                '#pane-side',
                '[aria-label="聊天列表"]',
                '[role="grid"]',
            ]
            
            scroll_container = None
            for selector in scroll_container_selectors:
                try:
                    scroll_container = await self.page.wait_for_selector(selector, timeout=1000)
                    if scroll_container:
                        logger.debug(f"Found scroll container with selector: {selector}")
                        break
                except:
                    continue
            
            # 获取所有聊天列表项（尝试多种选择器以兼容不同版本）
            chat_list_items = None
            item_selectors = [
                '#pane-side [role="listitem"]',  # 旧版本选择器
                '#pane-side [role="row"]',  # 新版本选择器（根据HTML结构）
                '[aria-label="聊天列表"] [role="row"]',
                '[role="grid"] [role="row"]',
            ]
            
            for selector in item_selectors:
                try:
                    items = self.page.locator(selector)
                    count = await items.count()
                    if count > 0:
                        chat_list_items = items
                        logger.debug(f"Using selector: {selector} with {count} items")
                        break
                except:
                    continue
            
            if not chat_list_items:
                logger.error("Could not find chat list items with any known selector")
                return result
            
            total_count = await chat_list_items.count()
            result["total_count"] = total_count
            
            # 计算实际要获取的范围
            end_index = min(offset + limit, total_count)
            result["has_more"] = end_index < total_count
            
            # 如果需要获取的联系人超出当前可见范围，先滚动到目标位置
            if offset > 0 or end_index > 0:
                try:
                    # 方法1: 使用 scroll_into_view_if_needed 滚动到目标元素（推荐方法）
                    target_item = chat_list_items.nth(offset)
                    item_count = await target_item.count()
                    if item_count > 0:
                        # 使用 scroll_into_view_if_needed 滚动到起始位置的联系人
                        await target_item.scroll_into_view_if_needed()
                        # 等待滚动完成
                        await self.page.wait_for_timeout(random.randint(300, 500))
                        logger.info(f"Scrolled to contact at index {offset} using scroll_into_view_if_needed")
                    
                    # 如果结束位置的联系人也需要滚动，也滚动到那里以确保都在可见范围内
                    if end_index > offset + 1:
                        end_item = chat_list_items.nth(end_index - 1)
                        end_item_count = await end_item.count()
                        if end_item_count > 0:
                            await end_item.scroll_into_view_if_needed()
                            await self.page.wait_for_timeout(random.randint(200, 400))
                            logger.info(f"Scrolled to contact at index {end_index - 1} using scroll_into_view_if_needed")
                    
                except Exception as e:
                    logger.warning(f"Error scrolling to target contacts using scroll_into_view_if_needed: {e}")
                    # 方法2: 如果滚动失败，尝试使用JavaScript滚动（备选方案）
                    try:
                        # 根据HTML结构，每个联系人项的高度大约是95px
                        # 但为了更准确，我们可以尝试获取实际高度
                        item_height = 95  # 默认高度（从HTML结构看到）
                        
                        # 尝试获取第一个联系人的实际高度
                        try:
                            first_item = chat_list_items.nth(0)
                            if await first_item.count() > 0:
                                bounding_box = await first_item.bounding_box()
                                if bounding_box and bounding_box.get('height'):
                                    item_height = int(bounding_box['height'])
                                    logger.debug(f"Detected item height: {item_height}px")
                        except:
                            pass
                        
                        # 计算需要滚动到的位置（稍微提前一点，确保目标项在视图中）
                        scroll_position = max(0, offset * item_height - 100)  # 提前100px
                        
                        # 使用JavaScript滚动到目标位置
                        scroll_js = f"""
                            (() => {{
                                // 尝试多个选择器找到滚动容器
                                const selectors = [
                                    '#pane-side',
                                    '[aria-label="聊天列表"]',
                                    '[role="grid"]'
                                ];
                                
                                for (const selector of selectors) {{
                                    const container = document.querySelector(selector);
                                    if (container && container.scrollHeight > container.clientHeight) {{
                                        container.scrollTop = {scroll_position};
                                        return true;
                                    }}
                                }}
                                return false;
                            }})();
                        """
                        
                        scroll_result = await self.page.evaluate(scroll_js)
                        if scroll_result:
                            await self.page.wait_for_timeout(random.randint(300, 500))
                            logger.info(f"Scrolled using JavaScript to position {scroll_position}px (offset={offset})")
                        else:
                            logger.warning("Could not find scrollable container for JavaScript scroll")
                            
                    except Exception as js_error:
                        logger.warning(f"JavaScript scroll also failed: {js_error}")
            
            # 获取指定范围的联系人
            contacts = []
            for i in range(offset, end_index):
                try:
                    item = chat_list_items.nth(i)
                    
                    # 确保元素在视图中，如果不在则滚动
                    try:
                        await item.scroll_into_view_if_needed()
                        await self.page.wait_for_timeout(random.randint(50, 100))
                    except:
                        pass
                    
                    # 获取联系人名称
                    contact_name = None
                    try:
                        name_element = item.locator('span[dir="auto"]').first
                        if await name_element.count() > 0:
                            contact_name = await name_element.text_content()
                            contact_name = contact_name.strip() if contact_name else None
                    except:
                        pass
                    
                    # 如果无法获取名称，尝试其他方式
                    if not contact_name:
                        try:
                            # 尝试从 title 属性获取
                            title = await item.get_attribute('title')
                            if title:
                                contact_name = title.strip()
                        except:
                            pass
                    
                    # 如果仍然没有名称，跳过
                    if not contact_name:
                        logger.debug(f"Could not extract contact name for item {i}")
                        continue
                    
                    # 获取最后一条消息预览
                    message_preview = ""
                    try:
                        preview_elements = await item.locator('.selectable-text.copyable-text, [data-testid="conversation-info"]').all()
                        if preview_elements:
                            preview_text = await preview_elements[0].text_content()
                            if preview_text:
                                message_preview = preview_text.strip()
                    except:
                        pass
                    
                    # 检查是否有未读消息
                    unread_count = 0
                    try:
                        unread_badge = item.locator('[aria-label*="未读"], [aria-label*="unread"]').first
                        if await unread_badge.count() > 0:
                            aria_label = await unread_badge.get_attribute('aria-label')
                            if aria_label:
                                count_match = re.search(r'(\d+)', aria_label)
                                if count_match:
                                    unread_count = int(count_match.group(1))
                    except:
                        pass
                    
                    # 检查是否静音
                    is_muted = False
                    try:
                        muted_indicator = item.locator('[aria-label*="静音"], [aria-label*="muted"]').first
                        if await muted_indicator.count() > 0:
                            is_muted = True
                    except:
                        pass
                    
                    # 获取电话号码
                    phone_number = None
                    try:
                        # 方法1: 从联系人项的 data-id 属性中提取（格式：{phone}@c.us 或 {phone}@g.us）
                        data_id = await item.get_attribute('data-id')
                        if data_id:
                            # 提取电话号码（移除 @c.us 或 @g.us 后缀）
                            phone_match = re.match(r'^(\d+)@[cg]\.us$', data_id)
                            if phone_match:
                                phone_number = phone_match.group(1)
                                logger.debug(f"Extracted phone number from data-id: {phone_number}")
                        
                        # 方法2: 如果方法1失败，尝试从子元素的 data-id 中提取
                        if not phone_number:
                            try:
                                # 查找包含 data-id 的子元素
                                child_elements = await item.locator('[data-id]').all()
                                for child in child_elements:
                                    child_data_id = await child.get_attribute('data-id')
                                    if child_data_id:
                                        phone_match = re.match(r'^(\d+)@[cg]\.us$', child_data_id)
                                        if phone_match:
                                            phone_number = phone_match.group(1)
                                            logger.debug(f"Extracted phone number from child data-id: {phone_number}")
                                            break
                            except:
                                pass
                        
                        # 方法3: 如果联系人的名称本身就是电话号码格式，从名称中提取
                        if not phone_number and contact_name:
                            # 移除常见的电话号码格式字符（空格、连字符、括号等）
                            cleaned_name = re.sub(r'[\s\-\(\)]', '', contact_name)
                            # 检查是否是纯数字（至少7位，最多15位，符合国际电话号码格式）
                            if re.match(r'^\+?\d{7,15}$', cleaned_name):
                                phone_number = cleaned_name.lstrip('+')
                                logger.debug(f"Extracted phone number from contact name: {phone_number}")
                    except Exception as e:
                        logger.debug(f"Error extracting phone number for item {i}: {e}")
                    
                    # 获取联系人元素引用，方便后续直接点击切换到聊天界面
                    contact_element = None
                    try:
                        contact_element = await item.element_handle()
                    except Exception as e:
                        logger.debug(f"Error getting element handle for contact at index {i}: {e}")
                    
                    contact_info = {
                        "contact_name": contact_name,
                        "phone_number": phone_number,
                        "message_preview": message_preview,
                        "unread_count": unread_count,
                        "is_muted": is_muted,
                        "index": i,
                        "element": contact_element  # 保存元素引用，方便后续直接点击
                    }
                    
                    contacts.append(contact_info)
                    
                except Exception as e:
                    logger.warning(f"Error extracting contact info at index {i}: {e}")
                    continue
            
            # 如果启用从当前聊天或详情页获取电话号码，对于没有电话号码的联系人，尝试获取
            for contact in contacts:
                if not contact.get("phone_number"):
                    try:
                        # 打开该联系人的聊天
                        contact_name = contact.get("contact_name")
                        contact_element = contact.get("element")
                        
                        if contact_name:
                            logger.info(f"Attempting to get phone number for: {contact_name}")
                            
                            # 优先使用保存的元素引用直接点击，如果失败则回退到查找方法
                            chat_opened = False
                            if contact_element:
                                try:
                                    # 检查元素是否仍然有效
                                    try:
                                        await contact_element.is_visible(timeout=1000)
                                    except:
                                        # 元素可能已失效，需要重新查找
                                        contact_element = None
                                        logger.debug(f"Contact element for {contact_name} is no longer valid, will use search method")
                                    
                                    if contact_element:
                                        # 使用保存的元素引用直接点击
                                        await contact_element.scroll_into_view_if_needed()
                                        await self.page.wait_for_timeout(random.randint(100, 200))
                                        await contact_element.click()
                                        await self.page.wait_for_timeout(random.randint(500, 1000))  # 等待聊天加载
                                        
                                        # 验证是否成功打开聊天
                                        current_contact = await self._get_current_chat_contact_name()
                                        if current_contact and self._is_contact_match(current_contact, contact_name):
                                            chat_opened = True
                                            logger.debug(f"Successfully opened chat for {contact_name} using saved element reference")
                                        else:
                                            logger.debug(f"Failed to open chat using element reference, will try search method")
                                except Exception as e:
                                    logger.debug(f"Error clicking contact element for {contact_name}: {e}, will try search method")
                            
                            # 如果使用元素引用失败，回退到原来的查找方法
                            if not chat_opened:
                                active_result = await self.get_target_contact_chat_active(contact_name)
                                chat_opened = active_result.get("success")
                                if not chat_opened:
                                    logger.warning(f"Failed to open chat for {contact_name}: {active_result.get('error')}")
                            
                            if chat_opened:
                                phone_number = await self._get_phone_number_from_chat_messages()
                                
                                if phone_number:
                                    contact["phone_number"] = phone_number
                                    logger.info(f"Successfully got phone number: {phone_number}")
                                else:
                                    logger.warning(f"Could not extract phone number for {contact_name}")
                                
                                # 返回到聊天列表
                                # 在桌面版 WhatsApp Web 中，侧边栏通常一直显示，但我们需要确保能看到聊天列表
                                # 尝试点击侧边栏区域或等待页面稳定
                                try:
                                    # 检查是否在聊天列表页面（通过检查 #pane-side 是否存在）
                                    await self.page.wait_for_selector('#pane-side', timeout=2000)
                                    # 如果侧边栏存在，点击一下确保焦点在列表上
                                    side_panel = await self.page.wait_for_selector('#pane-side', timeout=1000)
                                    if side_panel:
                                        await side_panel.click(button='left', click_count=1, delay=random.randint(50, 100))
                                        await self.page.wait_for_timeout(random.randint(200, 400))
                                except:
                                    # 如果侧边栏不可见，可能需要关闭聊天视图
                                    # 在桌面版中，通常侧边栏会一直显示，所以这里主要是等待
                                    await self.page.wait_for_timeout(random.randint(300, 500))
                    except Exception as e:
                        logger.warning(f"Error getting phone number for {contact.get('contact_name')}: {e}")
                        continue
            
            # 清理 contacts 中的 element 字段，避免 JSON 序列化问题
            for contact in contacts:
                if "element" in contact:
                    del contact["element"]
        
            result["contacts"] = contacts
            logger.info(f"Successfully retrieved {len(contacts)} contacts (offset={offset}, limit={limit})")
            
        except Exception as e:
            logger.error(f"Error getting contacts list: {e}")
        
        return result
        
    async def get_chat_messages(self) -> List[MessageItem]:
        """
        Get structured chat messages from current chat conversation.
        
        Returns:
            List of MessageItem objects containing chat messages
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        try:
            # Wait for chat messages to load
            await self.page.wait_for_selector('#main', timeout=1000)
            # await self._save_conversation_html_to_log(self.page)
            
            messages: List[MessageItem] = []
            
            # Get all message elements (both incoming and outgoing)
            message_elements = await self.page.query_selector_all('#main [role="row"] .message-in, #main [role="row"] .message-out')
            
            for element in message_elements:
                try:
                    message_data = await self._parse_message_element(element)
                    if message_data:
                        messages.append(message_data)
                except Exception as e:
                    logger.warning(f"Error parsing message element: {e}")
                    continue
            
            logger.info(f"Extracted {len(messages)} messages from chat")
            return messages
            
        except Exception as e:
            logger.error(f"Error getting chat messages: {e}")
            return []
    
    async def _parse_message_element(self, element) -> Optional[MessageItem]:
        """
        Parse individual message element to extract message data.
        
        Args:
            element: Playwright element for message
            
        Returns:
            MessageItem object if parsing succeeds, None otherwise
        """
        try:
            # Determine message type based on class
            class_list = await element.get_attribute('class')
            is_outgoing = 'message-out' in class_list
            message_type = "sent" if is_outgoing else "received"
            
            # Get sender information
            sender = "Me" if is_outgoing else "Unknown"
            
            # For incoming messages, try to extract sender from aria-label or data attributes
            if not is_outgoing:
                # Try to get sender from copyable-text data-pre-plain-text attribute
                pre_text_element = await element.query_selector('[data-pre-plain-text]')
                if pre_text_element:
                    pre_text = await pre_text_element.get_attribute('data-pre-plain-text')
                    if pre_text:
                        # Extract sender from format like "[08:32, 2025年6月13日] +33 6 26 34 58 35: "
                        sender_match = re.search(r'\] ([^:]+):', pre_text)
                        if sender_match:
                            sender = sender_match.group(1).strip()
                
                # Alternative: try aria-label
                if sender == "Unknown":
                    aria_label_element = await element.query_selector('[aria-label]')
                    if aria_label_element:
                        aria_label = await aria_label_element.get_attribute('aria-label')
                        if aria_label and '：' in aria_label:
                            sender = aria_label.replace('：', '').strip()
            
            # Get message content
            content = ""
            content_elements = await element.query_selector_all('.selectable-text.copyable-text')
            
            content_parts = []
            for content_element in content_elements:
                text = await content_element.text_content()
                if text and text.strip():
                    # Skip time stamps (usually short and contain only numbers/colons)
                    if not re.match(r'^\d{1,2}:\d{2}$', text.strip()):
                        content_parts.append(text.strip())
            
            content = ' '.join(content_parts).strip()
            
            # Extract images from the message - only process blob URLs and base64 data
            images = []
            img_elements = await element.query_selector_all('img')
            for img_elem in img_elements:
                try:
                    img_src = await img_elem.get_attribute('src')
                    if img_src:
                        # Only handle blob URLs and base64 data
                        if img_src.startswith('blob:'):
                            # Convert blob URL to base64
                            logger.info(f'Processing blob URL: {img_src}')
                            await self.page.wait_for_timeout(3000)
                            base64_data = await self._convert_blob_to_base64(img_src)
                            logger.info(f'Base64 conversion result: {base64_data[:100] if base64_data else "None"}...')
                            
                            if base64_data:
                                images.append(base64_data)
                        elif img_src.startswith('data:image/'):
                            # Direct base64 data - add directly
                            logger.info(f'Found base64 image data: {img_src[:100]}...')
                            images.append(img_src)
                        else:
                            # Skip regular URLs - only process blob URLs and base64 data
                            logger.debug(f'Skipping regular URL: {img_src}')
                            continue
                except Exception as e:
                    logger.warning(f"Error extracting image: {e}")
                    continue
            
            # Get datetime
            datetime_str = ""
            current_time = datetime.now(timezone(timedelta(hours=8)))
            timestamp = current_time.isoformat()
            
            # Try to extract datetime from data-pre-plain-text
            pre_text_element = await element.query_selector('[data-pre-plain-text]')
            if pre_text_element:
                pre_text = await pre_text_element.get_attribute('data-pre-plain-text')
                if pre_text:
                    # Extract datetime from format like "[08:32, 2025年6月13日]"
                    datetime_match = re.search(r'\[([^\]]+)\]', pre_text)
                    if datetime_match:
                        datetime_str = datetime_match.group(1).strip()
            
            # If not found, try to get time from visible time elements
            if not datetime_str:
                time_elements = await element.query_selector_all('.x1rg5ohu.x16dsc37, .x1c4vz4f.x2lah0s')
                for time_elem in time_elements:
                    time_text = await time_elem.text_content()
                    if time_text and re.match(r'^\d{1,2}:\d{2}$', time_text.strip()):
                        # Add current date if only time is available
                        current_date = current_time.strftime("%Y-%m-%d")
                        datetime_str = f"{time_text.strip()}, {current_date}"
                        break
            
            # Skip if no content and no images found
            if not content and not images:
                return None
            
            return MessageItem(
                type=message_type,
                sender=sender,
                content=content,
                datetime=datetime_str or current_time.strftime('%Y-%m-%d %H:%M:%S'),
                timestamp=timestamp,
                images=images
            )
            
        except Exception as e:
            logger.warning(f"Error parsing message element: {e}")
            return None

    async def _convert_blob_to_base64(self, blob_url: str) -> Optional[str]:
        """
        Convert blob URL to base64 encoded image data.
        Only processes blob URLs, skips regular URLs.
        
        Args:
            blob_url: The blob URL to convert
            
        Returns:
            Base64 encoded image data string or None if conversion fails
        """
        try:
            if not self.page:
                logger.warning("Browser page not available for blob conversion")
                return None
            
            # Validate that this is actually a blob URL
            if not blob_url.startswith('blob:'):
                logger.warning(f"Not a blob URL, skipping: {blob_url}")
                return None
            
            # Execute JavaScript to convert blob URL to base64
            try:
                base64_data = await self.page.evaluate(global_get_img_base64_code, blob_url)
                logger.info(f"JavaScript execution completed for blob URL: {blob_url[:50]}...")
            except Exception as js_error:
                logger.error(f"JavaScript execution failed for blob URL: {js_error}")
                return None
                
            if base64_data and base64_data.startswith('data:image/'):
                logger.info(f"Successfully converted blob URL to base64 image data")
                return base64_data
            else:
                logger.warning(f"Failed to convert blob URL to valid base64 image data: {blob_url[:50]}...")
                return None
                
        except Exception as e:
            logger.warning(f"Error converting blob to base64: {e}")
            return None



    async def send_message_to_contact(self, contact_name: str, message: str) -> Dict[str, Any]:
        """
        Send a message to a specific contact.
        
        Implementation steps:
        1. Check if current conversation is the target user, if yes send message directly
        2. If current conversation is not the target user, check if user is in conversation list, if yes click username and send message
        3. If not in list, search first, then click username and send message after finding results
        4. If user not found in search, return failure
        
        Args:
            contact_name: The name of the contact to send message to
            message: The message text to send
            
        Returns:
            Dictionary with send result:
            {
                "success": bool,
                "contact_found": bool,
                "method_used": "current_chat" | "chat_list" | "search" | "failed",
                "message": "sent message text",
                "error": "error message if failed",
                "timestamp": "ISO format timestamp"
            }
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        if not contact_name or not contact_name.strip():
            return {
                "success": False,
                "contact_found": False,
                "method_used": "failed",
                "message": message,
                "error": "Contact name cannot be empty",
                "timestamp": datetime.now().isoformat()
            }
        
        if not message or not message.strip():
            return {
                "success": False,
                "contact_found": False,
                "method_used": "failed",
                "message": "",
                "error": "Message cannot be empty",
                "timestamp": datetime.now().isoformat()
            }
        
        result = {
            "success": False,
            "contact_found": False,
            "method_used": "failed",
            "message": message.strip(),
            "error": None,
            "timestamp": datetime.now().isoformat()
        }
        
        target_contact = contact_name.strip()
        logger.info(f"Attempting to send message to contact: {target_contact}")
        
        try:
            active_chat_result = await self.get_target_contact_chat_active(contact_name)
            if not active_chat_result["success"]:
                result["error"] = active_chat_result["error"]
                return result

            await self.page.wait_for_timeout(random.randint(500, 1500))
            send_result = await self._send_message_to_current_chat(message)
            
            result["contact_found"] = True
            result["success"] = send_result.get("success", False)
            result["error"] = send_result.get("error")
            
            return result
        except Exception as e:
            error_msg = f"Error sending message to contact '{target_contact}': {e}"
            result["error"] = error_msg
            logger.error(error_msg)
            return result

    async def _get_phone_number_from_chat_messages(self) -> Optional[str]:
        """
        从当前聊天页面的消息元素中获取电话号码。
        通过查找消息元素的 data-id 属性来提取电话号码。
        data-id 格式示例: "false_33637723195@c.us_3A9F53B9DF1E162BD003"
        
        Returns:
            电话号码字符串（如果找到），否则返回 None
        """
        try:
            # 确保在聊天页面
            await self.page.wait_for_selector('#main', timeout=3000)
            
            # 等待消息加载
            await self.page.wait_for_timeout(random.randint(500, 800))
            
            # 查找消息容器元素，从它们的 data-id 属性中提取电话号码
            # 根据HTML结构，消息的 data-id 在 #main [role="row"] 下的 div 元素上
            # 格式: "false_33637723195@c.us_3A9F53B9DF1E162BD003"
            message_data_id_selectors = [
                '#main [role="row"] > div[data-id]',  # 直接子元素
                '#main [role="row"] [data-id]',  # 所有子元素
            ]
            
            phone_number = None
            
            for selector in message_data_id_selectors:
                try:
                    # 查找所有包含 data-id 的消息元素
                    data_id_elements = await self.page.query_selector_all(selector)
                    
                    for element in data_id_elements:
                        try:
                            data_id = await element.get_attribute('data-id')
                            if data_id:
                                # data-id 格式: "false_33637723195@c.us_3A9F53B9DF1E162BD003"
                                # 提取电话号码部分: {phone}@c.us 或 {phone}@g.us
                                phone_match = re.search(r'(\d+)@[cg]\.us', data_id)
                                if phone_match:
                                    phone_number = phone_match.group(1)
                                    logger.debug(f"Extracted phone number from message data-id: {phone_number} (data-id: {data_id})")
                                    break
                        except Exception as e:
                            logger.debug(f"Error extracting data-id from element: {e}")
                            continue
                    
                    if phone_number:
                        break
                except Exception as e:
                    logger.debug(f"Error querying selector {selector}: {e}")
                    continue
            
            if phone_number:
                logger.info(f"Successfully extracted phone number from chat messages: {phone_number}")
            else:
                logger.warning("Could not extract phone number from chat messages")
            
            return phone_number
            
        except Exception as e:
            logger.error(f"Error getting phone number from chat messages: {e}")
            return None


    async def _get_current_chat_contact_name(self) -> Optional[str]:
        """
        Get the name of the contact in the currently open chat based on HTML structure.
        
        Returns:
            Contact name if found, None otherwise
        """
        try:
            # Wait for main chat area to be present
            await self.page.wait_for_selector('#main', timeout=3000)
            
            # Try multiple selectors for the contact name in header based on WhatsApp structure
            contact_selectors = [
                '#main header div[role="button"] span[dir="auto"]',
            ]
            
            for selector in contact_selectors:
                try:
                    contact_element = await self.page.wait_for_selector(selector, timeout=1000)
                    if contact_element:
                        contact_name = await contact_element.text_content()
                        if contact_name and contact_name.strip():
                            # Filter out common non-contact text
                            name = contact_name.strip()
                            # Skip if it looks like a time, status, or other UI element
                            if not re.match(r'^\d{2}:\d{2}$', name) and name not in ['在线', 'online', '输入中', 'typing']:
                                logger.debug(f"Found current contact name with selector '{selector}': {name}")
                                return name
                except:
                    continue
            logger.warning("Could not find current chat contact name")
            return None
            
        except Exception as e:
            logger.error(f"Error getting current chat contact name: {e}")
            return None

    async def _get_current_chat_last_message(self) -> Optional[MessageItem]:
        """
        获取当前对话的最新消息信息
        
        Returns:
            MessageItem object if found, None otherwise
        """
        try:
            # 获取当前对话联系人名称
            current_contact = await self._get_current_chat_contact_name()
            if not current_contact:
                return None
                
            # 获取当前对话的消息列表
            messages = await self.get_chat_messages()
            if not messages:
                return None
                
            # Find last received message
            last_received_message = None
            for message in reversed(messages):
                if message.type == "received":
                    current_time = datetime.now(timezone(timedelta(hours=8)))
                    last_received_message = MessageItem(
                        type="received",
                        sender=message.sender,
                        content=message.content,
                        datetime=message.datetime or current_time.strftime('%Y-%m-%d %H:%M:%S'),
                        timestamp=message.timestamp or current_time.isoformat(),
                        images=message.images
                    )
                    break
            
            return last_received_message
            
        except Exception as e:
            logger.error(f"Error getting current chat last message: {e}")
            return None

    def _is_contact_match(self, current_contact: str, target_contact: str) -> bool:
        """
        Check if two contact names match (case insensitive, with some fuzzy matching).
        
        Args:
            current_contact: Current contact name
            target_contact: Target contact name
            
        Returns:
            True if contacts match, False otherwise
        """
        logger.info(f"Checking if {current_contact} matches {target_contact}")
        if not current_contact or not target_contact:
            return False
        
        current = current_contact.lower().strip().replace("+", "")
        target = target_contact.lower().strip().replace("+", "")
        
        # Exact match
        if current == target:
            return True
        
        # Check if one contains the other (for partial matches)
        if current in target or target in current:
            return True
        
        # Check if they match when removing common suffixes/prefixes
        # Remove common phone number formatting
        current_clean = re.sub(r'[+\-\s\(\)]', '', current)
        target_clean = re.sub(r'[+\-\s\(\)]', '', target)
        
        if current_clean == target_clean:
            return True
        
        return False

    async def _find_contact_in_chat_list(self, target_contact: str) -> Optional[Dict[str, Any]]:
        """
        Find a contact in the chat list using Playwright locators for fast string matching.
        
        Args:
            target_contact: The contact name to search for
            
        Returns:
            Dictionary with contact info if found, None otherwise
        """
        try:
            # First check if we're logged in and on the main page
            try:
                await self.page.wait_for_selector('#side', timeout=3000)
            except:
                logger.warning("WhatsApp sidebar not found, page may not be fully loaded")
                return None
            
            # Wait for chat list to be visible - try multiple selectors
            chat_list_selectors = [
                '#pane-side',
                '[aria-label="聊天列表"]',
                '[aria-label="对话列表"]',
                '[aria-label="Chat list"]', 
            ]
            
            chat_list_found = False
            for selector in chat_list_selectors:
                try:
                    await self.page.wait_for_selector(selector, timeout=2000)
                    chat_list_found = True
                    logger.debug(f"Found chat list with selector: {selector}")
                    break
                except:
                    continue
            
            if not chat_list_found:
                logger.error("Could not find chat list with any known selector")
                return None
            
            # Use Playwright locator to find chat item by contact name
            # Try different matching strategies with improved safety and precision
            contact_locators = []
            
            # Strategy 1: Direct text match within pane-side
            contact_locators.append(
                self.page.locator('#pane-side').get_by_text(target_contact)
            )
            
            # Strategy 2: Role-based search for all list items within pane-side
            chat_list_items = self.page.locator('#pane-side [role="listitem"]')
            # Get all list items
            items_count = await chat_list_items.count()
            for i in range(items_count):
                contact_locators.append(chat_list_items.nth(i))
         
            for locator in contact_locators:
                try:
                    # Check if any matching elements exist
                    if await locator.count() > 0:
                        # Get the first matching chat item
                        first_match = locator.first
                        
                        # Get the contact name from title attribute or text content
                        contact_name = None
                        try:
                            name_element = first_match.locator('span[dir="auto"]').first
                            if await name_element.count() > 0:
                                contact_name = await name_element.text_content()
                                contact_name = contact_name.strip() if contact_name else None
                        except:
                            pass
                           
                        
                        if contact_name and self._is_contact_match(contact_name, target_contact):
                            logger.info(f"Found matching contact in chat list: {contact_name}")
                            
                            # Get the actual element for clicking
                            element = await first_match.element_handle()
                            return {
                                'name': contact_name,
                                'element': element
                            }
                        
                except Exception as e:
                    logger.debug(f"Locator strategy failed: {e}")
                    continue
            
            logger.info(f"Contact '{target_contact}' not found in chat list")
            
            return None
            
        except Exception as e:
            logger.error(f"Error finding contact in chat list: {e}")
            
            return None

    async def _click_chat_contact(self, contact_info: Dict[str, Any]) -> bool:
        """
        Click on a contact in the chat list based on HTML structure.
        
        Args:
            contact_info: Dictionary containing contact element
            
        Returns:
            True if click was successful, False otherwise
        """
        try:
            chat_element = contact_info['element']
            
            if chat_element:
                # Scroll element into view if needed
                await chat_element.scroll_into_view_if_needed()
                await self.page.wait_for_timeout(200)
                
                # Click the element
                await chat_element.click()
                await self.page.wait_for_timeout(random.randint(500, 1000))  # Wait for chat to load
                logger.info(f"Successfully clicked on contact: {contact_info['name']}")
                return True
            else:
                logger.error(f"Chat element not found for contact: {contact_info['name']}")
                return False
            
        except Exception as e:
            logger.error(f"Error clicking chat contact: {e}")
            return False

    async def _search_and_select_contact(self, target_contact: str) -> Dict[str, Any]:
        """
        Search for a contact using WhatsApp's search functionality and select the first result.
        
        Args:
            target_contact: The contact name to search for
            
        Returns:
            Dictionary with search result:
            {
                "success": bool,
                "contact_name": str,
                "error": str
            }
        """
        result = {
            "success": False,
            "contact_name": "",
            "error": None
        }

        try:
            # First try to activate search mode by clicking the search button
            search_button_selectors = [
                '#side button[aria-label="搜索或开始新对话"]',
                '#side button[aria-label="Search or start new chat"]',
            ]
            
            for selector in search_button_selectors:
                try:
                    search_button = await self.page.wait_for_selector(selector, timeout=2000)
                    if search_button:
                        await search_button.click()
                        await self.page.wait_for_timeout(200)
                        logger.debug(f"Activated search with button selector: {selector}")
                        break
                except:
                    continue
            
            # Find the search input field based on HTML structure
            search_input_selectors = [
                '[aria-label="搜索输入内容文本框"][contenteditable="true"]',
                '[aria-label="Search input text box"][contenteditable="true"]', 
            ]
            
            search_input = None
            for selector in search_input_selectors:
                try:
                    search_input = await self.page.wait_for_selector(selector, timeout=1000)
                    if search_input:
                        logger.debug(f"Found search input with selector: {selector}")
                        break
                except:
                    continue
            
            if not search_input:
                result["error"] = "Search input field not found"
                return result
            
            # Clear existing search content and enter target contact name
            await search_input.click()
            
            await self.human_like_input(search_input, target_contact, clear_first=True, press_enter=True)
            
            await self.page.wait_for_timeout(random.randint(1500, 3000))  # Wait for search results
            
            # Find the contact in search results
            chat_contact = await self._find_contact_in_chat_list(target_contact)
            
            if not chat_contact:
                # Clear search before returning
                await self.human_like_input(search_input, '', clear_first=True, press_enter=False)
                result["error"] = f"No search results found for '{target_contact}'"
                return result
            
            # Click on the found result
            click_success = await self._click_search_result(chat_contact)
            
            if click_success:
                result["success"] = True
                result["contact_name"] = chat_contact['name']
                logger.info(f"Successfully selected contact from search: {chat_contact['name']}")
            else:
                result["error"] = "Failed to click on search result"

            await self.human_like_input(search_input, '', clear_first=True, press_enter=False)
            return result
            
        except Exception as e:
            error_msg = f"Error searching for contact: {e}"
            result["error"] = error_msg
            logger.error(error_msg)
            return result

    async def _click_search_result(self, result_info: Dict[str, Any]) -> bool:
        """
        Click on a search result to open the chat based on HTML structure.
        
        Args:
            result_info: Dictionary containing result element
            
        Returns:
            True if click was successful, False otherwise
        """
        try:
            result_element = result_info['element']
            
            # Based on HTML structure, find the clickable area within the search result
            clickable_selectors = [
                '[role="button"]',  # Primary from HTML structure
            ]
            
            clickable_element = None
            for selector in clickable_selectors:
                try:
                    clickable_element = await result_element.query_selector(selector)
                    if clickable_element:
                        logger.debug(f"Found clickable search result with selector: {selector}")
                        break
                except:
                    continue
            
            if clickable_element:
                # Scroll into view and click
                await clickable_element.scroll_into_view_if_needed()
                await self.page.wait_for_timeout(200)
                await clickable_element.click()
            else:
                # Fallback: click the result element directly
                await result_element.scroll_into_view_if_needed()
                await result_element.click()
            
            # Wait for chat to load
            await self.page.wait_for_timeout(1500)
            
            logger.info(f"Successfully clicked on search result: {result_info['name']}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error clicking search result: {e}")
            return False
  
    async def _send_message_to_current_chat(self, message: str) -> Dict[str, Any]:
        """
        Send a message in the current WhatsApp chat by typing in the input field and pressing Enter.
        Uses random delays between characters to simulate natural human typing.
        
        Args:
            message: The message text to send
            
        Returns:
            Dictionary with send result:
            {
                "success": bool,
                "message": "sent message text",
                "error": "error message if failed",
                "timestamp": "ISO format timestamp"
            }
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        if not message or not message.strip():
            return {
                "success": False,
                "message": "",
                "error": "Message cannot be empty",
                "timestamp": datetime.now().isoformat()
            }
        
        result = {
            "success": False,
            "message": message.strip(),
            "error": None,
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            # Wait for the chat to be loaded and input field to be available
            await self.page.wait_for_selector('#main', timeout=2000)
            
            # Find the message input field using multiple possible selectors
            input_selectors = [
                '[aria-label="输入消息"]',
                '[aria-label="Type a message"]',
                '[contenteditable="true"][role="textbox"]',
            ]
            
            input_element = None
            for selector in input_selectors:
                try:
                    input_element = await self.page.wait_for_selector(selector, timeout=1000)
                    if input_element:
                        logger.debug(f"Found input element with selector: {selector}")
                        break
                except:
                    continue
            
            if not input_element:
                result["error"] = "Message input field not found"
                logger.error("Could not find message input field")
                return result
            
            await self.human_like_input(input_element, message, clear_first=True, press_enter=True)
            
            logger.info(f"Sent message: {message}")

            # Save conversation HTML for debugging

            result["success"] = True
            logger.info(f"Successfully sent message: {message}")

        except Exception as e:
            error_msg = f"Error sending message: {e}"
            result["error"] = error_msg
            logger.error(error_msg)
        
        return result
  
    async def _save_conversation_html_to_log(self, page):
        """
        Save conversation list HTML content to log file for debugging.
        """
        try:
            # Ensure logs directory exists
            logs_dir = Path("/app/docker/logs")
            logs_dir.mkdir(exist_ok=True)
            # Generate filename with timestamp
            utc_plus_8 = timezone(timedelta(hours=8))
            current_time = datetime.now(utc_plus_8)
            # Use a shorter reverse timestamp format (YYYYMMDDHHMMSS)
            reverse_timestamp = f"{99999999999999 - int(current_time.strftime('%Y%m%d%H%M%S'))}"
            filename = f"{reverse_timestamp}_wa_conversation_list_raw.html"
            html_filepath = logs_dir / filename
            
            html_content = await page.content()
            with open(html_filepath, 'w', encoding='utf-8') as f:
                f.write(html_content)
            
            logger.info(f"Saved conversation HTML debug file: {html_filepath}")
            
        except Exception as e:
            logger.error(f"Error saving conversation HTML to log: {e}", exc_info=True)
    
    async def human_like_input(self, element_or_selector, text: str, clear_first: bool = True, press_enter: bool = True) -> bool:
        """
        通用的人类化输入方法，模拟真实用户的输入行为
        
        Args:
            element_or_selector: 元素对象或CSS选择器字符串
            text: 要输入的文本内容
            clear_first: 是否先清空现有内容，默认为True
            press_enter: 是否在输入完成后按Enter键，默认为False
            
        Returns:
            bool: 输入是否成功
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
            
        try:
            # 获取目标元素
            target_element = None
            if isinstance(element_or_selector, str):
                # 如果是选择器字符串，查找元素
                target_element = await self.page.wait_for_selector(element_or_selector, timeout=2000)
                if not target_element:
                    logger.error(f"Element not found with selector: {element_or_selector}")
                    return False
            else:
                # 如果是元素对象，直接使用
                target_element = element_or_selector
            
            # 点击元素获得焦点
            await target_element.click()
            await self.page.wait_for_timeout(random.randint(100, 200))

            # 清空现有内容
            if clear_first:
                await self.page.keyboard.press('Control+a')
                await self.page.wait_for_timeout(random.randint(50, 100))
                await self.page.keyboard.press('Backspace')
                await self.page.wait_for_timeout(random.randint(50, 100))
            
            if text.strip() == '':
                return True

            # 逐字符输入，模拟人类打字习惯
            for i, char in enumerate(text.strip()):
                # 输入当前字符
                await self.page.keyboard.type(char)
                
                # 字符间随机间隔 (30-150ms)
                if i < len(text.strip()) - 1:  # 最后一个字符后不暂停
                    char_interval = random.randint(20, 100)
                    await self.page.wait_for_timeout(char_interval)
                    
                    # 5% 概率模拟思考停顿
                    if random.random() < 0.05:
                        thinking_pause = random.randint(100, 300)
                        await self.page.wait_for_timeout(thinking_pause)
            
            # 模拟可能的人类行为：30% 概率进行退格重打最后一个字符
            if len(text.strip()) > 0 and random.random() < 0.3:
                await self.page.keyboard.press('Backspace')
                await self.page.wait_for_timeout(random.randint(50, 150))
                # 重新输入最后一个字符
                await self.page.keyboard.type(text.strip()[-1])
                await self.page.wait_for_timeout(random.randint(100, 200))
            
            # 输入完成后的随机等待
            await self.page.wait_for_timeout(random.randint(100, 300))
            
            # 根据参数决定是否按Enter键
            if press_enter:
                await self.page.keyboard.press('Enter')
                
            return True
            
        except Exception as e:
            logger.error(f"Error in human_like_input: {e}")
            return False

    async def get_unread_messages(self) -> List[MessageItem]:
        """
        Get unread contact messages, excluding muted conversations.
        Also tracks and compares the current chat contact's last message.
        
        Returns:
            List of MessageItem objects containing unread messages
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        unread_messages: List[MessageItem] = []
        
        try:
            
            # 获取当前对话的最新消息信息
            current_chat_info = await self._get_current_chat_last_message()
            
            # 如果有当前对话信息，检查是否有新消息
            if current_chat_info and self._current_chat_info:
                # 只有当联系人相同时才进行比较
                if current_chat_info.sender == self._current_chat_info.sender:
                    # 如果最新消息不同，说明有新消息
                    if current_chat_info.content != self._current_chat_info.content:
                        unread_messages.append(current_chat_info)
            
            # 更新当前对话信息缓存
            self._current_chat_info = current_chat_info
            
            # 使用更简单的选择器查找未读消息指示器
            unread_selectors = [
                '[aria-label*="未读消息"]',
                '[aria-label*="unread message"]',
                '[data-testid="unread-message"]'
            ]
            
            unread_items = []
            for selector in unread_selectors:
                try:
                    await self.click_chat_list_tab("all")
                    await self.click_chat_list_tab("unread")
                    items = await self.page.query_selector_all(selector)
                    unread_items.extend(items)
                except Exception as e:
                    logger.debug(f"Selector {selector} failed: {e}")
                    continue
            
            if not unread_items:
                return unread_messages
            
            for unread_indicator in unread_items:
                try:
                    # 检查是否为静音对话
                    try:
                        muted_indicator = await unread_indicator.query_selector(
                            '[aria-label="已静音的对话"], [aria-label*="muted"]'
                        )
                        
                        if muted_indicator:
                            logger.debug("Skipping muted conversation")
                            continue
                    except Exception as e:
                        logger.debug(f"Error checking muted status: {e}")
                    
                    # 提取未读消息数量
                    try:
                        aria_label = await unread_indicator.get_attribute('aria-label')
                        unread_count = "1"  # Default to 1 if can't parse
                        if aria_label:
                            count_match = re.search(r'(\d+)', aria_label)
                            if count_match:
                                unread_count = count_match.group(1)
                    except Exception as e:
                        logger.debug(f"Error extracting aria-label: {e}")
                        unread_count = "1"
                    
                    # 使用Playwright的内置方法查找父级聊天项
                    try:
                        # 尝试多种方式查找父级聊天项
                        chat_item_element = None
                        
                        # 方法1: 使用closest选择器
                        try:
                            chat_item_element = await unread_indicator.query_selector(
                                'xpath=ancestor::div[@role="listitem"][1]'
                            )
                        except Exception:
                            pass
                        
                        # 方法2: 使用data-testid选择器
                        if not chat_item_element:
                            try:
                                chat_item_element = await unread_indicator.query_selector(
                                    'xpath=ancestor::div[@data-testid="cell" or @data-testid="chat-list-item"][1]'
                                )
                            except Exception:
                                pass
                        
                        # 方法3: 使用常见的WhatsApp类名
                        if not chat_item_element:
                            try:
                                chat_item_element = await unread_indicator.query_selector(
                                    'xpath=ancestor::div[contains(@class, "_8nE1Y")][1]'
                                )
                            except Exception:
                                pass
                        
                        if not chat_item_element:
                            logger.debug("Could not find parent chat item for unread indicator")
                            continue
                            
                        # 验证元素是否仍然有效
                        try:
                            await chat_item_element.get_attribute('role')
                        except Exception:
                            logger.debug("Element is no longer valid, skipping")
                            continue
                            
                    except Exception as e:
                        logger.debug(f"Error finding parent chat item: {e}")
                        continue
                    
                    # 获取联系人名称
                    contact_name = "Unknown"
                    
                    try:
                        # 使用更精确的选择器查找联系人名称
                        name_elements = await chat_item_element.query_selector_all(
                            'span[dir="auto"], [data-testid="conversation-title"]'
                        )
                        
                        for name_elem in name_elements:
                            text = await name_elem.text_content()
                            if text and text.strip() and not re.match(r'^\d{1,2}:\d{2}$', text.strip()):
                                contact_name = text.strip()
                                break
                        
                        # 获取消息预览
                        message_preview_container = await chat_item_element.text_content()
                    except Exception as e:
                        logger.warning(f"Error extracting text content: {e}")
                        message_preview_container = "Message content unavailable"
                    
                    current_time = datetime.now(timezone(timedelta(hours=8)))
                    
                    # 创建未读消息条目
                    unread_entry = MessageItem(
                        type="received",
                        sender=contact_name,
                        content=message_preview_container,
                        datetime=current_time.strftime('%Y-%m-%d %H:%M:%S'),
                        timestamp=current_time.isoformat(),
                        images=[]
                    )
                    
                    unread_messages.append(unread_entry)
                    logger.info(f"Found unread message from {contact_name}: {unread_count} messages")
                    
                except Exception as e:
                    logger.warning(f"Error extracting chat info from item: {e}", exc_info=True)
                    continue
            
            # 对 unread_messages 进行去重，基于 sender 和 content
            unique_messages = []
            seen_combinations = set()
            
            for message in unread_messages:
                # 创建唯一标识符：sender + content
                unique_key = f"{message.sender}:{message.content}"
                if unique_key not in seen_combinations:
                    seen_combinations.add(unique_key)
                    unique_messages.append(message)
            
            await self.random_reload_page()
            return unique_messages
        except Exception as e:
            logger.error(f"Error getting unread contact messages: {e}")
            return []

    async def random_reload_page(self):
        """Randomly reload the page"""
        # 1/100 概率刷新页面
        import random
        if random.randint(1, 100) == 1:
            logger.info("Random page refresh triggered (1/100 probability)")
            await self.page.reload()
            await asyncio.sleep(5)  # 等待页面加载
            await self._handle_whatsapp_ui_changes()
    
    async def click_chat_list_tab(self, tab_name: str) -> Dict[str, Any]:
        """
        点击聊天列表的tab按钮（所有、未读、特别关注、群组）
        
        Args:
            tab_name: Tab名称，支持的值: "all", "unread", "favorites", "groups"
                     对应的中文: "所有", "未读", "特别关注", "群组"
        
        Returns:
            Dictionary with click result:
            {
                "success": bool,
                "tab_name": str,
                "error": str
            }
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        # Tab名称映射
        tab_mapping = {
            "all": "所有",
            "unread": "未读", 
            "favorites": "特别关注",
            "groups": "群组"
        }
        
        result = {
            "success": False,
            "tab_name": tab_name,
            "error": None
        }
        
        try:
            # 获取对应的中文名称
            chinese_name = tab_mapping.get(tab_name.lower(), tab_name)
            
            # 构建tab选择器
            tab_selectors = [
                f'button[role="tab"][aria-label*="{chinese_name}"]',
                f'button[role="tab"] div:has-text("{chinese_name}")',
                f'button[id="{tab_name}-filter"]',
                f'button[aria-controls="chat-list"] div:has-text("{chinese_name}")'
            ]
            
            tab_element = None
            for selector in tab_selectors:
                try:
                    tab_element = await self.page.wait_for_selector(selector, timeout=2000)
                    if tab_element:
                        logger.debug(f"Found tab element with selector: {selector}")
                        break
                except:
                    continue
            
            if not tab_element:
                result["error"] = f"Tab '{chinese_name}' not found"
                logger.error(f"Could not find tab: {chinese_name}")
                return result
            
            # 检查tab是否已经是激活状态
            is_active = await tab_element.get_attribute("aria-pressed")
            if is_active == "true":
                logger.info(f"Tab '{chinese_name}' is already active")
                result["success"] = True
                return result
            
            # 模拟人类点击行为
            await tab_element.click()
            await self.page.wait_for_timeout(random.randint(100, 300))
            result["success"] = True
            
        except Exception as e:
            error_msg = f"Error clicking tab '{tab_name}': {e}"
            result["error"] = error_msg
            logger.error(error_msg)
        
        return result


class WhatsAppMonitor:
    """WhatsApp Web monitoring class that supports timed execution of multiple tasks"""
    
    def __init__(self, whatsapp_client: WhatsAppBrowserClient, interval_seconds: int = 10):
        self.whatsapp_client = whatsapp_client
        self.interval_seconds = interval_seconds
        self.tasks: Dict[str, MonitorTask] = {}
        self.is_running = False
        self.current_task = None
        self._task_lock = asyncio.Lock()
        
        # Register default tasks
        self._register_default_tasks()
        
        self.disable_task("auto_reply_message")
        
    def _register_default_tasks(self):
        """Register default monitoring tasks"""
        # Screenshot task
        # self.add_task(
        #     TaskType.SCREENSHOT,
        #     self.whatsapp_client.take_screenshot,
        #     "screenshot_task",
        # )
        
        # Message check task
        self.add_task(
            TaskType.AUTO_REPLY,
            self.whatsapp_client.auto_reply_message,
            "auto_reply_message"
        )
    
    def add_task(
        self,
        task_type: TaskType,
        task_func: Callable,
        name: str,
        enabled: bool = True,
        **kwargs
    ):
        """Add monitoring task"""
        task = MonitorTask(task_type, task_func, name, enabled, **kwargs)
        self.tasks[name] = task
        
    def enable_task(self, task_name: str):
        """Enable task"""
        if task_name in self.tasks:
            self.tasks[task_name].enabled = True
            
    def disable_task(self, task_name: str):
        """Disable task"""
        if task_name in self.tasks:
            self.tasks[task_name].enabled = False
            
    def remove_task(self, task_name: str):
        """Remove task"""
        if task_name in self.tasks:
            del self.tasks[task_name]
            
    async def _execute_tasks(self) -> Dict[str, Any]:
        """执行所有启用的任务"""
        results = {}
        execution_start = datetime.now()
        
        async with self._task_lock:
            for task_name, task in self.tasks.items():
                if not task.enabled:
                    # logger.debug(f"Skipping disabled task: {task_name}")
                    continue
                    
                try:
                    self.current_task = task_name
                    
                    task_start = datetime.now()
                    
                    # 执行任务
                    if task.kwargs:
                        result = await task.task_func(**task.kwargs)
                    else:
                        result = await task.task_func()
                    
                    task_duration = (datetime.now() - task_start).total_seconds()
                    
                    # 更新任务统计
                    task.last_execution = datetime.now()
                    task.execution_count += 1
                    
                    results[task_name] = {
                        'status': 'success',
                        'result': result,
                        'duration': task_duration
                    }
                    
                except Exception as e:
                    task.error_count += 1
                    results[task_name] = {
                        'status': 'error',
                        'error': str(e),
                        'duration': 0
                    }
                    logger.error(f"Task {task_name} failed: {e}", exc_info=True)
                    
                finally:
                    self.current_task = None
        
        total_duration = (datetime.now() - execution_start).total_seconds()
        results['_execution_summary'] = {
            'total_duration': total_duration,
            'tasks_executed': len([r for r in results.values() if isinstance(r, dict) and r.get('status') == 'success']),
            'tasks_failed': len([r for r in results.values() if isinstance(r, dict) and r.get('status') == 'error']),
            'execution_time': execution_start.isoformat()
        }
        
        return results
    
    async def start_monitoring(self):
        """开始监控"""
        if self.is_running:
            logger.warning("Monitor is already running")
            return
            
        self.is_running = True
        # 显示任务列表
        enabled_tasks = [name for name, task in self.tasks.items() if task.enabled]
        try:
            while self.is_running:
                cycle_start = datetime.now()
                
                # 执行所有任务
                results = await self._execute_tasks()
                # 计算下次执行时间
                execution_time = (datetime.now() - cycle_start).total_seconds()
                if execution_time < self.interval_seconds:
                    # 如果执行时间小于间隔时间，等待剩余时间
                    wait_time = self.interval_seconds - execution_time
                    await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Monitor error: {e}", exc_info=True)
        finally:
            self.is_running = False
    
    def stop_monitoring(self):
        """停止监控"""
        self.is_running = False
        logger.info("Monitor stop requested")
        
    def get_status(self) -> Dict[str, Any]:
        """获取监控状态"""
        return {
            'is_running': self.is_running,
            'current_task': self.current_task,
            'interval_seconds': self.interval_seconds,
            'total_tasks': len(self.tasks),
            'enabled_tasks': len([t for t in self.tasks.values() if t.enabled]),
            'tasks': {
                name: {
                    'type': task.task_type.value,
                    'enabled': task.enabled,
                    'execution_count': task.execution_count,
                    'error_count': task.error_count,
                    'last_execution': task.last_execution.isoformat() if task.last_execution else None
                }
                for name, task in self.tasks.items()
            }
        }




global_whatsapp_client = WhatsAppBrowserClient()