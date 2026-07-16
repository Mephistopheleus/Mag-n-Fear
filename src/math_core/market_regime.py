"""
Детектор режима рынка (Market Regime Detection).
Определяет текущее состояние: Тренд, Флэт, Высокая волатильность, Паника.
Используется для фильтрации стратегий и настройки риск-менеджмента.
"""
import numpy as np
from typing import Dict, Tuple, List
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)

class MarketRegime(Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGING = "RANGING"  # Флэт
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"

@dataclass
class RegimeResult:
    regime: MarketRegime
    confidence: float  # 0.0 - 1.0
    metrics: Dict[str, float]  # Волатильность, ADX, и т.д.
    timestamp: float

class MarketRegimeDetector:
    def __init__(self, config=None):
        self.config = config
        # Пороги из конфига или дефолтные
        if config:
            regime_cfg = getattr(config, 'regime', None) or getattr(config, 'data', {})
            if isinstance(regime_cfg, dict):
                self.volatility_threshold_high = regime_cfg.get("volatility_high", 0.05)
                self.volatility_threshold_low = regime_cfg.get("volatility_low", 0.01)
                self.trend_threshold = regime_cfg.get("trend_threshold", 0.25)
            else:
                self.volatility_threshold_high = getattr(regime_cfg, 'volatility_high', 0.05)
                self.volatility_threshold_low = getattr(regime_cfg, 'volatility_low', 0.01)
                self.trend_threshold = getattr(regime_cfg, 'trend_threshold', 0.25)
        else:
            self.volatility_threshold_high = 0.05
            self.volatility_threshold_low = 0.01
            self.trend_threshold = 0.25

    def analyze(self, prices: List[float], volumes: List[float]) -> RegimeResult:
        """
        Анализирует массив цен и объемов для определения режима.
        Возвращает текущий режим и уверенность.
        """
        if len(prices) < 20:
            return RegimeResult(
                regime=MarketRegime.RANGING,
                confidence=0.0,
                metrics={"error": "insufficient_data"},
                timestamp=0.0
            )

        # 1. Расчет волатильности (стандартное отклонение логарифмических доходностей)
        returns = np.diff(np.log(prices))
        volatility = np.std(returns)

        # 2. Простая оценка тренда (линейная регрессия или наклон скользящей средней)
        # Упрощенно: сравнение текущей цены со средней за период
        ma_short = np.mean(prices[-10:])
        ma_long = np.mean(prices[-50:]) if len(prices) >= 50 else np.mean(prices)
        
        trend_strength = (ma_short - ma_long) / ma_long if ma_long != 0 else 0
        
        # Определение режима
        regime = MarketRegime.RANGING
        confidence = 0.5

        if volatility > self.volatility_threshold_high:
            regime = MarketRegime.HIGH_VOLATILITY
            confidence = min(1.0, volatility / 0.1) # Нормализация
        elif volatility < self.volatility_threshold_low:
            # Если волатильность очень низкая, проверяем ликвидность по объемам
            avg_vol = np.mean(volumes[-20:])
            if avg_vol < 1000: # Условный порог
                regime = MarketRegime.LOW_LIQUIDITY
                confidence = 0.8
            else:
                regime = MarketRegime.RANGING
                confidence = 0.7
        else:
            # Нормальная волатильность, смотрим на тренд
            if abs(trend_strength) > self.trend_threshold:
                if trend_strength > 0:
                    regime = MarketRegime.TREND_UP
                else:
                    regime = MarketRegime.TREND_DOWN
                confidence = min(1.0, abs(trend_strength) / 0.05)
            else:
                regime = MarketRegime.RANGING
                confidence = 0.6

        metrics = {
            "volatility": volatility,
            "trend_strength": trend_strength,
            "ma_short": ma_short,
            "ma_long": ma_long
        }

        result = RegimeResult(
            regime=regime,
            confidence=confidence,
            metrics=metrics,
            timestamp=0.0 # Заполнится при вызове
        )
        
        logger.debug(f"Regime detected: {regime.value} (Conf: {confidence:.2f})")
        return result
