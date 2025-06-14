import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

class BrowserManager:
    """Browser manager for handling browser lifecycle and configuration."""
    
    def __init__(self, headless: bool = True, user_data_dir: Optional[str] = None, profile_name: str = "default"):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.playwright = None
        self.headless = headless
        self.profile_name = profile_name
        
        # Set up persistent user data directory
        if user_data_dir:
            self.user_data_dir = Path(user_data_dir)
        else:
            # Default to docker/data/playwright
            self.user_data_dir = Path("/app/docker/data/playwright") / profile_name
        
        # Ensure the directory exists
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        
    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
        
    async def start(self):
        """Start the browser with optimized settings for real user simulation."""
        self.playwright = await async_playwright().start()
        
        # In containerized environments, browser must run in headless mode
        # Set headless=False only if running in local development with display server
        headless_mode = self.headless or os.getenv('DISPLAY') is None
        
        print(f"Using persistent user data directory: {self.user_data_dir}")
        
        # Launch browser with persistent user data directory
        self.browser = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=headless_mode,
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            extra_http_headers={
                'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Sec-CH-UA': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                'Sec-CH-UA-Mobile': '?0',
                'Sec-CH-UA-Platform': '"Linux"',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Upgrade-Insecure-Requests': '1'
            },
            locale='en-US',
            timezone_id='America/New_York',
            permissions=['notifications'],
            color_scheme='light',
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--exclude-switches=enable-automation',
                '--disable-extensions-except=/path/to/extension',
                '--disable-extensions',
                '--no-first-run',
                '--disable-default-apps',
                '--disable-infobars',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor',
                '--window-size=1920,1080',
                '--start-maximized',
                # Cache and storage related args
                '--enable-features=NetworkService,NetworkServiceLogging',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
                '--enable-local-file-accesses',
                '--allow-file-access-from-files'
            ]
        )
        
        # With launch_persistent_context, the browser object is actually a BrowserContext
        self.context = self.browser
        
        mode_str = "headless" if headless_mode else "visible"
        print(f"Browser started in {mode_str} mode with persistent storage enabled")
        print(f"Profile: {self.profile_name}")
        
    async def close(self):
        """Close the browser and cleanup resources."""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
            
    async def new_page(self) -> Page:
        """Create a new page with anti-detection scripts."""
        if not self.context:
            raise RuntimeError("Browser not started. Call start() first.")
            
        page = await self.context.new_page()
        
        # Hide automation traces with JavaScript
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            
            // Remove automation indicators
            delete window.navigator.__proto__.webdriver;
            delete navigator.__proto__.webdriver;
            
            // Mock chrome runtime
            window.chrome = {
                runtime: {}
            };
            
            // Mock platform to match Linux
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Linux x86_64',
            });
            
            // Mock plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
            
            // Mock languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en', 'zh-CN', 'zh'],
            });
            
            // Mock hardware concurrency (typical for Linux server)
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 4,
            });
            
            // Override permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)
        
        return page
        
    def is_started(self) -> bool:
        """Check if browser is started and ready."""
        return self.browser is not None and self.context is not None
    
    def get_user_data_dir(self) -> Path:
        """Get the user data directory path."""
        return self.user_data_dir
    
    def get_profile_name(self) -> str:
        """Get the profile name."""
        return self.profile_name

class GenericBrowserClient:
    """Generic browser client that can be used for any website."""
    
    def __init__(
        self, 
        headless: bool = True, 
        user_data_dir: Optional[str] = None,
        profile_name: str = "generic"
    ):
        self.browser_manager = BrowserManager(
            headless=headless, 
            user_data_dir=user_data_dir, 
            profile_name=profile_name
        )
        self.page: Optional[Page] = None
        
    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
        
    async def start(self):
        """Start the browser manager."""
        await self.browser_manager.start()
        
    async def close(self):
        """Close the browser and cleanup."""
        await self.browser_manager.close()
        
    async def goto(self, url: str) -> Page:
        """Navigate to a URL and return the page."""
        if not self.browser_manager.is_started():
            await self.start()
            
        self.page = await self.browser_manager.new_page()
        await self.page.goto(url, wait_until='networkidle')
        return self.page
        
    async def take_screenshot(self, filename_prefix: str = "screenshot") -> str:
        """Take a screenshot and save it with timestamp."""
        if not self.page:
            raise RuntimeError("No page available. Call goto() first.")
            
        # Ensure images directory exists
        images_dir = Path("/app/docker/logs")
        images_dir.mkdir(exist_ok=True)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{filename_prefix}_{timestamp}.png"
        filepath = images_dir / filename
        
        # Take screenshot
        await self.page.screenshot(path=str(filepath), full_page=True)
        
        print(f"Screenshot saved: {filepath}")
        return str(filepath)

async def test_browser_realness(headless: bool = True):
    """
    Test browser realness by visiting detection websites and taking screenshots.
    
    Args:
        headless: Whether to run browser in headless mode (default: True for containerized environments)
    """
    detection_sites = [
        {
            "name": "Bot Detection - Sannysoft",
            "url": "https://bot.sannysoft.com/",
            "wait_selector": "body",
            "description": "Comprehensive bot detection test"
        },
        {
            "name": "Headless Detection",
            "url": "https://arh.antoinevastel.com/bots/areyouheadless",
            "wait_selector": "body",
            "description": "Headless browser detection"
        },
        {
            "name": "Browser Fingerprint - WebKay",
            "url": "https://webkay.robinlinus.com/",
            "wait_selector": "body",
            "description": "Browser fingerprint analysis"
        },
        {
            "name": "HTTP Headers - HTTPBin",
            "url": "https://httpbin.org/headers",
            "wait_selector": "pre",
            "description": "HTTP headers inspection"
        },
        {
            "name": "Browser Info - WhatIsMyBrowser",
            "url": "https://www.whatismybrowser.com/",
            "wait_selector": ".browser-detection-content",
            "description": "Browser information detection"
        },
        {
            "name": "Privacy Test - EFF",
            "url": "https://coveryourtracks.eff.org/",
            "wait_selector": "body",
            "description": "Privacy and uniqueness test"
        }
    ]
    
    async with GenericBrowserClient(headless=headless) as client:
        print("🚀 Starting browser realness detection test...")
        screenshot_paths = []
        
        for site in detection_sites:
            try:
                print(f"\n📍 Testing: {site['name']}")
                print(f"   URL: {site['url']}")
                print(f"   Description: {site['description']}")
                
                # Navigate to the site
                page = await client.goto(site['url'])
                
                # Wait for the page to load
                try:
                    await page.wait_for_selector(site['wait_selector'], timeout=10000)
                except:
                    # If specific selector not found, wait a bit and continue
                    await asyncio.sleep(3)
                
                # Wait a bit more for dynamic content
                await asyncio.sleep(2)
                
                # Take screenshot
                site_name = site['name'].replace(' ', '_').replace('-', '_').lower()
                screenshot_path = await client.take_screenshot(f"detection_test_{site_name}")
                screenshot_paths.append(screenshot_path)
                
                print(f"   ✅ Screenshot saved: {screenshot_path}")
                
            except Exception as e:
                print(f"   ❌ Error testing {site['name']}: {e}")
                continue
        
        print(f"\n🎯 Detection test completed! {len(screenshot_paths)} screenshots saved.")
        print(f"   Screenshots location: /app/docker/logs/")
        
        for i, path in enumerate(screenshot_paths, 1):
            filename = Path(path).name
            print(f"   {i}. {filename}")
        
        return screenshot_paths
