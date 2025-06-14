"""
Example usage of WhatsApp Web screenshot functionality.
"""

import asyncio
from src.client.browser_client import screenshot_whatsapp, WhatsAppBrowserClient


async def main():
    """Main function demonstrating different usage patterns."""
    
    print("=== WhatsApp Web Screenshot Demo ===\n")
    
    # Option 1: Use the simple function (runs for 2 minutes as demo)
    print("Option 1: Using screenshot_whatsapp function")
    print("This will run for 2 minutes, taking screenshots every 10 seconds")
    print("Press Ctrl+C to stop early\n")
    
    try:
        await screenshot_whatsapp(interval_seconds=10, duration_minutes=2)
    except KeyboardInterrupt:
        print("Demo stopped by user")
    
    print("\n" + "="*50)
    
    # Option 2: Use the class directly for more control
    print("Option 2: Using WhatsAppBrowserClient class directly")
    print("This will take 3 individual screenshots with 5-second intervals\n")
    
    async with WhatsAppBrowserClient() as client:
        for i in range(3):
            print(f"Taking screenshot {i+1}/3...")
            await client.take_screenshot()
            if i < 2:  # Don't sleep after the last screenshot
                await asyncio.sleep(5)
    
    print("\nDemo completed!")


if __name__ == "__main__":
    # Make sure playwright browsers are installed
    print("Make sure you have installed playwright browsers:")
    print("Run: playwright install chromium")
    print()
    
    asyncio.run(main()) 