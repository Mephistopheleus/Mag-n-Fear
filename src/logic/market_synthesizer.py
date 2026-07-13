"""
Market Synthesizer - "Мозг" системы.
Объединяет данные от всех анализаторов в единую модель рынка (MarketModel).
"""
import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

# Импорт анализатора стакана
from src.math_core.order_book_sr import OrderBookAnalyzer, SRLevel

# Импорт корреляционного движка
from src.correlation_engine import CorrelationEngine

# Импорт гармонического анализатора
from src.harmonic_analyzer import HarmonicAnalyzer

logger = logging.getLogger(__name__)

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
    
    # Метрики корреляции (добавлено для интеграции CorrelationEngine)
    corr_btc: float = 0.0
    divergence_score: float = 0.0
    
    # Гармонические паттерны (добавлено для интеграции HarmonicAnalyzer)
    harmonic_patterns: List[Dict] = field(default_factory=list)
    g_channel: Optional[Dict] = None
    
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
        
        # Инициализация анализатора стакана
        self.ob_analyzer = OrderBookAnalyzer(config)
        
        # Инициализация корреляционного движка
        self.corr_engine = CorrelationEngine(config)
        
        # Инициализация гармонического анализатора
        self.harmonic_analyzer = HarmonicAnalyzer(config)
        
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
            # Объединяем уровни из свечей и из стакана
            levels = self._calculate_levels(market_data.get('history', []), current_price)
            
            # Добавляем уровни из стакана, если есть данные
            order_book = market_data.get('order_book')
            if order_book and self.ob_analyzer:
                bids = order_book.get('bids', [])
                asks = order_book.get('asks', [])
                ob_levels = self.ob_analyzer.analyze_snapshot(bids, asks, current_price)
                
                # Конвертируем SRLevel в MarketLevel и добавляем к общему списку
                for sr_level in ob_levels.get('support', []):
                    levels.append(MarketLevel(
                        price=sr_level.price,
                        strength=sr_level.strength,
                        type='support',
                        timeframe='order_book'
                    ))
                for sr_level in ob_levels.get('resistance', []):
                    levels.append(MarketLevel(
                        price=sr_level.price,
                        strength=sr_level.strength,
                        type='resistance',
                        timeframe='order_book'
                    ))
            
            # === ШАГ 1: Интеграция Correlation Engine ===
            # Обновляем цены для корреляционного анализа
            candles_short = market_data.get('candles_short', [])
            if candles_short:
                for candle in candles_short[-10:]:
                    timestamp = candle.get('time', 0)
                    price = candle.get('close', current_price)
                    self.corr_engine.update_price(self.symbol, price, timestamp)
            
            # Получаем корреляционные сигналы
            corr_signals = self.corr_engine.get_correlation_signals()
            corr_btc = corr_signals.get(self.symbol, {}).get('btc_correlation', 0.0)
            divergence_score = corr_signals.get(self.symbol, {}).get('divergence_score', 0.0)
            
            logger.debug(f"Correlation: {self.symbol}/BTC={corr_btc:.3f}, divergence={divergence_score:.3f}")
            
            # === ШАГ 1: Интеграция Harmonic Analyzer ===
            # Ищем гармонические паттерны и G-Channel
            harm_patterns = []
            g_channel = None
            
            if candles_short and len(candles_short) >= 20:
                # Преобразуем свечи в pandas Series для анализатора
                import pandas as pd
                prices = pd.Series([c['close'] for c in candles_short])
                
                # Поиск паттернов Гартли
                gartley = self.harmonic_analyzer.detect_pattern(prices, 'gartley')
                if gartley:
                    harm_patterns.append(gartley)
                    logger.info(f"Harmonic pattern detected: Gartley on {self.symbol}")
                
                # Поиск G-Trend Channel
                g_channel = self.harmonic_analyzer.calculate_g_channel(prices)
                if g_channel:
                    logger.debug(f"G-Channel on {self.symbol}: position={g_channel['position']:.2f}, trend={g_channel['trend']}")
                    
                    # Добавляем уровни из G-Channel в общий пул
                    levels.append(MarketLevel(
                        price=g_channel['upper'],
                        strength=g_channel['confidence'],
                        type='resistance',
                        timeframe='g_channel'
                    ))
                    levels.append(MarketLevel(
                        price=g_channel['lower'],
                        strength=g_channel['confidence'],
                        type='support',
                        timeframe='g_channel'
                    ))
            
            # Сортируем уровни по силе и убираем дубликаты (близкие цены)
            levels = self._merge_close_levels(levels, current_price)

            # 3. Оценка настроения (агрессия, страх, жадность)
            sentiment = self._evaluate_sentiment(analysis_points, market_data)

            # 4. Расчет волатильности
            volatility = self._calculate_volatility(market_data.get('candles_short', []))

            # 5. Сборка модели с добавлением метаданных корреляции и гармонии
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
            
            # Сохраняем корреляционные данные в модели для передачи в сценарии
            model.corr_btc = corr_btc
            model.divergence_score = divergence_score
            model.harmonic_patterns = harm_patterns
            model.g_channel = g_channel
            
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

    def _merge_close_levels(self, levels: List[MarketLevel], current_price: float) -> List[MarketLevel]:
        """
        Объединяет близкие уровни поддержки/сопротивления и сортирует по силе.
        Уровни считаются близкими, если разница < 0.1%.
        """
        if not levels:
            return []
        
        merged = []
        tolerance = 0.001  # 0.1%
        
        # Сортируем по цене
        sorted_levels = sorted(levels, key=lambda x: x.price)
        
        i = 0
        while i < len(sorted_levels):
            current_level = sorted_levels[i]
            similar_levels = [current_level]
            
            # Ищем похожие уровни рядом
            j = i + 1
            while j < len(sorted_levels):
                next_level = sorted_levels[j]
                diff_pct = abs(next_level.price - current_level.price) / current_price
                
                if diff_pct <= tolerance and next_level.type == current_level.type:
                    similar_levels.append(next_level)
                    j += 1
                else:
                    break
            
            # Если нашли несколько похожих, берем самый сильный
            if len(similar_levels) > 1:
                best = max(similar_levels, key=lambda x: x.strength)
                # Усиливаем уровень за счет количества совпадений
                best.strength = min(1.0, best.strength * (1 + 0.1 * len(similar_levels)))
                merged.append(best)
            else:
                merged.append(current_level)
            
            i = j
        
        # Сортируем итоговый список по силе (убывание)
        merged.sort(key=lambda x: x.strength, reverse=True)
        return merged[:10]  # Возвращаем топ-10 уровней

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
