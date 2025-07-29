import asyncio
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
from collections import deque

from playwright.async_api import Page
from client.browser_client import BrowserManager

logger = logging.getLogger(__name__)


class PagePool:
    """
    页面管理池，用于重用Playwright页面以提高性能
    """
    
    def __init__(self, max_pages: int = 10):
        self.max_pages = max_pages
        self.available_pages: deque[Page] = deque()
        self.in_use_pages: set[Page] = set()
        self._lock = asyncio.Lock()
    
    async def get_page(self, browser_manager: BrowserManager) -> Page:
        """
        从池中获取一个页面，如果没有可用页面则创建新的
        
        Args:
            browser_manager: 浏览器管理器实例
            
        Returns:
            可用的页面实例
        """
        async with self._lock:
            # 尝试从可用页面池中获取
            if self.available_pages:
                page = self.available_pages.popleft()
                self.in_use_pages.add(page)
                logger.debug(f"从页面池获取页面，当前使用中: {len(self.in_use_pages)}, 可用: {len(self.available_pages)}")
                return page
            
            # 如果没有可用页面且未达到最大数量，创建新页面
            if len(self.in_use_pages) < self.max_pages:
                page = await browser_manager.new_page()
                if page:
                    self.in_use_pages.add(page)
                    logger.debug(f"创建新页面，当前使用中: {len(self.in_use_pages)}, 可用: {len(self.available_pages)}")
                    return page
                else:
                    raise RuntimeError("无法创建新页面")
            
            # 如果达到最大数量，等待有页面可用
            logger.warning(f"页面池已满({self.max_pages})，等待可用页面...")
            while not self.available_pages:
                await asyncio.sleep(0.1)
            
            page = self.available_pages.popleft()
            self.in_use_pages.add(page)
            logger.debug(f"等待后获取页面，当前使用中: {len(self.in_use_pages)}, 可用: {len(self.available_pages)}")
            return page
    
    async def return_page(self, page: Page):
        """
        将页面返回到池中
        
        Args:
            page: 要返回的页面实例
        """
        async with self._lock:
            if page in self.in_use_pages:
                self.in_use_pages.remove(page)
                
                # 检查页面是否仍然可用
                try:
                    # 尝试访问页面标题来检查页面是否仍然有效
                    await page.title()
                    self.available_pages.append(page)
                    logger.debug(f"页面已返回池中，当前使用中: {len(self.in_use_pages)}, 可用: {len(self.available_pages)}")
                except Exception as e:
                    logger.warning(f"页面已失效，丢弃页面: {e}")
                    try:
                        await page.close()
                    except Exception as close_error:
                        logger.error(f"关闭失效页面时出错: {close_error}")
    
    async def close_all_pages(self):
        """关闭池中的所有页面"""
        async with self._lock:
            all_pages = list(self.available_pages) + list(self.in_use_pages)
            self.available_pages.clear()
            self.in_use_pages.clear()
            
            for page in all_pages:
                try:
                    await page.close()
                except Exception as e:
                    logger.error(f"关闭页面时出错: {e}")
            
            logger.info(f"已关闭所有页面，共 {len(all_pages)} 个")
    
    def get_stats(self) -> Dict[str, int]:
        """获取页面池统计信息"""
        return {
            "available": len(self.available_pages),
            "in_use": len(self.in_use_pages),
            "total": len(self.available_pages) + len(self.in_use_pages),
            "max_pages": self.max_pages
        }


class TaobaoBrowserClient:
    """
    Browser client for taobao.com: persistent login, product page access, and background daemon support.
    """
    def __init__(
        self,
        headless: bool = True,
        user_data_dir: Optional[str] = None,
        profile_name: str = "taobao",
        monitor_interval: int = 60,
        max_pages: int = 10
    ):
        self.browser_manager = BrowserManager(
            headless=headless,
            user_data_dir=user_data_dir,
            profile_name=profile_name
        )
        self.page: Optional[Page] = None
        self.monitor_interval = monitor_interval
        self.started = False
        self.page_pool = PagePool(max_pages=max_pages)

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
        await self.page_pool.close_all_pages()
        await self.browser_manager.close()

    async def get_product_info_with_api(self, product_id: str) -> Optional[Dict[str, Any]]:
        """
        Visit a specific product page on taobao.com and capture the product-info API response.
        使用页面池优化性能。
        
        Args:
            product_id: The product ID to search for
            
        Returns:
            The API response data or None if not found
        """
        await self.start()
        
        # 从页面池获取页面
        page = await self.page_pool.get_page(self.browser_manager)
        if not page:
            raise RuntimeError("无法获取页面。请先调用 start()。")

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
            # 将页面返回到池中而不是关闭
            await self.page_pool.return_page(page)

    async def get_product_info_batch(self, product_ids: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        批量获取产品信息，使用页面池并发处理
        
        Args:
            product_ids: 产品ID列表
            
        Returns:
            产品ID到产品信息的映射字典
        """
        async def get_single_product_info(product_id: str) -> tuple[str, Optional[Dict[str, Any]]]:
            try:
                result = await self.get_product_info_with_api(product_id)
                return product_id, result
            except Exception as e:
                logger.error(f"获取产品 {product_id} 信息失败: {e}")
                return product_id, None
        
        # 并发处理所有产品ID，但限制并发数量以避免过度占用页面池
        semaphore = asyncio.Semaphore(min(5, len(product_ids)))
        
        async def limited_get_product_info(product_id: str):
            async with semaphore:
                return await get_single_product_info(product_id)
        
        tasks = [limited_get_product_info(pid) for pid in product_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理结果
        product_info_map = {}
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"批量获取产品信息时出错: {result}")
                continue
            product_id, info = result
            product_info_map[product_id] = info
        
        return product_info_map

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

    async def _cleanup_old_screenshots(self, images_dir: Path, filename_prefix: str):
        """清理旧的截图文件（删除超过10分钟的文件）"""
        try:
            current_time = datetime.now()
            cutoff_time = current_time - timedelta(minutes=10)
            
            for file_path in images_dir.glob(f"*_{filename_prefix}.png"):
                try:
                    file_stat = file_path.stat()
                    file_time = datetime.fromtimestamp(file_stat.st_mtime)
                    
                    if file_time < cutoff_time:
                        file_path.unlink()
                        logger.debug(f"删除旧截图文件: {file_path}")
                except Exception as e:
                    logger.warning(f"删除文件 {file_path} 时出错: {e}")
        except Exception as e:
            logger.error(f"清理旧截图文件时出错: {e}")
  
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

    async def get_pool_stats(self) -> Dict[str, int]:
        """获取页面池统计信息"""
        return self.page_pool.get_stats()


taobao_client = TaobaoBrowserClient() 