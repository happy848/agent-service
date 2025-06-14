"""
Browser client for WhatsApp Web screenshot capture using Playwright.
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List
from enum import Enum

from client.browser_client import BrowserManager

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
        
        # Navigate to WhatsApp Web
        await self.page.goto('https://web.whatsapp.com/')
        
        # Wait for the page to load
        await self.page.wait_for_load_state('networkidle')
        
        logger.info("Browser started and navigated to WhatsApp Web")
        if not self.browser_manager.headless:
            logger.info("Please scan the QR code if not already logged in...")
        else:
            logger.info("Note: Running in headless mode. QR code scanning requires pre-authenticated session.")
        
        # Auto start monitoring if enabled
        if self.auto_start_monitoring:
            await self._start_monitoring()
        
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
        
        # Start monitoring in background task
        self._monitoring_task = asyncio.create_task(self.monitor.start_monitoring())
        logger.info(f"Started WhatsApp monitoring with {self.monitor_interval}s interval")
    
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
        filename = f"{filename_prefix}_{timestamp}.png"
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
    
    async def check_new_messages(self) -> List[Dict[str, Any]]:
        """Check new messages"""
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        res = await self.click_unread_filter_and_process()

        logger.info(f"check_new_messages: {res}")

        return res
    
    async def click_unread_filter_and_process(self) -> Dict[str, Any]:
        """
        Click the unread filter button, check for unread messages, 
        click the first unread message and read its content.
        
        Returns:
            Dictionary with process results including unread messages and chat content
        """
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        result = {
            'unread_filter_clicked': False,
            'unread_messages_found': [],
            'first_chat_clicked': False,
            'chat_messages': [],
            'error': None
        }
        
        try:
            # Step 0: Check and click continue button if present
            logger.info("Checking for continue button...")
            try:
                # Look for continue button with various possible selectors (case insensitive)
                continue_selectors = [
                    'button:has-text("Continue")',
                    'button:has-text("continue")',
                    'button:has-text("CONTINUE")',
                    'button:has-text("继续")',
                    '[data-testid*="continue" i]',
                    '.continue-button',
                    'button[aria-label="Continue" i]',
                    'button[aria-label="continue" i]',
                    'button[aria-label="CONTINUE" i]',
                    'button[aria-label="继续"]'
                ]
                
                continue_button = None
                for selector in continue_selectors:
                    try:
                        continue_button = await self.page.wait_for_selector(selector, timeout=2000)
                        if continue_button:
                            logger.info(f"Found continue button with selector: {selector}")
                            break
                    except:
                        continue
                
                if continue_button:
                    await continue_button.click()
                    await self.page.wait_for_timeout(1000)  # Wait for page to process
                    logger.info("Clicked continue button")
                else:
                    logger.info("No continue button found, proceeding...")
                    
            except Exception as e:
                logger.warning(f"Error checking for continue button: {e}")
            
            # Step 1: Click the unread filter button
            logger.info("Looking for unread filter button...")
            unread_filter_button = await self.page.wait_for_selector('#unread-filter', timeout=1000)
            
            if unread_filter_button:
                await unread_filter_button.click()
                await self.page.wait_for_timeout(1000)  # Wait for filter to apply
                result['unread_filter_clicked'] = True
                logger.info("Clicked unread filter button")
            else:
                logger.warning("Unread filter button not found, pls login first")
                result['error'] = "Unread filter button not found, pls login first"
                return result
            
            # Step 2: Check for unread messages using updated selector strategy
            logger.info("Checking for unread messages...")
            unread_messages = await self._get_unread_messages_from_filtered_list()
            result['unread_messages_found'] = unread_messages
            
            if not unread_messages:
                logger.info("No unread messages found")
                result['error'] = "No unread messages found"
                return result
            
            logger.info(f"Found {len(unread_messages)} unread chats")
            
            # Step 3: Click the first unread message
            first_unread = unread_messages[0]
            logger.info(f"Clicking first unread chat: {first_unread.get('contact_name', 'Unknown')}")
            
            # Find and click the first chat with unread messages
            first_chat_element = await self._find_and_click_first_unread_chat()
            
            if first_chat_element:
                result['first_chat_clicked'] = True
                logger.info("Successfully clicked first unread chat")
                
                # Wait for chat to load
                await self.page.wait_for_timeout(3000)
                
                # Step 4: Read chat messages
                logger.info("Reading chat messages...")
                chat_messages = await self.get_chat_messages()
                result['chat_messages'] = chat_messages
                
                logger.info(f"Successfully read {len(chat_messages)} messages from chat")
                
            else:
                result['error'] = "Failed to click first unread chat"
                logger.error("Failed to click first unread chat")
                
        except Exception as e:
            error_msg = f"Error in click_unread_filter_and_process: {e}"
            logger.error(error_msg)
            result['error'] = error_msg
            
        return result
    
    async def _get_unread_messages_from_filtered_list(self) -> List[Dict[str, Any]]:
        """
        Get unread messages from the filtered chat list.
        This method works after the unread filter has been applied.
        """
        messages = []
        
        try:
            # Wait for chat list to be visible
            await self.page.wait_for_selector('[aria-label="对话列表"], [aria-label="Chat list"]', timeout=5000)
            
            # Find all chat items in the list
            chat_items = await self.page.query_selector_all('[role="listitem"]')
            
            for chat_item in chat_items:
                try:
                    # Look for unread message count indicator
                    unread_indicator = await chat_item.query_selector('[aria-label*="未读消息"], [aria-label*="unread message"]')
                    
                    if unread_indicator:
                        # Extract unread count from aria-label
                        aria_label = await unread_indicator.get_attribute('aria-label')
                        unread_count = re.search(r'(\d+)', aria_label).group(1) if re.search(r'(\d+)', aria_label) else "1"
                        
                        # Get contact name
                        name_element = await chat_item.query_selector('[dir="auto"]')
                        contact_name = await name_element.text_content() if name_element else "Unknown"
                        
                        # Get last message preview
                        message_elements = await chat_item.query_selector_all('span[dir="auto"], span[dir="ltr"]')
                        last_message = ""
                        for msg_elem in message_elements:
                            text = await msg_elem.text_content()
                            # Skip contact name and time, get actual message content
                            if text and text != contact_name and not re.match(r'^\d{2}:\d{2}$', text.strip()):
                                last_message = text.strip()
                                break
                        
                        messages.append({
                            'contact_name': contact_name.strip(),
                            'unread_count': unread_count,
                            'last_message_preview': last_message,
                            'timestamp': datetime.now().isoformat(),
                            'chat_element': chat_item  # Store element reference for clicking
                        })
                        
                except Exception as e:
                    logger.warning(f"Error extracting chat info: {e}")
                    continue
            
            return messages
            
        except Exception as e:
            logger.error(f"Error getting unread messages from filtered list: {e}")
            return []
    
    async def _find_and_click_first_unread_chat(self) -> bool:
        """
        Find and click the first chat with unread messages.
        
        Returns:
            True if successfully clicked, False otherwise
        """
        try:
            # Find the first chat item with unread messages
            first_chat_with_unread = await self.page.query_selector('[role="listitem"]:has([aria-label*="未读消息"]), [role="listitem"]:has([aria-label*="unread message"])')
            
            if first_chat_with_unread:
                # Find the clickable button within the chat item
                chat_button = await first_chat_with_unread.query_selector('[role="button"]')
                
                if chat_button:
                    await chat_button.click()
                    return True
                else:
                    # If no button found, try clicking the chat item directly
                    await first_chat_with_unread.click()
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error finding and clicking first unread chat: {e}")
            return False
    
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
        
    def _register_default_tasks(self):
        """Register default monitoring tasks"""
        # Screenshot task
        self.add_task(
            TaskType.SCREENSHOT,
            self.whatsapp_client.take_screenshot,
            "screenshot_task",
        )
        
        # Message check task
        self.add_task(
            TaskType.MESSAGE_CHECK,
            self.whatsapp_client.check_new_messages,
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
