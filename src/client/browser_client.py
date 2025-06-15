import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import json

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

class BrowserManager:
    """Browser manager for handling browser lifecycle and configuration."""
    
    def __init__(
        self, 
        headless: bool = True, 
        user_data_dir: Optional[str] = None, 
        profile_name: str = "default",
    ):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.playwright = None
        self.headless = headless
        self.profile_name = profile_name
        self.connect_to_remote = connect_to_remote  # Format: "ws://host:port/devtools/browser/..."
        
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
        
        # Prepare browser launch arguments
        browser_args = [
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled',
            '--exclude-switches=enable-automation',
            '--disable-extensions',
            '--no-first-run',
            '--disable-default-apps',
            '--disable-infobars',
            '--window-size=1920,1080',
            '--start-maximized',
            # Enhanced anti-detection arguments
            '--disable-features=VizDisplayCompositor,TranslateUI',
            '--disable-ipc-flooding-protection',
            '--disable-renderer-backgrounding',
            '--disable-backgrounding-occluded-windows',
            '--disable-background-timer-throttling',
            '--disable-field-trial-config',
            '--disable-background-networking',
            '--disable-sync',
            '--disable-translate',
            '--disable-web-security',
            '--allow-running-insecure-content',
            '--enable-local-file-accesses',
            '--allow-file-access-from-files',
            # Performance and caching
            '--enable-features=NetworkService,NetworkServiceLogging',
            '--max_old_space_size=4096',
            '--aggressive-cache-discard',
            # Memory optimization
            '--memory-pressure-off',
            '--renderer-process-limit=10',
            '--enable-accelerated-2d-canvas',
            '--enable-gpu-rasterization',
            # Security and privacy
            '--disable-client-side-phishing-detection',
            '--disable-component-extensions-with-background-pages',
            '--disable-features=AudioServiceOutOfProcess',
            # Language and locale
            '--lang=en-US',
            '--accept-lang=en-US,en,ja'
        ]
        
        # Launch browser with persistent user data directory
        self.browser = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=headless_mode,
            viewport={'width': 1920, 'height': 1080},
            screen={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            extra_http_headers={
                'Accept-Language': 'en-US,en;q=0.9,ja;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br, zstd',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Cache-Control': 'max-age=0',
                'Sec-CH-UA': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
                'Sec-CH-UA-Mobile': '?0',
                'Sec-CH-UA-Platform': '"Windows"',
                'Sec-CH-UA-Platform-Version': '"15.0.0"',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Upgrade-Insecure-Requests': '1',
                'DNT': '1'
            },
            locale='en-US',
            timezone_id='Asia/Tokyo',
            permissions=['notifications', 'geolocation'],
            color_scheme='light',
            args=browser_args
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
            // Remove webdriver property completely
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            
            // Remove automation indicators
            delete window.navigator.__proto__.webdriver;
            delete navigator.__proto__.webdriver;
            
            // Mock chrome runtime with more realistic properties
            window.chrome = {
                runtime: {
                    onConnect: null,
                    onMessage: null
                },
                csi: function() {},
                loadTimes: function() {
                    return {
                        requestTime: Date.now() / 1000,
                        startLoadTime: Date.now() / 1000,
                        commitLoadTime: Date.now() / 1000,
                        finishDocumentLoadTime: Date.now() / 1000,
                        finishLoadTime: Date.now() / 1000,
                        firstPaintTime: Date.now() / 1000,
                        firstPaintAfterLoadTime: 0,
                        navigationType: "Other"
                    };
                }
            };
            
            // Mock platform to match Windows
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Win32',
            });
            
            // Mock realistic plugins list
            Object.defineProperty(navigator, 'plugins', {
                get: () => ({
                    length: 3,
                    0: { name: "PDF Viewer", filename: "internal-pdf-viewer" },
                    1: { name: "Chrome PDF Viewer", filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai" },
                    2: { name: "Chromium PDF Viewer", filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai" }
                }),
            });
            
            // Mock languages for Japan region with English preference
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en', 'ja'],
            });
            
            Object.defineProperty(navigator, 'language', {
                get: () => 'en-US',
            });
            
            // Mock hardware concurrency (typical for modern Windows PC)
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8,
            });
            
            // Mock memory information
            Object.defineProperty(navigator, 'deviceMemory', {
                get: () => 8,
            });
            
            // Mock connection information
            Object.defineProperty(navigator, 'connection', {
                get: () => ({
                    effectiveType: '4g',
                    rtt: 50,
                    downlink: 10,
                    saveData: false
                }),
            });
            
            // Mock permissions with realistic responses
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => {
                const name = parameters.name;
                if (name === 'notifications') {
                    return Promise.resolve({ state: 'default' });
                } else if (name === 'geolocation') {
                    return Promise.resolve({ state: 'prompt' });
                } else if (name === 'camera') {
                    return Promise.resolve({ state: 'prompt' });
                } else if (name === 'microphone') {
                    return Promise.resolve({ state: 'prompt' });
                }
                return originalQuery(parameters);
            };
            
            // Override Date to match Japan timezone
            const originalDate = Date;
            Date = class extends originalDate {
                constructor(...args) {
                    if (args.length === 0) {
                        super();
                    } else {
                        super(...args);
                    }
                }
                
                getTimezoneOffset() {
                    return -540; // JST (UTC+9)
                }
            };
            Date.prototype = originalDate.prototype;
            Date.now = originalDate.now;
            Date.parse = originalDate.parse;
            Date.UTC = originalDate.UTC;
            
            // Hide automation-related properties
            Object.defineProperty(window, 'outerWidth', {
                get: () => window.innerWidth,
            });
            
            Object.defineProperty(window, 'outerHeight', {
                get: () => window.innerHeight + 85, // Account for browser chrome
            });
            
            // Mock WebGL properties
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) { // UNMASKED_VENDOR_WEBGL
                    return 'Intel Inc.';
                }
                if (parameter === 37446) { // UNMASKED_RENDERER_WEBGL
                    return 'Intel(R) HD Graphics 630';
                }
                return getParameter.call(this, parameter);
            };
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
