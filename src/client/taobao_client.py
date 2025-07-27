import asyncio
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

from playwright.async_api import Page
from client.browser_client import BrowserManager

logger = logging.getLogger(__name__)

class TaobaoBrowserClient:
    """
    Browser client for taobao.com: persistent login, product page access, and background daemon support.
    """
    def __init__(
        self,
        headless: bool = True,
        user_data_dir: Optional[str] = None,
        profile_name: str = "taobao",
        monitor_interval: int = 60
    ):
        self.browser_manager = BrowserManager(
            headless=headless,
            user_data_dir=user_data_dir,
            profile_name=profile_name
        )
        self.page: Optional[Page] = None
        self.monitor_interval = monitor_interval
        self.started = False

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        """
        Start the browser and ensure login to taobao.com.
        """
        if not self.started:
            await self.browser_manager.start()
            self.started = True

    async def close(self):
        await self.browser_manager.close()

    async def get_product_info_with_api(self, product_id: str) -> Optional[Dict[str, Any]]:
        """
        Visit a specific product page on taobao.com and capture the product-info API response.
        
        Args:
            product_id: The product ID to search for
            
        Returns:
            The API response data or None if not found
        """
        await self.start()
        page = await self.browser_manager.new_page()
        if not page:
            raise RuntimeError("Browser not started. Call start() first.")

        url = f"https://hoobuy.com/product/1/{product_id}"
        route_url = f"**/hoobuy_order/pub/get/goods/info**"
        
        try:
            response_future = asyncio.Future()
            
            async def handle_response(response):
                if route_url.replace("**", "") in response.url:
                    try:
                        if response.status == 200:
                            response_data = await response.json()
                            logger.info(f"Captured product info API response for product {product_id}")
                            if not response_future.done():
                                response_future.set_result(response_data)
                        else:
                            logger.warning(f"API request failed with status: {response.status}")
                    except Exception as e:
                        logger.error(f"Failed to parse API response JSON: {e}")
            
            # Add response event listener
            page.on("response", handle_response)
            
            # Visit the product page
            await page.goto(url, wait_until="networkidle")
            logger.info(f"Visited product page: {url}")
            
            # Wait for API request to complete with timeout
            timeout = 20  # 20 seconds timeout
            try:
                api_response = await asyncio.wait_for(response_future, timeout=timeout)
                logger.info(f"Successfully captured product info API response for product {product_id}")
                return api_response
            except asyncio.TimeoutError:
                logger.warning(f"Timeout waiting for API response after {timeout} seconds")
                return None
            
        except Exception as e:
            logger.error(f"Error during product info retrieval: {e}", exc_info=True)
            return None
        finally:
            try:
                await page.close()
            except Exception as e:
                logger.error(f"Error closing page: {e}")

    async def _handle_product_api_request(self, route):
        """
        Handle the product-info API request and capture the response.
        This method is kept for compatibility but the main logic is now in get_product_info_with_api.
        """
        try:
            # Continue the request
            await route.continue_()
        except Exception as e:
            logger.error(f"Error handling product API request: {e}")
            # Ensure we continue the request even if there's an error
            try:
                await route.continue_()
            except Exception as continue_error:
                logger.error(f"Failed to continue route after error: {continue_error}")

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
        
        # Clean up old screenshots (delete files older than 10 minutes)
        await self._cleanup_old_screenshots(images_dir, filename_prefix)
        
        logger.info(f"Screenshot saved: {filepath}")
        return str(filepath)
  
    async def run_daemon(self):
        """
        Run background daemon for periodic tasks (e.g., keep-alive, monitoring).
        """
        logger.info("Starting taobao client daemon...")
        while True:
            try:
                # Example: keep session alive by visiting home page
                if self.page:
                    await self.page.goto("https://taobao.com/", wait_until="networkidle")
                    logger.info("Keep-alive: visited home page.")
                    # 截图记录保活状态
                    await self.take_screenshot("keepalive")
                await asyncio.sleep(self.monitor_interval)
            except Exception as e:
                logger.error(f"Daemon error: {e}")
                await asyncio.sleep(self.monitor_interval)


taobao_client = TaobaoBrowserClient() 