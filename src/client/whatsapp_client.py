"""
Browser client for WhatsApp Web screenshot capture using Playwright.
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

from client.browser_client import BrowserManager

from playwright.async_api import Page

from client.whatsapp_messages_handler import get_whatsapp_message_handler


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
        
        # Continue button check tracking
        self._continue_check_attempts = 0
        self._max_continue_attempts = 3
        self._skip_continue_check = False
        
        # Unread filter state tracking
        self._unread_filter_active = False
        
        self.whatsapp_message_handler = get_whatsapp_message_handler(self)
        
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
        
        # Reset filter state when page loads
        self.reset_filter_state()
        
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
        
    def reset_filter_state(self):
        """Reset the unread filter state (useful when page is refreshed or reloaded)."""
        self._unread_filter_active = False
        logger.info("Reset unread filter state to inactive")
        
    def get_filter_state(self) -> bool:
        """Get current tracked filter state."""
        return self._unread_filter_active
    
    
    async def check_new_messages(self) -> List[Dict[str, Any]]:
        """Check new messages"""
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
        
        res = await self.click_unread_filter_and_process()
        
        if res['unread_messages_found']:
            reply = await self.whatsapp_message_handler.generate_ai_customer_reply(res['chat_messages'])
            if reply['success']:
                await self.send_message(reply['ai_reply_message'])

        logger.info(f"check_new_messages: {res}")

        return res
    
    
            
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
    
    async def _check_unread_filter_state(self, filter_element) -> bool:
        """
        Check if the unread filter button is currently active/pressed.
        
        Args:
            filter_element: The unread filter button element
            
        Returns:
            True if filter is active, False otherwise
        """
        try:
            # Check common attributes that indicate active state
            
            # Method 1: Check aria-pressed attribute
            aria_pressed = await filter_element.get_attribute('aria-pressed')
            if aria_pressed == 'true':
                logger.debug("Filter is active (aria-pressed=true)")
                return True
            
            # Method 2: Check if element has active/selected class
            class_list = await filter_element.get_attribute('class')
            if class_list:
                active_indicators = ['active', 'selected', 'pressed', 'checked', 'on']
                for indicator in active_indicators:
                    if indicator in class_list.lower():
                        logger.debug(f"Filter is active (found '{indicator}' in class)")
                        return True
            
            # Method 3: Check data attributes
            data_active = await filter_element.get_attribute('data-active')
            if data_active == 'true':
                logger.debug("Filter is active (data-active=true)")
                return True
            
            # Method 4: Check if button has different styling (background color, etc.)
            # This would require getting computed styles, which is more complex
            
            logger.debug("Filter appears to be inactive")
            return False
            
        except Exception as e:
            logger.warning(f"Error checking filter state: {e}")
            # If we can't determine state, assume it's inactive and allow click
            return False
    
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
            'unread_filter_already_active': self._unread_filter_active,
            'unread_messages_found': [],
            'first_chat_clicked': False,
            'chat_messages': [],
            'error': None
        }
        
        try:
            # Step 0: Check and click continue button if present (max 3 attempts)
            if not self._skip_continue_check and self._continue_check_attempts < self._max_continue_attempts:
                logger.info(f"Checking for continue button... (attempt {self._continue_check_attempts + 1}/{self._max_continue_attempts})")
                try:
                    # Look for continue button with various possible selectors (case insensitive)
                    continue_selectors = [
                        'button:has-text("Continue")',
                        'button:has-text("continue")',
                        'button:has-text("继续")',
                        '[data-testid*="continue" i]',
                        '.continue-button',
                        'button[aria-label="Continue" i]',
                        'button[aria-label="continue" i]',
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
                    
                    self._continue_check_attempts += 1
                    
                    if continue_button:
                        await continue_button.click()
                        await self.page.wait_for_timeout(1000)  # Wait for page to process
                        logger.info("Clicked continue button")
                        # Reset attempts counter since we found and clicked the button
                        self._continue_check_attempts = 0
                    else:
                        logger.info(f"No continue button found on attempt {self._continue_check_attempts}")
                        # If we've reached max attempts, skip future checks
                        if self._continue_check_attempts >= self._max_continue_attempts:
                            self._skip_continue_check = True
                            logger.info("Reached max continue button check attempts, will skip future checks")
                        
                except Exception as e:
                    self._continue_check_attempts += 1
                    logger.warning(f"Error checking for continue button (attempt {self._continue_check_attempts}): {e}")
                    # If we've reached max attempts, skip future checks
                    if self._continue_check_attempts >= self._max_continue_attempts:
                        self._skip_continue_check = True
                        logger.info("Reached max continue button check attempts due to errors, will skip future checks")
            elif self._skip_continue_check:
                logger.debug("Skipping continue button check (max attempts reached)")
            else:
                logger.debug("Continue button check disabled")
            
                        # Step 1: Click the unread filter button (only if not already active)
            logger.info("Checking unread filter button state...")
            unread_filter_button = await self.page.wait_for_selector('#unread-filter', timeout=1000)
            
            if unread_filter_button:
                # Check if filter is already active
                current_filter_state = await self._check_unread_filter_state(unread_filter_button)
                
                if current_filter_state and self._unread_filter_active:
                    logger.info("Unread filter is already active, skipping click")
                    result['unread_filter_clicked'] = False
                    result['unread_filter_already_active'] = True
                else:
                    await unread_filter_button.click()
                    await self.page.wait_for_timeout(3000)  # Wait for filter to apply
                    self._unread_filter_active = True
                    result['unread_filter_clicked'] = True
                    result['unread_filter_already_active'] = False
                    logger.info("Clicked unread filter button and updated state")
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
            
            
            
            
            # unread_messages
            
            
            
            
            
            # Step 3: Click the first unread message
            first_unread = unread_messages[0]
            logger.info(f"Clicking first unread chat: {first_unread.get('contact_name', 'Unknown')}")
            
            
            # 调试，只处理AgentsBen的消息
            if (first_unread['contact_name'] != 'AgentsBen'):
                return result

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

    async def send_message(self, message: str) -> Dict[str, Any]:
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
                '.lexical-rich-text-input [contenteditable="true"]',
                'footer [contenteditable="true"]'
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
            
            # Click on the input field to focus it
            await input_element.click()
            await self.page.wait_for_timeout(500)
            
            # Clear existing content in the input field
            await input_element.evaluate('element => element.innerHTML = ""')
            
            # Find the paragraph element inside the input for typing
            p_element = await input_element.query_selector('p')
            if not p_element:
                # If no p element exists, create one
                await input_element.evaluate('''
                    element => {
                        element.innerHTML = '<p class="selectable-text copyable-text x15bjb6t x1n2onr6" style="text-indent: 0px; margin-top: 0px; margin-bottom: 0px;"></p>';
                    }
                ''')
                p_element = await input_element.query_selector('p')
            
            if p_element:
                # Advanced anti-detection input method
                await self._send_message_with_anti_detection(p_element, message)
                
                logger.info(f"Sent message: {message}")
                
                # Wait for message to be sent
                await self.page.wait_for_timeout(random.randint(2000, 3000))

                # Save conversation HTML for debugging
                await self._save_conversation_html_to_log()

                result["success"] = True
                logger.info(f"Successfully sent message: {message}")
                
            else:
                result["error"] = "Could not find or create paragraph element in input field"
                logger.error("Could not find paragraph element for typing")

        except Exception as e:
            error_msg = f"Error sending message: {e}"
            result["error"] = error_msg
            logger.error(error_msg)
        
        return result
    
    async def _send_message_with_anti_detection(self, p_element, message: str):
        """
        Advanced message sending method to avoid automation detection.
        Uses keyboard typing with random intervals to simulate human behavior.
        """
        try:
            # Strategy 1: Focus and clear with multiple methods
            await p_element.click()
            await self.page.wait_for_timeout(random.randint(100, 300))
            
            # Clear existing content
            await self.page.keyboard.press('Control+a')
            await self.page.wait_for_timeout(random.randint(50, 150))
            await self.page.keyboard.press('Delete')
            await self.page.wait_for_timeout(random.randint(100, 200))
            
            # Strategy 2: Use keyboard.type() with random intervals between characters
            # Split message into individual characters for more natural typing
            for i, char in enumerate(message):
                # Type each character
                await self.page.keyboard.type(char)
                
                # Add random interval between characters (30-150ms)
                if i < len(message) - 1:  # Don't pause after last character
                    char_interval = random.randint(30, 150)
                    await self.page.wait_for_timeout(char_interval)
                    
                    # Occasionally add longer pauses to simulate thinking (5% chance)
                    if random.random() < 0.05:
                        thinking_pause = random.randint(300, 800)
                        await self.page.wait_for_timeout(thinking_pause)
            
            # Strategy 3: Random additional human-like behaviors
            if random.random() < 0.3:  # 30% chance to simulate backspace and retype
                await self.page.keyboard.press('Backspace')
                await self.page.wait_for_timeout(random.randint(50, 150))
                # Retype last character
                await self.page.keyboard.type(message[-1])
                await self.page.wait_for_timeout(random.randint(100, 200))
            
            # Strategy 4: Multiple ways to send message
            send_method = random.choice(['enter', 'click_send', 'shift_enter'])
            send_method = 'enter'
            
            if send_method == 'enter':
                # Standard Enter press with random timing
                await self.page.wait_for_timeout(random.randint(200, 800))
                await self.page.keyboard.press('Enter')
                
            elif send_method == 'click_send':
                # Try to click send button instead of Enter
                try:
                    send_button = await self.page.wait_for_selector(
                        '[aria-label="发送"], [aria-label="Send"], [data-testid="send"], button[aria-label*="send" i]', 
                        timeout=2000
                    )
                    if send_button:
                        await send_button.click()
                    else:
                        # Fallback to Enter
                        await self.page.keyboard.press('Enter')
                except:
                    # Fallback to Enter
                    await self.page.keyboard.press('Enter')
                    
            else:  # shift_enter fallback
                # Sometimes people use Shift+Enter then Enter
                if random.random() < 0.5:
                    await self.page.keyboard.press('Shift+Enter')
                    await self.page.wait_for_timeout(random.randint(100, 300))
                await self.page.keyboard.press('Enter')
            
            # Strategy 5: Post-send human behavior simulation
            post_send_behavior = random.choice(['scroll', 'click_away', 'wait'])
            
            if post_send_behavior == 'scroll':
                # Sometimes people scroll after sending
                await self.page.mouse.wheel(0, random.randint(-100, 100))
                await self.page.wait_for_timeout(random.randint(200, 500))
                
            elif post_send_behavior == 'click_away':
                # Click somewhere else then back to input
                try:
                    chat_area = await self.page.query_selector('#main')
                    if chat_area:
                        box = await chat_area.bounding_box()
                        if box:
                            await self.page.mouse.click(
                                box['x'] + box['width'] * 0.5,
                                box['y'] + box['height'] * 0.3
                            )
                            await self.page.wait_for_timeout(random.randint(100, 300))
                except:
                    pass
            
            # Always end with a small random wait
            await self.page.wait_for_timeout(random.randint(300, 700))
            
        except Exception as e:
            logger.error(f"Error in anti-detection send: {e}")
            # Fallback to simple Enter press
            await self.page.keyboard.press('Enter')
    
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

            # Get current chat HTML for additional context
            current_chat_html = ""
            try:
                main_chat = await self.page.query_selector('#main')
                if main_chat:
                    current_chat_html = await main_chat.inner_html()
                    logger.debug("Got current chat HTML")
            except Exception as e:
                logger.warning(f"Could not get current chat HTML: {e}")
                current_chat_html = f"<!-- Error getting current chat: {e} -->"
            
            # Get page URL
            page_url = self.page.url
            
            # Create HTML debug file
            html_content = current_chat_html
            
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
