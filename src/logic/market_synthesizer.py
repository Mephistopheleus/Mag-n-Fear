"""
Market Synthesizer - "Мозг" системы.
Объединяет данные от всех анализаторов в единую модель рынка (MarketModel).
"""
import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum

class MarketTrend(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    SIDEWAYS = "sideways"
    UNKNOWN = "unknown"

@dataclass
class MarketLevel:
    price: float
    strength: float  # 0.0 - 1.0
    type: str  # 'support', 'resistance'
    timeframe: str

@dataclass
class MarketSentiment:
    aggression: float  # 0.0 - 1.0 (сила движения)
    fear: float        # 0.0 - 1.0
    greed: float       # 0.0 - 1.0
    news_impact: float # -1.0 to 1.0

@dataclass
class MarketModel:
    """Единая модель рынка, синтезированная из всех данных."""
    timestamp: float
    symbol: str
    current_price: float
    
    # Тренды на разных таймфреймах
    trend_short: MarketTrend = MarketTrend.UNKNOWN
    trend_mid: MarketTrend = MarketTrend.UNKNOWN
    trend_long: MarketTrend = MarketTrend.UNKNOWN
    
    # Ключевые уровни
    levels: List[MarketLevel] = field(default_factory=list)
    
    # Настроение рынка
    sentiment: MarketSentiment = field(default_factory=lambda: MarketSentiment(0, 0, 0, 0))
    
    # Волатильность
    volatility: float = 0.0
    
    # Дополнительные метрики
    volume_profile: Dict[str, float] = field(default_factory=dict)
    order_flow_imbalance: float = 0.0
    
    def get_dominant_trend(self) -> MarketTrend:
        """Определяет доминирующий тренд."""
        trends = [self.trend_short, self.trend_mid, self.trend_long]
        if trends.count(MarketTrend.BULLISH) > trends.count(MarketTrend.BEARISH):
            return MarketTrend.BULLISH
        elif trends.count(MarketTrend.BEARISH) > trends.count(MarketTrend.BULLISH):
            return MarketTrend.BEARISH
        return MarketTrend.SIDEWAYS

    def is_contradictory(self, direction: str, price: float) -> bool:
        """Проверяет, противоречит ли направление сделки модели."""
        dominant = self.get_dominant_trend()
        
        # Если покупаем против сильного медвежьего тренда на важном сопротивлении
        if direction == "BUY" and dominant == MarketTrend.BEARISH:
            for level in self.levels:
                if level.type == 'resistance' and abs(level.price - price) < price * 0.005:
                    return True
        
        # Если продаем против бычьего тренда на поддержке
        if direction == "SELL" and dominant == MarketTrend.BULLISH:
            for level in self.levels:
                if level.type == 'support' and abs(level.price - price) < price * 0.005:
                    return True
                    
        return False

class MarketSynthesizer:
    """
    Синтезирует единую картину рынка из разрозненных данных.
    """
    def __init__(self, config: Any, symbol: str = "DOGEUSDT"):
        self.config = config
        # Пытаемся получить символ из разных мест конфига
        if hasattr(config, 'data') and hasattr(config.data, 'symbols'):
            self.symbol = config.data.symbols[0] if config.data.symbols else symbol
        elif isinstance(config, dict):
            self.symbol = config.get('data', {}).get('symbols', [symbol])[0]
        else:
            self.symbol = symbol
        self.latest_model: Optional[MarketModel] = None
        self._lock = asyncio.Lock()

    async def synthesize(
        self,
        current_price: float,
        analysis_points: List[Any],
        market_data: Dict[str, Any]
    ) -> MarketModel:
        """
        Создает новую модель рынка на основе свежих данных.
        
        Args:
            current_price: Текущая цена актива.
            analysis_points: Точки прогнозов из ProbabilityField.
            market_data: Сырые данные (свечи, объем, стакан).
            
        Returns:
            MarketModel: Обновленная модель рынка.
        """
        async with self._lock:
            # 1. Анализ трендов (упрощенно, пока нет полноценных индикаторов)
            trend_short = self._detect_trend(market_data.get('candles_short', []))
            trend_mid = self._detect_trend(market_data.get('candles_mid', []))
            trend_long = self._detect_trend(market_data.get('candles_long', []))

            # 2. Вычисление уровней поддержки/сопротивления
            levels = self._calculate_levels(market_data.get('history', []), current_price)

            # 3. Оценка настроения (агрессия, страх, жадность)
            sentiment = self._evaluate_sentiment(analysis_points, market_data)

            # 4. Расчет волатильности
            volatility = self._calculate_volatility(market_data.get('candles_short', []))

            # 5. Сборка модели
            model = MarketModel(
                timestamp=asyncio.get_event_loop().time(),
                symbol=self.symbol,
                current_price=current_price,
                trend_short=trend_short,
                trend_mid=trend_mid,
                trend_long=trend_long,
                levels=levels,
                sentiment=sentiment,
                volatility=volatility,
                order_flow_imbalance=market_data.get('order_flow_imbalance', 0.0)
            )
            
            self.latest_model = model
            return model

    def _detect_trend(self, candles: List[Dict]) -> MarketTrend:
        """Простая эвристика для определения тренда."""
        if not candles or len(candles) < 3:
            return MarketTrend.UNKNOWN
        
        closes = [c['close'] for c in candles[-5:]]
        if closes[-1] > closes[0] * 1.002:
            return MarketTrend.BULLISH
        elif closes[-1] < closes[0] * 0.998:
            return MarketTrend.BEARISH
        return MarketTrend.SIDEWAYS

    def _calculate_levels(self, history: List[Dict], current_price: float) -> List[MarketLevel]:
        """Вычисляет ключевые уровни на основе истории."""
        levels = []
        if not history:
            return levels
            
        # Ищем локальные максимумы и минимумы
        highs = [h['high'] for h in history[-50:]]
        lows = [l['low'] for l in history[-50:]]
        
        if highs:
            max_high = max(highs)
            levels.append(MarketLevel(
                price=max_high,
                strength=0.9,
                type='resistance',
                timeframe='short'
            ))
            
        if lows:
            min_low = min(lows)
            levels.append(MarketLevel(
                price=min_low,
                strength=0.9,
                type='support',
                timeframe='short'
            ))
            
        return levels

    def _evaluate_sentiment(self, points: List[Any], data: Dict) -> MarketSentiment:
        """Оценивает настроение рынка на основе точек прогнозов."""
        if not points:
            return MarketSentiment(0.5, 0.5, 0.5, 0)
            
        # Анализируем распределение прогнозов
        buy_pressure = sum(1 for p in points if getattr(p, 'price', 0) > data.get('current_price', 0))
        total = len(points)
        
        ratio = buy_pressure / total if total > 0 else 0.5
        
        aggression = abs(ratio - 0.5) * 2  # 0..1
        greed = ratio
        fear = 1 - ratio
        
        # Влияние новостей (если есть)
        news_impact = data.get('news_impact', 0.0)
        
        return MarketSentiment(
            aggression=aggression,
            fear=fear,
            greed=greed,
            news_impact=news_impact
        )

    def _calculate_volatility(self, candles: List[Dict]) -> float:
        """Считает волатильность."""
        if not candles or len(candles) < 2:
            return 0.0
            
        ranges = [(c['high'] - c['low']) / c['open'] for c in candles[-10:]]
        return sum(ranges) / len(ranges) if ranges else 0.0

    def get_model(self) -> Optional[MarketModel]:
        """Возвращает последнюю синтезированную модель."""
        return self.latest_model
