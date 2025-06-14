"""
Browser client for WhatsApp Web screenshot capture using Playwright.
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

class WhatsAppBrowserClient:
    """Browser client for capturing WhatsApp Web screenshots."""
    
    def __init__(self, headless: bool = True):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None
        self.headless = headless
        
    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
        
    async def start(self):
        """Start the browser and navigate to WhatsApp Web."""
        self.playwright = await async_playwright().start()
        
        # In containerized environments, browser must run in headless mode
        # Set headless=False only if running in local development with display server
        headless_mode = self.headless or os.getenv('DISPLAY') is None
        
        # Launch browser with persistent context to maintain login
        self.browser = await self.playwright.chromium.launch(
            headless=headless_mode,
            args=['--no-sandbox', '--disable-dev-shm-usage']
        )
        
        self.context = await self.browser.new_context(
            viewport={'width': 1280, 'height': 720},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        )
        
        self.page = await self.context.new_page()
        
        # Navigate to WhatsApp Web
        await self.page.goto('https://web.whatsapp.com/')
        
        # Wait for the page to load
        await self.page.wait_for_load_state('networkidle')
        
        mode_str = "headless" if headless_mode else "visible"
        print(f"Browser started in {mode_str} mode and navigated to WhatsApp Web")
        if not headless_mode:
            print("Please scan the QR code if not already logged in...")
        else:
            print("Note: Running in headless mode. QR code scanning requires pre-authenticated session.")
        
    async def close(self):
        """Close the browser and cleanup."""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
            
    async def take_screenshot(self) -> str:
        """Take a screenshot and save it with timestamp."""
        if not self.page:
            raise RuntimeError("Browser not started. Call start() first.")
            
        # Ensure images directory exists
        images_dir = Path("/app/docker/logs")
        images_dir.mkdir(exist_ok=True)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"whatsapp_screenshot_{timestamp}.png"
        filepath = images_dir / filename
        
        # Take screenshot
        await self.page.screenshot(path=str(filepath), full_page=True)
        
        print(f"Screenshot saved: {filepath}")
        return str(filepath)
        
    async def start_continuous_screenshots(self, interval_seconds: int = 10):
        """Start taking screenshots every specified interval."""
        print(f"Starting continuous screenshots every {interval_seconds} seconds...")
        print("Press Ctrl+C to stop")
        
        try:
            while True:
                await self.take_screenshot()
                await asyncio.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\nStopping continuous screenshots...")
        except Exception as e:
            print(f"Error during screenshot capture: {e}")
            

async def screenshot_whatsapp(interval_seconds: int = 10, duration_minutes: Optional[int] = None, headless: bool = True):
    """
    External function to capture WhatsApp Web screenshots.
    
    Args:
        interval_seconds: Interval between screenshots in seconds (default: 10)
        duration_minutes: Total duration to run in minutes. If None, runs indefinitely
        headless: Whether to run browser in headless mode (default: True for containerized environments)
    """
    async with WhatsAppBrowserClient(headless=headless) as client:
        if duration_minutes:
            # Run for specified duration
            end_time = asyncio.get_event_loop().time() + (duration_minutes * 60)
            screenshot_count = 0
            
            try:
                while asyncio.get_event_loop().time() < end_time:
                    await client.take_screenshot()
                    screenshot_count += 1
                    await asyncio.sleep(interval_seconds)
                    
                print(f"Completed {screenshot_count} screenshots in {duration_minutes} minutes")
                
            except KeyboardInterrupt:
                print(f"\nStopped early. Captured {screenshot_count} screenshots")
        else:
            # Run indefinitely
            await client.start_continuous_screenshots(interval_seconds)


if __name__ == "__main__":
    # Example usage - runs in headless mode by default for containerized environments
    asyncio.run(screenshot_whatsapp(interval_seconds=10, duration_minutes=5, headless=True))