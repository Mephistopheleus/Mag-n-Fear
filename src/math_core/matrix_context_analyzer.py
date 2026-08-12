"""
MatrixContextAnalyzer - Мозг системы.
Анализирует полную матрицу вероятностей, выполняет кросс-валидацию данных от всех анализаторов,
определяет режим рынка и генерирует обогащенные поля для принятия решений.

НЕ выдает сигналы BUY/SELL. Выдает контекст: цели, время, вероятность, причину.
"""

import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import uuid

# Импорты анализаторов для сбора данных
try:
    from .liquidity_density_analyzer import LiquidityDensityAnalyzer
    from .atr_indicator import ATRIndicator
    from .g_trend_channel import G_TrendChannel
    from .fibonacci_levels import FibonacciLevels
    from .fractal_patterns import FractalPatternAnalyzer
    from .market_regime import MarketRegimeDetector
    from .multi_correlation_analyzer import MultiCorrelationAnalyzer
    ANALYZERS_AVAILABLE = True
except ImportError as e:
    ANALYZERS_AVAILABLE = False
    print(f"WARNING: MatrixContextAnalyzer - ошибка импорта анализаторов: {e}")

@dataclass
class MarketRegime:
    """Структура описания текущего режима рынка"""
    regime_type: str  # 'TREND_UP', 'TREND_DOWN', 'FLAT', 'VOLATILE', 'ACCUMULATION', 'DISTRIBUTION'
    confidence: float
    volatility_level: str  # 'LOW', 'MEDIUM', 'HIGH', 'EXTREME'
    trend_strength: float
    description: str

@dataclass
class MatrixInsight:
    """Результат анализа матрицы: конкретное поле вероятности"""
    insight_id: str
    insight_type: str  # 'TARGET_ZONE', 'TIME_WINDOW', 'LIQUIDITY_TRAP', 'FALSE_BREAKOUT_RISK', 'CONFLUENCE_ZONE'
    
    # Координаты события
    target_price: Optional[float] = None
    target_time_sec: Optional[int] = None  # Прогноз времени до события
    probability: float = 0.0
    
    # Контекст
    reason: str = ""
    supporting_analyzers: List[str] = field(default_factory=list)
    regime_context: Optional[str] = None
    
    # Дополнительные данные
    data: Dict[str, Any] = field(default_factory=dict)

class MatrixContextAnalyzer:
    """
    Агрегирует данные всех анализаторов, ищет конфлюэнции и противоречия,
    определяет режим рынка и создает обогащенные поля вероятностей.
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.instance_id = str(uuid.uuid4())[:8]
        
        # Инициализация под-анализаторов (если доступны)
        if ANALYZERS_AVAILABLE:
            self.liquidity_analyzer = LiquidityDensityAnalyzer(config)
            self.atr_analyzer = ATRIndicator(config)
            self.trend_analyzer = G_TrendChannel(config)
            self.fib_analyzer = FibonacciLevels(config)
            self.pattern_analyzer = FractalPatternAnalyzer(config)
            self.slice_analyzer = MarketRegimeDetector(config)
            self.corr_analyzer = MultiCorrelationAnalyzer(config)
        else:
            self.liquidity_analyzer = None
            self.atr_analyzer = None
            self.trend_analyzer = None
            self.fib_analyzer = None
            self.pattern_analyzer = None
            self.slice_analyzer = None
            self.corr_analyzer = None
            
        self.last_regime: Optional[MarketRegime] = None

    async def analyze(self, matrix_snapshot: Dict[str, Any]) -> List[MatrixInsight]:
        """
        Главный метод анализа.
        Вход: снимок всей матрицы вероятностей (данные всех анализаторов).
        Выход: список инсайтов (обогащенных полей вероятности).
        """
        insights = []
        
        # 1. Определение режима рынка
        regime = await self._detect_market_regime(matrix_snapshot)
        self.last_regime = regime
        
        # 2. Кросс-валидация данных (поиск конфлюэнций)
        confluence_insights = await self._find_confluences(matrix_snapshot, regime)
        insights.extend(confluence_insights)
        
        # 3. Поиск временных окон (Time Resonance)
        time_insights = await self._find_time_windows(matrix_snapshot, regime)
        insights.extend(time_insights)
        
        # 4. Оценка рисков (ловушки, ложные пробои)
        risk_insights = await self._assess_risks(matrix_snapshot, regime)
        insights.extend(risk_insights)
        
        # 5. Целевые зоны (на основе ликвидности и паттернов)
        target_insights = await self._identify_target_zones(matrix_snapshot, regime)
        insights.extend(target_insights)
        
        return insights

    async def _detect_market_regime(self, matrix: Dict[str, Any]) -> MarketRegime:
        """
        Определяет текущий режим рынка на основе совокупности факторов.
        """
        # Логика определения режима (упрощенная версия, требует настройки тюнером)
        volatility = matrix.get('atr', {}).get('current_atr', 0)
        trend_strength = matrix.get('g_trend', {}).get('strength', 0)
        liquidity_balance = matrix.get('liquidity', {}).get('balance', 0)
        
        regime_type = 'FLAT'
        confidence = 0.5
        description = "Неопределенность"
        
        if trend_strength > 0.7 and volatility > 0:
            direction = "UP" if matrix.get('g_trend', {}).get('direction', 0) > 0 else "DOWN"
            regime_type = f"TREND_{direction}"
            confidence = 0.8
            description = f"Сильный тренд {direction}"
        elif volatility > 1.5: # Высокая волатильность без тренда
            regime_type = 'VOLATILE'
            confidence = 0.7
            description = "Высокая волатильность, возможен пробой"
        elif liquidity_balance < -0.5 or liquidity_balance > 0.5:
            regime_type = 'ACCUMULATION' if liquidity_balance > 0 else 'DISTRIBUTION'
            confidence = 0.6
            description = "Накопление/Распределение ликвидности"
            
        vol_level = 'MEDIUM'
        if volatility < 0.5: vol_level = 'LOW'
        elif volatility > 1.5: vol_level = 'HIGH'
        elif volatility > 2.5: vol_level = 'EXTREME'
        
        return MarketRegime(
            regime_type=regime_type,
            confidence=confidence,
            volatility_level=vol_level,
            trend_strength=trend_strength,
            description=description
        )

    async def _find_confluences(self, matrix: Dict[str, Any], regime: MarketRegime) -> List[MatrixInsight]:
        """
        Ищет зоны, где совпадают сигналы от разных анализаторов (конфлюэнция).
        """
        insights = []
        # Пример логики: если уровень Фибоначчи совпадает с POC ликвидности и линией тренда
        fib_levels = matrix.get('fibonacci', {}).get('levels', [])
        poc_price = matrix.get('liquidity', {}).get('poc_price', None)
        trend_lines = matrix.get('trend', {}).get('lines', [])
        
        if poc_price and fib_levels:
            for level in fib_levels:
                if abs(level['price'] - poc_price) < (poc_price * 0.001): # Совпадение с точностью 0.1%
                    insights.append(MatrixInsight(
                        insight_id=str(uuid.uuid4())[:8],
                        insight_type='CONFLUENCE_ZONE',
                        target_price=poc_price,
                        probability=0.85, # Высокая вероятность из-за совпадения
                        reason=f"Совпадение POC ликвидности и уровня Фибоначчи {level['type']}",
                        supporting_analyzers=['liquidity', 'fibonacci'],
                        regime_context=regime.regime_type,
                        data={'fib_level': level['type'], 'poc_density': matrix.get('liquidity', {}).get('max_density', 0)}
                    ))
        return insights

    async def _find_time_windows(self, matrix: Dict[str, Any], regime: MarketRegime) -> List[MatrixInsight]:
        """
        Определяет временные окна высокой вероятности движения.
        """
        insights = []
        # Логика: анализ волатильности по времени суток + открытие сессий
        current_hour = datetime.utcnow().hour
        
        # Упрощенная эвристика (должна быть заменена на реальный анализ исторической волатильности по времени)
        if current_hour in [9, 10, 15, 16]: # Открытие Лондона и Нью-Йорка
            insights.append(MatrixInsight(
                insight_id=str(uuid.uuid4())[:8],
                insight_type='TIME_WINDOW',
                target_time_sec=1800, # Ожидание движения в ближайшие 30 мин
                probability=0.75,
                reason="Открытие крупной биржевой сессии",
                supporting_analyzers=['time_analysis'],
                regime_context=regime.regime_type,
                data={'session': 'London/NY'}
            ))
        return insights

    async def _assess_risks(self, matrix: Dict[str, Any], regime: MarketRegime) -> List[MatrixInsight]:
        """
        Оценивает риски: ловушки ликвидности, ложные пробои.
        """
        insights = []
        liquidity_zones = matrix.get('liquidity', {}).get('zones', [])
        
        # Поиск вакуумов ликвидности (риск быстрого проскальзывания)
        voids = [z for z in liquidity_zones if z.get('type') == 'VOID']
        if voids:
            for void in voids:
                insights.append(MatrixInsight(
                    insight_id=str(uuid.uuid4())[:8],
                    insight_type='LIQUIDITY_VOID_RISK',
                    target_price=void.get('center_price'),
                    probability=0.7,
                    reason="Зона низкой ликвидности (вакуум)",
                    supporting_analyzers=['liquidity'],
                    regime_context=regime.regime_type,
                    data={'void_width': void.get('width'), 'risk_level': 'HIGH'}
                ))
                
        # Риск ложного пробоя при высокой волатильности
        if regime.volatility_level == 'EXTREME' and regime.regime_type == 'FLAT':
            insights.append(MatrixInsight(
                insight_id=str(uuid.uuid4())[:8],
                insight_type='FALSE_BREAKOUT_RISK',
                probability=0.8,
                reason="Высокая волатильность во флэте повышает риск ложных пробоев",
                supporting_analyzers=['atr', 'regime_detection'],
                regime_context=regime.regime_type,
                data={'risk_level': 'CRITICAL'}
            ))
            
        return insights

    async def _identify_target_zones(self, matrix: Dict[str, Any], regime: MarketRegime) -> List[MatrixInsight]:
        """
        Определяет целевые зоны для движения цены.
        """
        insights = []
        poc = matrix.get('liquidity', {}).get('poc_price')
        current_price = matrix.get('current_price', 0)
        
        if poc and current_price:
            direction = "UP" if current_price < poc else "DOWN"
            insights.append(MatrixInsight(
                insight_id=str(uuid.uuid4())[:8],
                insight_type='TARGET_ZONE',
                target_price=poc,
                probability=0.65, # Базовая вероятность тяги к ликвидности
                reason=f"Магнит ликвидности (POC) находится {direction}",
                supporting_analyzers=['liquidity'],
                regime_context=regime.regime_type,
                data={'poc_distance_pct': abs(poc - current_price) / current_price * 100}
            ))
            
        # Цели на основе графических паттернов
        patterns = matrix.get('patterns', [])
        for pattern in patterns:
            if pattern.get('target_price'):
                insights.append(MatrixInsight(
                    insight_id=str(uuid.uuid4())[:8],
                    insight_type='PATTERN_TARGET',
                    target_price=pattern['target_price'],
                    target_time_sec=pattern.get('timeframe_seconds', 3600),
                    probability=pattern.get('confidence', 0.5),
                    reason=f"Цель графического паттерна {pattern.get('type')}",
                    supporting_analyzers=['patterns'],
                    regime_context=regime.regime_type,
                    data=pattern
                ))
                
        return insights

    def get_regime_summary(self) -> Optional[Dict[str, Any]]:
        """Возвращает краткую сводку по текущему режиму рынка."""
        if not self.last_regime:
            return None
        return {
            'regime': self.last_regime.regime_type,
            'confidence': self.last_regime.confidence,
            'volatility': self.last_regime.volatility_level,
            'description': self.last_regime.description
        }
