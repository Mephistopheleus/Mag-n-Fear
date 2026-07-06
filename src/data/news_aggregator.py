"""
News Aggregator Module.
Parses news sources, calculates impact vectors, and writes to ProbabilityField.
"""
import asyncio
import time
from typing import List, Dict, Any, Optional
import feedparser
import aiohttp
from src.core.models import NewsVector
from src.core.field import ProbabilityField


class NewsAggregator:
    """
    Агрегатор новостей.
    
    Функции:
    1. Парсинг RSS/Atom лент крипто-источников.
    2. Фильтрация по ключевым словам (DOGE, BTC, futures, SEC и т.д.).
    3. Расчет вектора влияния: [Направление, Сила, Время, Вероятность].
    4. Запись векторов в ProbabilityField.
    
    Источники (публичные RSS):
    - CoinDesk
    - Cointelegraph
    - CryptoSlate
    - The Block
    """
    
    def __init__(self, config: Dict[str, Any], probability_field: ProbabilityField):
        self.config = config
        self.field = probability_field
        
        # Ключевые слова для анализа
        self.bullish_keywords = [
            'bull', 'surge', 'rally', 'moon', 'breakout', 'adoption', 
            'partnership', 'upgrade', 'halving', 'etf', 'approval', 'buy'
        ]
        self.bearish_keywords = [
            'bear', 'crash', 'dump', 'hack', 'exploit', 'ban', 'lawsuit',
            'sec', 'investigation', 'ftx', 'collapse', 'sell', 'warning'
        ]
        self.asset_keywords = {
            'DOGE': ['doge', 'dogecoin', 'shib', 'meme coin'],
            'BTC': ['btc', 'bitcoin', 'crypto', 'cryptocurrency', 'futures']
        }
        
        # RSS источники
        self.feeds = [
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://cointelegraph.com/rss",
            "https://www.cryptoslate.com/feed/",
            "https://www.theblockcrypto.com/rss",
        ]
        
        self._last_fetch = 0
        self._fetch_interval = 60  # Обновление каждые 60 сек
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        """Запуск HTTP сессии."""
        if not self._session:
            self._session = aiohttp.ClientSession()

    async def stop(self):
        """Остановка HTTP сессии."""
        if self._session:
            await self._session.close()
            self._session = None

    async def run_cycle(self, symbols: List[str]):
        """
        Основной цикл: парсинг -> анализ -> запись в матрицу.
        """
        if time.time() - self._last_fetch < self._fetch_interval:
            return
            
        await self.start()
        self._last_fetch = time.time()
        
        tasks = [self._fetch_feed(url) for url in self.feeds]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            all_entries = []
            for res in results:
                if isinstance(res, list):
                    all_entries.extend(res)
            
            # Анализ каждой новости
            for entry in all_entries:
                await self._analyze_and_dispatch(entry, symbols)
                
        except Exception as e:
            print(f"[NewsAggregator] Error in cycle: {e}")

    async def _fetch_feed(self, url: str) -> List[Dict]:
        """Парсинг одного RSS канала."""
        if not self._session:
            return []
            
        try:
            async with self._session.get(url, timeout=10) as response:
                content = await response.text()
                feed = feedparser.parse(content)
                
                entries = []
                for item in feed.entries[:5]:  # Топ 5 последних
                    entries.append({
                        'title': item.title,
                        'summary': item.get('summary', ''),
                        'published': item.get('published_parsed'),
                        'link': item.get('link', '')
                    })
                return entries
        except Exception as e:
            print(f"[NewsAggregator] Failed to fetch {url}: {e}")
            return []

    def _analyze_sentiment(self, text: str) -> tuple[float, float]:
        """
        Анализ тональности текста.
        Возвращает (direction, strength).
        direction: -1.0 (медвежье) до 1.0 (бычье).
        strength: 0.0 до 1.0.
        """
        text_lower = text.lower()
        
        bull_count = sum(1 for kw in self.bullish_keywords if kw in text_lower)
        bear_count = sum(1 for kw in self.bearish_keywords if kw in text_lower)
        
        total = bull_count + bear_count
        if total == 0:
            return 0.0, 0.0
            
        # Направление
        direction = (bull_count - bear_count) / total
        
        # Сила (зависит от количества триггеров)
        strength = min(1.0, total / 5.0)  # Нормализация
        
        return direction, strength

    def _detect_asset(self, text: str) -> Optional[str]:
        """Определение, к какому активу относится новость."""
        text_lower = text.lower()
        
        # Проверка DOGE
        for kw in self.asset_keywords['DOGE']:
            if kw in text_lower:
                return 'DOGEUSDT'
                
        # Проверка BTC (как коррелятор)
        for kw in self.asset_keywords['BTC']:
            if kw in text_lower:
                return 'BTCUSDT'
                
        return None

    async def _analyze_and_dispatch(self, entry: Dict, symbols: List[str]):
        """Анализ новости и отправка вектора в матрицу."""
        title = entry.get('title', '')
        summary = entry.get('summary', '')
        full_text = f"{title} {summary}"
        
        # Определение актива
        asset = self._detect_asset(full_text)
        if not asset or asset not in symbols:
            return  # Новость не относится к нашим символам
            
        # Анализ тональности
        direction, strength = self._analyze_sentiment(full_text)
        if strength == 0:
            return  # Нейтральная новость
            
        # Оценка вероятности (зависит от источника)
        # Для простоты: 0.7 для известных источников
        probability = 0.7
        
        # Время действия (в секундах)
        # Важные новости живут дольше
        duration = 300 if strength > 0.5 else 120
        
        vector = NewsVector(
            direction=direction,
            strength=strength,
            duration_sec=duration,
            probability=probability,
            source_id="rss_aggregator",
            headline=title
        )
        
        # Запись в матрицу
        await self.field.update_news_vector(asset, vector)
        print(f"[NewsAggregator] News for {asset}: {title[:50]}... (dir={direction:.2f}, str={strength:.2f})")
