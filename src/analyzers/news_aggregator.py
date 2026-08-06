"""
NewsAggregator: Модуль сбора и анализа новостей для формирования поля вероятностей.
Источники: Finnhub, NewsAPI, FRED.
Логика: Нормализация сентимента, взвешивание, экспоненциальное затухание влияния.
"""

import asyncio
import aiohttp
import math
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

# Импорты внутренних модулей (предполагаемая структура)
try:
    from src.core.data_card import AnalysisCard
    from src.config.config_loader import Config
except ImportError:
    # Для автономного тестирования или если пути отличаются
    AnalysisCard = None
    Config = None


@dataclass
class NewsItem:
    """Сырая новость от провайдера."""
    source: str
    headline: str
    sentiment_raw: float  # Сырой сентимент (-1..1 или 0..1)
    timestamp: float
    relevance: float  # Релевантность конкретным активам (0..1)
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NewsImpactPoint:
    """Точка влияния новости во времени."""
    start_time: float
    peak_impact: float  # Пиковое влияние (-1..1)
    decay_rate: float   # Коэффициент затухания (lambda)
    duration_sec: float # Длительность значимого влияния
    confidence: float   # Уверенность в прогнозе
    direction: str      # 'BULLISH', 'BEARISH', 'NEUTRAL'


class NewsAggregator:
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.api_keys = {
            'finnhub': self.config.get('finnhub_api_key', ''),
            'newsapi': self.config.get('newsapi_key', ''),
            'fred': self.config.get('fred_api_key', '')
        }
        
        # Веса источников (настраиваемые через конфиг)
        self.source_weights = {
            'finnhub': self.config.get('news_weight_finnhub', 1.0),
            'newsapi': self.config.get('news_weight_newsapi', 0.8),
            'fred': self.config.get('news_weight_fred', 0.9)
        }
        
        # Параметры затухания (период полураспада в секундах, по умолчанию 15 мин)
        self.half_life_sec = self.config.get('news_half_life_sec', 900)
        self.decay_lambda = math.log(2) / self.half_life_sec
        
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_fetch_time = 0.0
        self.fetch_interval = 60  # Опрос раз в минуту

    async def start(self):
        """Инициализация сессии aiohttp."""
        if not self.session:
            self.session = aiohttp.ClientSession(headers={'User-Agent': 'TradingBot/2.0'})

    async def stop(self):
        """Закрытие сессии."""
        if self.session:
            await self.session.close()
            self.session = None

    async def fetch_all_news(self) -> List[NewsItem]:
        """Асинхронный сбор новостей со всех источников."""
        if not self.session:
            await self.start()
            
        tasks = []
        if self.api_keys['finnhub']:
            tasks.append(self._fetch_finnhub())
        if self.api_keys['newsapi']:
            tasks.append(self._fetch_newsapi())
        if self.api_keys['fred']:
            tasks.append(self._fetch_fred())
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        news_items = []
        for res in results:
            if isinstance(res, Exception):
                # Логирование ошибки, но не прерывание работы
                print(f"[NewsAggregator] Error fetching source: {res}")
                continue
            if isinstance(res, list):
                news_items.extend(res)
                
        self.last_fetch_time = time.time()
        return news_items

    async def _fetch_finnhub(self) -> List[NewsItem]:
        """Finnhub: Крипто-новости с готовым сентиментом."""
        items = []
        try:
            # Пример: общие крипто-новости
            url = "https://finnhub.io/api/v1/crypto-news"
            params = {'category': 'general', 'token': self.api_keys['finnhub']}
            
            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data[:10]: # Ограничение на последние 10
                        # Finnhub иногда отдает sentimentScore, иногда нет
                        sentiment = item.get('sentimentScore', 0.0) 
                        # Нормализация если нужно (Finnhub обычно -1..1)
                        items.append(NewsItem(
                            source='finnhub',
                            headline=item.get('headline', ''),
                            sentiment_raw=sentiment,
                            timestamp=item.get('datetime', time.time()) / 1000.0,
                            relevance=1.0, # Крипто-новости релевантны по умолчанию
                            raw_data=item
                        ))
        except Exception as e:
            raise e
        return items

    async def _fetch_newsapi(self) -> List[NewsItem]:
        """NewsAPI: Макро и общие новости. Требует локального расчета сентимента."""
        items = []
        try:
            # Простой запрос последних новостей по ключевым словам
            # В продакшене нужно расширить список query
            query = 'crypto OR bitcoin OR ethereum OR inflation OR fed'
            url = "https://newsapi.org/v2/everything"
            params = {
                'q': query,
                'sortBy': 'publishedAt',
                'pageSize': 10,
                'apiKey': self.api_keys['newsapi']
            }
            
            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('status') == 'ok':
                        for article in data.get('articles', []):
                            # VADER-подобная эвристика для заголовка (упрощенно)
                            # В полной версии подключить nltk.vader
                            sentiment = self._calculate_simple_sentiment(article.get('title', ''))
                            
                            items.append(NewsItem(
                                source='newsapi',
                                headline=article.get('title', ''),
                                sentiment_raw=sentiment,
                                timestamp=datetime.fromisoformat(article['publishedAt'].replace('Z', '+00:00')).timestamp(),
                                relevance=0.8, # Чуть ниже, так как общий фон
                                raw_data=article
                            ))
        except Exception as e:
            raise e
        return items

    async def _fetch_fred(self) -> List[NewsItem]:
        """FRED: Макроэкономические данные (процентные ставки, ВВП и т.д.)."""
        items = []
        # Реализация требует парсинга конкретных серий данных
        # Для краткости примера - заглушка логики, но без имитации данных
        # Если ключ есть, пытаемся получить последние значения ключевых индикаторов
        if not self.api_keys['fred']:
            return items
            
        # Здесь должен быть код запроса к https://api.stlouisfed.org/fred/series/observations
        # Возвращаем пустой список, если нет конкретной реализации под серию, 
        # чтобы не генерировать мусор.
        return items

    def _calculate_simple_sentiment(self, text: str) -> float:
        """
        Упрощенный расчет сентимента (замена VADER для легковесности).
        Возвращает float от -1.0 до 1.0.
        """
        text = text.lower()
        positive_words = ['rise', 'gain', 'surge', 'bull', 'positive', 'growth', 'up', 'record']
        negative_words = ['fall', 'drop', 'crash', 'bear', 'negative', 'loss', 'down', 'risk']
        
        score = 0
        total = 0
        
        for word in positive_words:
            if word in text:
                score += 1
                total += 1
        for word in negative_words:
            if word in text:
                score -= 1
                total += 1
                
        if total == 0:
            return 0.0
        
        return max(-1.0, min(1.0, score / total))

    def process_news_to_impacts(self, news_items: List[NewsItem]) -> List[NewsImpactPoint]:
        """Преобразование списка новостей в точки влияния с затуханием."""
        impacts = []
        current_time = time.time()
        
        for item in news_items:
            # Фильтрация старых новостей (старше 1 часа не учитываем)
            if current_time - item.timestamp > 3600:
                continue
                
            # Расчет взвешенного импульса
            weight = self.source_weights.get(item.source, 0.5)
            base_impact = item.sentiment_raw * item.relevance * weight
            
            # Определение направления
            if base_impact > 0.1:
                direction = 'BULLISH'
            elif base_impact < -0.1:
                direction = 'BEARISH'
            else:
                direction = 'NEUTRAL'
                continue # Пропускаем нейтральные, они не создают поля
            
            # Уверенность зависит от источника и свежести
            freshness_factor = math.exp(-self.decay_lambda * (current_time - item.timestamp))
            confidence = abs(base_impact) * freshness_factor
            
            impacts.append(NewsImpactPoint(
                start_time=item.timestamp,
                peak_impact=base_impact,
                decay_rate=self.decay_lambda,
                duration_sec=self.half_life_sec * 4, # 4 периода полураспада
                confidence=min(1.0, confidence),
                direction=direction
            ))
            
        return impacts

    def generate_analysis_cards(self, impacts: List[NewsImpactPoint], trading_symbols: List[str]) -> List[Any]:
        """
        Генерация AnalysisCard для каждого актива на основе новостного фона.
        Возвращает список объектов AnalysisCard (или dict, если класс не импортирован).
        """
        cards = []
        current_time = time.time()
        
        for symbol in trading_symbols:
            # Агрегируем влияние всех новостей на данный момент для символа
            # В упрощенном варианте считаем общее влияние одинаковым для всех крипто-активов
            # В сложном - нужно мапить новости на конкретные тикеры
            
            total_impulse = 0.0
            avg_confidence = 0.0
            count = 0
            
            for impact in impacts:
                # Расчет текущего значения импульса с учетом затухания
                time_diff = current_time - impact.start_time
                if 0 <= time_diff <= impact.duration_sec:
                    current_val = impact.peak_impact * math.exp(-impact.decay_rate * time_diff)
                    total_impulse += current_val
                    avg_confidence += impact.confidence
                    count += 1
            
            if count > 0:
                avg_confidence /= count
                # Нормализация общего импульса (ограничение -1..1)
                final_impulse = max(-1.0, min(1.0, total_impulse))
                
                # Формирование карточки
                card_data = {
                    'analyzer_type': 'NEWS_AGGREGATOR',
                    'symbol': symbol,
                    'timestamp': current_time,
                    'value': final_impulse, # -1..1
                    'confidence': avg_confidence,
                    'trust_points': 0.0, # Будет заполнено автотюнером
                    'horizon_sec': self.half_life_sec * 2,
                    'metadata': {
                        'active_news_count': count,
                        'dominant_direction': 'BULLISH' if final_impulse > 0 else 'BEARISH'
                    }
                }
                
                if AnalysisCard:
                    cards.append(AnalysisCard(**card_data))
                else:
                    cards.append(card_data)
                    
        return cards

    async def get_current_analysis(self, symbols: List[str]) -> List[Any]:
        """Полный цикл: сбор -> обработка -> выдача карточек."""
        news = await self.fetch_all_news()
        impacts = self.process_news_to_impacts(news)
        return self.generate_analysis_cards(impacts, symbols)
