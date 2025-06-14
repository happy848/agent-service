#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WhatsApp monitoring service startup script
Demonstrates how to use WhatsAppMonitor class for automated monitoring
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from client.whatsapp_client import (
    WhatsAppBrowserClient,
    WhatsAppMonitor,
    TaskType,
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/app/docker/logs/whatsapp_monitor.log')
    ]
)

logger = logging.getLogger(__name__)


async def custom_task_example(page, message: str = "Custom task executed"):
    """自定义任务示例"""
    # 执行一些自定义的页面操作
    title = await page.title()
    current_url = await page.url
    
    result = {
        'message': message,
        'page_title': title,
        'current_url': current_url,
        'timestamp': datetime.now().isoformat()
    }
    
    logger.info(f"Custom task result: {result}")
    return result


async def advanced_monitoring_example():
    """Advanced monitoring example: shows how to customize tasks and configuration"""
    logger.info("🚀 Starting advanced WhatsApp monitoring example...")
    
    async with WhatsAppBrowserClient(headless=True) as client:
        # Create monitor
        monitor = WhatsAppMonitor(client, interval_seconds=15)
        
        # Add custom task
        monitor.add_task(
            TaskType.CUSTOM,
            lambda: custom_task_example(client.page, "Hello from custom task!"),
            "custom_hello_task",
            enabled=True
        )
        
        # Disable screenshot task (demonstration of task control)
        monitor.disable_task("screenshot_task")
        
        # Show monitor status
        status = monitor.get_status()
        logger.info(f"Monitor status: {status}")
        
        try:
            # Start monitoring (run for a short time as example)
            monitoring_task = asyncio.create_task(monitor.start_monitoring())
            
            # Let monitoring run for 1 minute
            await asyncio.sleep(60)
            
            # Stop monitoring
            monitor.stop_monitoring()
            await monitoring_task
            
        except KeyboardInterrupt:
            logger.info("Monitoring interrupted by user")
            monitor.stop_monitoring()

