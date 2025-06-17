"""
Browser client for WhatsApp Web screenshot capture using Playwright.
所有操作必须模拟真人操作，随机的停顿，鼠标位置的移动和点击
每次点击的时候鼠标先移动到该位置，然后点击，记录当前位置，下次点击的时候这个位置作为移动的起始位置
鼠标移动的曲线模拟人类正常使用Windows 浏览器的移动曲线，同时加上随机时间
"""

import asyncio
import logging
import os
import random
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List
from enum import Enum
from functools import wraps

from client.browser_client import BrowserManager
from agents import get_agent
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from playwright.async_api import Page


# Configure logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Add console handler if not already present
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

class TaskType(Enum):
    """Task type enumeration"""
    SCREENSHOT = "screenshot"
    MESSAGE_CHECK = "message_check"
    CUSTOM = "custom"

def can_auto_replay_contact_message(contact_name: str) -> bool:
    """
    判断是否可以自动回复联系人的消息
    """
    return contact_name in ['ben-service@agentsben.com']

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
            # 执行前截图
            await self.take_screenshot(f"{method_name}_before")
            
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
        auto_start_monitoring: bool = True, 
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
        self.auto_start_monitoring = auto_start_monitoring
        self.monitor_interval = monitor_interval
        self.monitor: Optional['WhatsAppMonitor'] = None
        self._monitoring_task: Optional[asyncio.Task] = None
        self.customer_service_agent = get_agent("customer-service")
        
        # Continue button check tracking
        self._continue_check_attempts = 0
        self._max_continue_attempts = 3
        self._skip_continue_check = False
        
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
        await self.browser_manager.start()
        
        # Create new page with anti-detection
        self.page = await self.browser_manager.new_page()
        
        
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
            logger.info("Already on WhatsApp Web")
            return self.page
        
        logger.info("Browser started and navigated to WhatsApp Web")
        if not self.browser_manager.headless:
            logger.info("Please scan the QR code if not already logged in...")
        else:
            logger.info("Note: Running in headless mode. QR code scanning requires pre-authenticated session.")
        
        await self.page.wait_for_timeout(5000)

        # Auto start monitoring if enabled
        # if self.auto_start_monitoring:
        #     await self._start_monitoring()
        
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

    # async def _start_monitoring(self):
    #     """Start monitoring tasks in background."""
    #     if self.monitor is None:
    #         self.monitor = WhatsAppMonitor(self, self.monitor_interval)
    #     # Start monitoring in background task
    #     self._monitoring_task = asyncio.create_task(self.monitor.start_monitoring())
    #     logger.info(f"Started WhatsApp monitoring with {self.monitor_interval}s interval")
    
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
        
    async def check_unread_messages(self) -> List[Dict[str, Any]]:
        """Check new messages"""
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        unread_contact_messages = await self.get_unread_contact_message()
        
        for contact_message in unread_contact_messages:
            if can_auto_replay_contact_message(contact_message['contact_name']):
                # Use the new customer service agent to handle messages
                reply = await self.handle_customer_message(contact_message)
                if reply['success']:
                    # Send each message separately
                    messages_to_send = reply['ai_reply_message']
                    if isinstance(messages_to_send, str):
                        messages_to_send = [messages_to_send]
                    
                    for message in messages_to_send:
                        await self.send_message_to_contact(contact_message['contact_name'], message)
                        # Add a small delay between messages
                        await self.page.wait_for_timeout(random.randint(500, 1500))
                else:
                    logger.error(f"Failed to generate reply: {reply['error']}")
        
        logger.info(f"check_unread_messages: {unread_contact_messages}")

        return unread_contact_messages
            
    async def take_screenshot(self, filename_prefix: str = "wa") -> str:
        """Take a screenshot and save it with timestamp."""
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
            
        # Ensure images directory exists
        images_dir = Path("/app/docker/logs")
        images_dir.mkdir(exist_ok=True)
        
        # Generate filename with UTC+8 timestamp in readable format
        utc_plus_8 = timezone(timedelta(hours=8))
        timestamp = datetime.now(utc_plus_8).strftime("%Y-%m-%d %H:%M:%S")
        filename = f"wa_{timestamp}_{filename_prefix}.png"
        filepath = images_dir / filename
        
        # Take screenshot
        await self.page.screenshot(path=str(filepath), full_page=True)
        
        logger.info(f"Screenshot saved: {filepath}")
        
        # Clean up old screenshots (delete files older than 10 minutes)
        await self._cleanup_old_screenshots(images_dir, filename_prefix)
        
        return str(filepath)
    
    async def _cleanup_old_screenshots(self, images_dir: Path, filename_prefix: str = "wa"):
        """Clean up screenshot files older than 10 minutes."""
        try:
            current_time = datetime.now()
            cutoff_time = current_time.timestamp() - 600  # 10 minutes = 600 seconds
            
            deleted_count = 0
            
            # Find all screenshot files with the given prefix
            pattern = f"{filename_prefix}_*.png"
            screenshot_files = list(images_dir.glob(pattern))
            
            for file_path in screenshot_files:
                try:
                    # Get file creation time
                    file_stat = file_path.stat()
                    file_creation_time = file_stat.st_mtime  # Use modification time as creation time
                    
                    # Check if file is older than 10 minutes
                    if file_creation_time < cutoff_time:
                        file_path.unlink()  # Delete the file
                        deleted_count += 1
                        logger.debug(f"Deleted old screenshot: {file_path.name}")
                        
                except Exception as e:
                    logger.warning(f"Failed to delete old screenshot {file_path.name}: {e}")
                    
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old screenshot(s)")
            else:
                logger.debug("No old screenshots to clean up")
                
        except Exception as e:
            logger.error(f"Error during screenshot cleanup: {e}")
    
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
                logger.info(f"Target contact is already active: {current_contact_name}")
                result["success"] = True
                result["contact_found"] = True
                result["method_used"] = "current_chat"
                result["contact_name"] = current_contact_name
                return result
            
            # Step 2: Try to find contact in chat list
            contact_info = await self._find_contact_in_chat_list(target_contact)
            if contact_info:
                logger.info(f"Found contact in chat list: {contact_info['name']}")
                result["method_used"] = "chat_list"
                result["contact_name"] = contact_info['name']
            else:
                # Step 3: Search for contact
                search_result = await self._search_and_select_contact(target_contact)
                if search_result["success"]:
                    logger.info(f"Found contact through search: {search_result['contact_name']}")
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
        
    @screenshot_state_recorder
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
        
    async def get_chat_messages(self) -> List[Dict[str, Any]]:
        """
        Get structured chat messages from current chat conversation.
        
        Returns:
            List of message dictionaries with structure:
            [
                {
                    "type": "received" or "sent",
                    "sender": "contact_name or phone",
                    "content": "message content",
                    "datetime": "HH:MM, YYYY-MM-DD",
                    "timestamp": "ISO format timestamp"
                }
            ]
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        try:
            # Wait for chat messages to load
            await self.page.wait_for_selector('#main', timeout=3000)
            
            messages = []
            
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
    
    async def _parse_message_element(self, element) -> Optional[Dict[str, Any]]:
        """
        Parse individual message element to extract message data.
        
        Args:
            element: Playwright element for message
            
        Returns:
            Dictionary with message data or None if parsing fails
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
            content_elements = await element.query_selector_all('._ao3e.selectable-text.copyable-text')
            
            content_parts = []
            for content_element in content_elements:
                text = await content_element.text_content()
                if text and text.strip():
                    # Skip time stamps (usually short and contain only numbers/colons)
                    if not re.match(r'^\d{1,2}:\d{2}$', text.strip()):
                        content_parts.append(text.strip())
            
            content = ' '.join(content_parts).strip()
            
            # Get datetime
            datetime_str = ""
            timestamp = datetime.now().isoformat()
            
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
                        current_date = datetime.now().strftime("%Y-%m-%d")
                        datetime_str = f"{time_text.strip()}, {current_date}"
                        break
            
            # Skip if no content found
            if not content:
                return None
            
            return {
                "type": message_type,
                "sender": sender,
                "content": content,
                "datetime": datetime_str,
                "timestamp": timestamp
            }
            
        except Exception as e:
            logger.warning(f"Error parsing message element: {e}")
            return None

    @screenshot_state_recorder
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

    def _is_contact_match(self, current_contact: str, target_contact: str) -> bool:
        """
        Check if two contact names match (case insensitive, with some fuzzy matching).
        
        Args:
            current_contact: Current contact name
            target_contact: Target contact name
            
        Returns:
            True if contacts match, False otherwise
        """
        logger.info(f"--------------------------------")
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
            
            await self.take_screenshot()
            await self._save_conversation_html_to_log()
            return None
            
        except Exception as e:
            logger.error(f"Error finding contact in chat list: {e}")
            
            await self.take_screenshot()
            await self._save_conversation_html_to_log()
            
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
            await self.page.wait_for_selector('#main', timeout=5000)
            
            # Find the message input field using multiple possible selectors
            input_selectors = [
                '[aria-label="输入消息"]',
                '[aria-label="Type a message"]',
                '[contenteditable="true"][role="textbox"]',
            ]
            
            input_element = None
            for selector in input_selectors:
                try:
                    input_element = await self.page.wait_for_selector(selector, timeout=2000)
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
            await self._save_conversation_html_to_log()

            result["success"] = True
            logger.info(f"Successfully sent message: {message}")

        except Exception as e:
            error_msg = f"Error sending message: {e}"
            result["error"] = error_msg
            logger.error(error_msg)
        
        return result
  
    async def _save_conversation_html_to_log(self):
        """
        Save conversation list HTML content to log file for debugging.
        """
        try:
            # Ensure logs directory exists
            logs_dir = Path("/app/docker/logs")
            logs_dir.mkdir(exist_ok=True)
            # Generate filename with timestamp
            utc_plus_8 = timezone(timedelta(hours=8))
            timestamp = datetime.now(utc_plus_8).strftime("%Y-%m-%d_%H-%M-%S")

            html_content = await self.page.html()
            
            # Save HTML file
            html_filename = f"whatsapp_debug_{timestamp}.html"
            html_filepath = logs_dir / html_filename
            
            with open(html_filepath, 'w', encoding='utf-8') as f:
                f.write(html_content)
            
            logger.info(f"Saved conversation HTML debug file: {html_filepath}")
            
            await self._cleanup_old_debug_files(logs_dir)
            
        except Exception as e:
            logger.error(f"Error saving conversation HTML to log: {e}")
    
    async def _cleanup_old_debug_files(self, logs_dir: Path):
        """Clean up debug HTML files older than 1 hour."""
        try:
            current_time = datetime.now()
            cutoff_time = current_time.timestamp() - 3600  # 1 hour = 3600 seconds
            
            deleted_count = 0
            
            # Find all debug HTML files
            debug_patterns = ["whatsapp_debug_*.html", "conversation_list_raw_*.html"]
            
            for pattern in debug_patterns:
                debug_files = list(logs_dir.glob(pattern))
                
                for file_path in debug_files:
                    try:
                        file_stat = file_path.stat()
                        file_creation_time = file_stat.st_mtime
                        
                        if file_creation_time < cutoff_time:
                            file_path.unlink()
                            deleted_count += 1
                            logger.debug(f"Deleted old debug file: {file_path.name}")
                            
                    except Exception as e:
                        logger.warning(f"Failed to delete old debug file {file_path.name}: {e}")
                        
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old debug file(s)")
                
        except Exception as e:
            logger.error(f"Error during debug file cleanup: {e}")

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
                target_element = await self.page.wait_for_selector(element_or_selector, timeout=5000)
                if not target_element:
                    logger.error(f"Element not found with selector: {element_or_selector}")
                    return False
            else:
                # 如果是元素对象，直接使用
                target_element = element_or_selector
            
            # 点击元素获得焦点
            await target_element.click()
            await self.page.wait_for_timeout(random.randint(200, 500))

            # 清空现有内容
            if clear_first:
                await self.page.keyboard.press('Control+a')
                await self.page.wait_for_timeout(random.randint(50, 150))
                await self.page.keyboard.press('Backspace')
                await self.page.wait_for_timeout(random.randint(100, 200))
            
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
                        thinking_pause = random.randint(300, 800)
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

    @screenshot_state_recorder
    async def get_unread_messages(self) -> List[Dict[str, Any]]:
        """
        Get unread contact messages, excluding muted conversations.
        
        Returns:
            List of unread message dictionaries with structure:
            [
                {
                    "contact_name": "contact name",
                    "unread_count": "number of unread messages",
                    "last_message_preview": "preview of last message",
                    "timestamp": "ISO format timestamp",
                    "is_muted": False,
                }
            ]
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        unread_messages = []
        
        try:
            await self.goto_whatsapp_web()
            # Wait for chat list to be visible
            await self.page.wait_for_selector('[aria-label="对话列表"], [aria-label="Chat list"]', timeout=5000)
            
            unread_items = await self.page.query_selector_all('[aria-label*="未读消息"], [aria-label*="unread message"]')
            
            if not unread_items:
                return unread_messages
            
            for unread_indicator in unread_items:
                try:
                    # Check if this conversation is muted
                    muted_indicator = await unread_indicator.query_selector('[aria-label="已静音的对话"], [aria-label*="muted"]')
                    
                    if muted_indicator:
                        logger.debug("Skipping muted conversation")
                        continue  # Skip muted conversations
                    
                    # Extract unread count from aria-label
                    aria_label = await unread_indicator.get_attribute('aria-label')
                    unread_count = "1"  # Default to 1 if can't parse
                    if aria_label:
                        count_match = re.search(r'(\d+)', aria_label)
                        if count_match:
                            unread_count = count_match.group(1)
                    
                    # Find the parent listitem that contains this unread indicator
                    chat_item = await unread_indicator.evaluate_handle('''
                        (element) => {
                            let current = element;
                            while (current && current.parentElement) {
                                current = current.parentElement;
                                if (current.getAttribute('role') === 'listitem') {
                                    return current;
                                }
                            }
                            return null;
                        }
                    ''')
                    
                    if not chat_item:
                        logger.warning("Could not find parent listitem for unread indicator")
                        continue
                    
                    # Get contact name from title attribute or text content
                    contact_name = "Unknown"
                    
                    name_elements = await chat_item.query_selector_all('span[dir="auto"]')
                    for name_elem in name_elements:
                        text = await name_elem.text_content()
                        if text and text.strip() and not re.match(r'^\d{1,2}:\d{2}$', text.strip()):
                            contact_name = text.strip()
                            break
                    
                    # Look for message preview in the message area
                    message_preview_container = await chat_item.text_content()
                  
                    # Create unread message entry
                    unread_entry = {
                        "contact_name": contact_name,
                        "unread_count": unread_count,
                        "message_preview_container": message_preview_container,
                        "is_muted": False,  # We already filtered out muted ones
                    }
                    
                    unread_messages.append(unread_entry)
                    logger.info(f"Found unread message from {contact_name}: {unread_count} messages")
                    
                except Exception as e:
                    logger.warning(f"Error extracting chat info from item: {e}")
                    continue
            
            logger.info(f"Found {len(unread_messages)} unread conversations (excluding muted)")
            return unread_messages
            
        except Exception as e:
            logger.error(f"Error getting unread contact messages: {e}")
            return []

    async def handle_customer_message(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle a customer message using the customer service agent.
        
        Args:
            message_data: Dictionary containing message information
            
        Returns:
            Dictionary with response information:
            {
                "success": bool,
                "ai_reply_message": str or List[str],  # Can be a single message or list of messages
                "error": Optional[str]
            }
        """
        try:
            # Get chat history for the contact
            chat_history = message_data
            
            # Convert chat history to messages
            messages = []
            for msg in chat_history:
                # Add system message with chat context
                if len(messages) == 0:
                    context = f"""This is a conversation with {message_data['contact_name']}.
                    Previous messages are shown below with their timestamps.
                    The current time is {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')}."""
                    messages.append(SystemMessage(content=context))
                
                # Convert each message to appropriate format
                content = f"[{msg['datetime']}] {msg['content']}"
                if msg['type'] == 'received':
                    messages.append(HumanMessage(content=content))
                else:
                    messages.append(AIMessage(content=content))
            
            # Add the current message
            current_msg = f"[{message_data['datetime']}] {message_data['content']}"
            messages.append(HumanMessage(content=current_msg))
            
            # Initialize agent state
            agent_state = {
                "messages": messages
            }
            
            # Run the agent
            result = await self.customer_service_agent.ainvoke(
                agent_state
            )
            
            # Extract and split the response
            if result and result.get("messages"):
                last_message = result["messages"][-1]
                # Split messages by the \n\n delimiter and clean up any escaped quotes
                messages_to_send = [msg.strip().strip('"') for msg in last_message.content.split("\n\n") if msg.strip()]
                logger.info(f"Messages to send: {messages_to_send}")
                return {
                    "success": True,
                    "ai_reply_message": messages_to_send,
                    "error": None
                }
            else:
                return {
                    "success": False,
                    "ai_reply_message": [],
                    "error": "No response generated"
                }
                
        except Exception as e:
            logger.error(f"Error handling customer message: {e}", exc_info=True)
            return {
                "success": False,
                "ai_reply_message": [],
                "error": str(e)
            }

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
        
    async def _register_default_tasks(self):
        """Register default monitoring tasks"""
        # Screenshot task
        # self.add_task(
        #     TaskType.SCREENSHOT,
        #     self.whatsapp_client.take_screenshot,
        #     "screenshot_task",
        # )
        
        await self.whatsapp_client.get_unread_messages()
        
        # Message check task
        self.add_task(
            TaskType.MESSAGE_CHECK,
            self.whatsapp_client.check_unread_messages,
            "message_check_task"
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
        logger.info(f"Added task: {name} ({task_type.value})")
        
    def enable_task(self, task_name: str):
        """Enable task"""
        if task_name in self.tasks:
            self.tasks[task_name].enabled = True
            logger.info(f"Enabled task: {task_name}")
            
    def disable_task(self, task_name: str):
        """Disable task"""
        if task_name in self.tasks:
            self.tasks[task_name].enabled = False
            logger.info(f"Disabled task: {task_name}")
            
    def remove_task(self, task_name: str):
        """Remove task"""
        if task_name in self.tasks:
            del self.tasks[task_name]
            logger.info(f"Removed task: {task_name}")
            
    async def _execute_tasks(self) -> Dict[str, Any]:
        """执行所有启用的任务"""
        results = {}
        execution_start = datetime.now()
        
        async with self._task_lock:
            for task_name, task in self.tasks.items():
                if not task.enabled:
                    continue
                    
                try:
                    self.current_task = task_name
                    logger.debug(f"Executing task: {task_name}")
                    
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
                    
                    logger.debug(f"Task {task_name} completed in {task_duration:.2f}s")
                    
                except Exception as e:
                    task.error_count += 1
                    results[task_name] = {
                        'status': 'error',
                        'error': str(e),
                        'duration': 0
                    }
                    logger.error(f"Task {task_name} failed: {e}")
                    
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
        logger.info(f"Starting WhatsApp monitoring with {len(self.tasks)} tasks, interval: {self.interval_seconds}s")
        
        # 显示任务列表
        enabled_tasks = [name for name, task in self.tasks.items() if task.enabled]
        logger.info(f"Enabled tasks: {', '.join(enabled_tasks)}")
        
        try:
            while self.is_running:
                cycle_start = datetime.now()
                
                # 执行所有任务
                results = await self._execute_tasks()
                summary = results.get('_execution_summary', {})
                
                logger.info(
                    f"Monitor cycle completed: "
                    f"{summary.get('tasks_executed', 0)} succeeded, "
                    f"{summary.get('tasks_failed', 0)} failed, "
                    f"took {summary.get('total_duration', 0):.2f}s"
                )
                
                # 计算下次执行时间
                execution_time = (datetime.now() - cycle_start).total_seconds()
                
                if execution_time < self.interval_seconds:
                    # 如果执行时间小于间隔时间，等待剩余时间
                    wait_time = self.interval_seconds - execution_time
                    logger.debug(f"Waiting {wait_time:.2f}s until next cycle")
                    await asyncio.sleep(wait_time)
                else:
                    # 如果执行时间超过间隔时间，立即开始下一轮
                    logger.warning(f"Task execution took {execution_time:.2f}s (longer than interval {self.interval_seconds}s)")
                    
        except KeyboardInterrupt:
            logger.info("Monitor stopped by user")
        except Exception as e:
            logger.error(f"Monitor error: {e}")
        finally:
            self.is_running = False
            logger.info("Monitor stopped")
    
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
