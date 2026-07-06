"""
Уведомления (Telegram/Discord).
Скелет для отправки сообщений о сделках и статусах системы.
"""
import logging
from typing import Optional
import asyncio

logger = logging.getLogger(__name__)

class Notifier:
    def __init__(self, config: dict):
        self.config = config
        self.enabled = config.get("notifications", {}).get("enabled", False)
        self.telegram_token = config.get("notifications", {}).get("telegram_token")
        self.chat_id = config.get("notifications", {}).get("chat_id")
        
        if self.enabled and not self.telegram_token:
            logger.warning("Notifications enabled but no token provided. Disabling.")
            self.enabled = False

    async def send_message(self, message: str, parse_mode: str = "Markdown"):
        """Отправляет сообщение в Telegram."""
        if not self.enabled:
            return
        
        # TODO: Реализовать отправку через aiohttp
        # url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        # payload = {"chat_id": self.chat_id, "text": message, "parse_mode": parse_mode}
        
        logger.info(f"[TELEGRAM MOCK] {message}")
        # await session.post(url, json=payload)

    async def notify_trade(self, scenario: dict, action: str):
        """Форматирует и отправляет уведомление о сделке."""
        msg = (
            f"🚀 **TRADE {action}**\n"
            f"Symbol: {scenario.get('symbol')}\n"
            f"Direction: {scenario.get('direction')}\n"
            f"Entry: {scenario.get('entry_price')}\n"
            f"Target: {scenario.get('target_prices')}\n"
            f"Stop: {scenario.get('stop_loss')}\n"
            f"Confidence: {scenario.get('confidence_score'):.2f}"
        )
        await self.send_message(msg)

    async def notify_error(self, error_msg: str):
        """Уведомление об ошибке."""
        msg = f"❌ **ERROR**\n`{error_msg}`"
        await self.send_message(msg)

    async def notify_status(self, status: str):
        """Статус системы."""
        msg = f"ℹ️ **STATUS**\n{status}"
        await self.send_message(msg)
