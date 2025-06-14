"""
Browser service manager for maintaining persistent browser instances.
"""

import asyncio
import logging
from typing import Dict, Optional

from client.whatsapp_client import WhatsAppBrowserClient
from client.browser_client import BrowserManager
from core import settings

logger = logging.getLogger(__name__)


class BrowserService:
    """Service for managing persistent browser instances across the application lifecycle."""
    
    def __init__(self):
        self.whatsapp_client: Optional[WhatsAppBrowserClient] = None
        self._background_tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        
    async def start(self):
        """Start all browser services."""
        if self._running:
            logger.warning("Browser service is already running")
            return
            
        logger.info("Starting browser service...")
        
        try:
            # Start WhatsApp browser client
            await self._start_whatsapp_client()
            
            # Start background monitoring tasks
            await self._start_background_tasks()
            
            self._running = True
            logger.info("Browser service started successfully")
            
            # 执行定时任务
            
            
            
        except Exception as e:
            logger.error(f"Failed to start browser service: {e}")
            await self.stop()  # Cleanup on failure
            raise
    
    async def stop(self):
        """Stop all browser services and cleanup resources."""
        if not self._running:
            logger.info("Browser service is not running")
            return
            
        logger.info("Stopping browser service...")
        
        # Cancel all background tasks
        for task_name, task in self._background_tasks.items():
            if not task.done():
                logger.info(f"Cancelling background task: {task_name}")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.info(f"Background task {task_name} cancelled")
                except Exception as e:
                    logger.error(f"Error cancelling task {task_name}: {e}")
        
        self._background_tasks.clear()
        
        # Close WhatsApp client
        if self.whatsapp_client:
            try:
                await self.whatsapp_client.close()
                logger.info("WhatsApp client closed")
            except Exception as e:
                logger.error(f"Error closing WhatsApp client: {e}")
            finally:
                self.whatsapp_client = None
        
        self._running = False
        logger.info("Browser service stopped")
    
    async def _start_whatsapp_client(self):
        """Start the WhatsApp browser client."""
        try:
            # 根据环境配置决定是否使用headless模式
            headless = getattr(settings, 'BROWSER_HEADLESS', True)
            
            self.whatsapp_client = WhatsAppBrowserClient(headless=headless)
            await self.whatsapp_client.start()
            
            logger.info("WhatsApp browser client started and ready")
            
        except Exception as e:
            logger.error(f"Failed to start WhatsApp client: {e}")
            raise
    
    async def _start_background_tasks(self):
        """Start background monitoring and maintenance tasks."""
        # Health check task
        self._background_tasks['health_check'] = asyncio.create_task(
            self._health_check_loop()
        )
        
        # Browser keepalive task
        self._background_tasks['keepalive'] = asyncio.create_task(
            self._keepalive_loop()
        )
        
        logger.info("Background tasks started")
    
    async def _health_check_loop(self):
        """Periodic health check for browser instances."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                if self.whatsapp_client and self.whatsapp_client.page:
                    # Simple health check - verify page is still responsive
                    try:
                        await self.whatsapp_client.page.evaluate("document.title")
                        logger.debug("WhatsApp client health check passed")
                    except Exception as e:
                        logger.warning(f"WhatsApp client health check failed: {e}")
                        # Attempt to restart
                        await self._restart_whatsapp_client()
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health check loop: {e}")
    
    async def _keepalive_loop(self):
        """Keep browser alive with periodic activity."""
        while self._running:
            try:
                await asyncio.sleep(300)  # Every 5 minutes
                
                if self.whatsapp_client and self.whatsapp_client.page:
                    # Small activity to keep session alive
                    try:
                        await self.whatsapp_client.page.evaluate("void(0)")
                        logger.debug("Browser keepalive executed")
                    except Exception as e:
                        logger.warning(f"Browser keepalive failed: {e}")
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in keepalive loop: {e}")
    
    async def _restart_whatsapp_client(self):
        """Restart the WhatsApp client if it becomes unresponsive."""
        logger.info("Attempting to restart WhatsApp client...")
        
        try:
            # Close existing client
            if self.whatsapp_client:
                await self.whatsapp_client.close()
            
            # Wait a bit before restart
            await asyncio.sleep(5)
            
            # Start new client
            await self._start_whatsapp_client()
            
            logger.info("WhatsApp client restarted successfully")
            
        except Exception as e:
            logger.error(f"Failed to restart WhatsApp client: {e}")
    
    def is_running(self) -> bool:
        """Check if the browser service is running."""
        return self._running
    
    async def get_whatsapp_status(self) -> dict:
        """Get status of WhatsApp client."""
        if not self.whatsapp_client:
            return {"status": "not_started", "page": None}
        
        try:
            if self.whatsapp_client.page:
                # Try to get page title to verify it's responsive
                title = await self.whatsapp_client.page.title()
                url = self.whatsapp_client.page.url
                return {
                    "status": "active",
                    "title": title,
                    "url": url,
                    "page": "available"
                }
            else:
                return {"status": "started", "page": "not_created"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def take_whatsapp_screenshot(self) -> str:
        """Take a screenshot of WhatsApp Web."""
        if not self.whatsapp_client:
            raise RuntimeError("WhatsApp client is not started")
        
        return await self.whatsapp_client.take_screenshot()


# Global browser service instance
browser_service = BrowserService() 