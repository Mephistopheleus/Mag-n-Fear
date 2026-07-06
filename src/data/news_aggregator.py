"""
Агрегатор новостей и анализ тональности (Sentiment Analysis).
Скелет для интеграции с внешними источниками (Twitter, NewsAPI, Telegram channels).
"""
import asyncio
import aiohttp
from typing import List, Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class NewsItem:
    def __init__(self, source: str, headline: str, sentiment: float, timestamp: datetime, impact_score: float):
        self.source = source
        self.headline = headline
        self.sentiment = sentiment  # -1.0 (негатив) до 1.0 (позитив)
        self.timestamp = timestamp
        self.impact_score = impact_score  # 0.0 до 1.0 (сила влияния)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "headline": self.headline,
            "sentiment": self.sentiment,
            "timestamp": self.timestamp.isoformat(),
            "impact_score": self.impact_score
        }

class NewsAggregator:
    def __init__(self, config: dict):
        self.config = config
        self.enabled = config.get("news", {}).get("enabled", False)
        self.sources = config.get("news", {}).get("sources", [])
        self.session: Optional[aiohttp.ClientSession] = None
        self.latest_news: List[NewsItem] = []
        self.aggregate_sentiment = 0.0  # Скользящее среднее тональности

    async def start(self):
        if not self.enabled:
            logger.info("News Aggregator disabled in config.")
            return
        self.session = aiohttp.ClientSession()
        logger.info("News Aggregator started.")
        asyncio.create_task(self._polling_loop())

    async def stop(self):
        if self.session:
            await self.session.close()
        logger.info("News Aggregator stopped.")

    async def _polling_loop(self):
        interval = self.config.get("news", {}).get("poll_interval_sec", 60)
        while True:
            try:
                await self._fetch_news()
            except Exception as e:
                logger.error(f"Error fetching news: {e}")
            await asyncio.sleep(interval)

    async def _fetch_news(self):
        """
        Здесь должна быть логика подключения к API новостей.
        Сейчас - заглушка, генерирующая тестовые данные.
        """
        # TODO: Реализовать парсинг реальных источников
        # Пример: CryptoPanic API, Twitter API v2, Telegram Scraper
        pass

    def _analyze_sentiment(self, text: str) -> float:
        """
        Анализ тональности текста.
        Можно использовать NLTK, VADER, или готовую ML-модель.
        """
        # Заглушка: случайное значение для демонстрации
        import random
        return random.uniform(-0.5, 0.5)

    def get_current_sentiment_factor(self) -> float:
        """
        Возвращает текущий агрегированный фактор тональности (-1.0 ... 1.0).
        Используется как входной параметр для индикаторов или сценариев.
        """
        return self.aggregate_sentiment

    def add_news_item(self, item: NewsItem):
        self.latest_news.append(item)
        # Обновляем скользящее среднее (упрощенно)
        if len(self.latest_news) > 10:
            self.latest_news.pop(0)
        
        if self.latest_news:
            weights = [n.impact_score for n in self.latest_news]
            total_weight = sum(weights)
            if total_weight > 0:
                self.aggregate_sentiment = sum(n.sentiment * n.impact_score for n in self.latest_news) / total_weight
            else:
                self.aggregate_sentiment = 0.0
