"""
ATR Indicator (Average True Range)
Расчет фактической волатильности на основе полотна свечей.
Используется как ФАКТ для Risk Manager и Trailing Stop.
Не является прогнозом, поэтому confidence = 1.0 (фактические данные).
"""
from typing import Dict, Any, List
import polars as pl
import numpy as np

from .base_indicator import BaseIndicator
from .registry import register_indicator


@register_indicator
class ATRIndicator(BaseIndicator):
    """
    Индикатор ATR для оценки текущей волатильности рынка.
    Возвращает фактическое значение ATR и его динамику.
    """

    def __init__(self, name: str, config: Dict[str, Any] = None):
        super().__init__(name, config)
        self.period = self.config.get('period', 14)
        # ATR используется для динамических стопов и расчета размера позиции

    def calculate(self, data: pl.DataFrame, current_price: float) -> Dict[str, Any]:
        """
        Расчет ATR на основе данных свечи (OHLCV).
        
        Args:
            data: DataFrame с колонками ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            current_price: Текущая цена (для совместимости, не используется в расчете ATR)
        
        Returns:
            Dict с результатами:
            - 'atr_value': Фактическое значение ATR
            - 'volatility_trend': Направление изменения волатильности (1-up, -1-down, 0-stable)
            - 'probability': 1.0 (это факт, не прогноз)
            - 'tags': ['volatility', 'atr']
            - 'metadata': {'atr_raw', 'period', 'prev_atr'}
        """
        if not self.validate_data(data):
            # Пытаемся работать с OHLCV данными даже если валидация не прошла
            if 'high' not in data.columns or 'low' not in data.columns:
                return {
                    'error': 'MISSING_OHLC_DATA',
                    'atr_value': 0.0,
                    'volatility_trend': 0,
                    'probability': 0.0,
                    'tags': ['volatility', 'error'],
                    'metadata': {'error': 'No high/low columns'}
                }

        df = data.tail(self.period * 2)  # Берем с запасом для расчета предыдущего ATR
        
        if len(df) < self.period + 1:
            return {
                'error': 'INSUFFICIENT_DATA',
                'atr_value': 0.0,
                'volatility_trend': 0,
                'probability': 0.0,
                'tags': ['volatility', 'error'],
                'metadata': {'error': f'Need {self.period + 1} candles, got {len(df)}'}
            }

        # Извлекаем High, Low, Close
        highs = df['high'].to_numpy()
        lows = df['low'].to_numpy()
        closes = df['close'].to_numpy()

        # Расчет True Range
        tr1 = highs[1:] - lows[1:]
        tr2 = np.abs(highs[1:] - closes[:-1])
        tr3 = np.abs(lows[1:] - closes[:-1])
        true_range = np.maximum(np.maximum(tr1, tr2), tr3)

        # Расчет текущего ATR (простое среднее за период)
        current_atr = np.mean(true_range[-self.period:])
        
        # Расчет предыдущего ATR для определения тренда волатильности
        prev_period_start = max(0, len(true_range) - self.period * 2)
        prev_period_end = len(true_range) - self.period
        if prev_period_end > prev_period_start:
            prev_atr = np.mean(true_range[prev_period_start:prev_period_end])
        else:
            prev_atr = current_atr  # Если данных мало, считаем равным

        # Определение направления волатильности
        volatility_change = current_atr - prev_atr
        if volatility_change > current_atr * 0.1:  # Рост > 10%
            vol_trend = 1
        elif volatility_change < -current_atr * 0.1:  # Падение > 10%
            vol_trend = -1
        else:
            vol_trend = 0

        return {
            'target_price': current_price,  # Для совместимости интерфейса
            'time_sec': 300,  # Актуально на текущий момент (5 мин)
            'probability': 1.0,  # Это факт, уверенность 100%
            'tags': ['volatility', 'atr', 'fact'],
            'metadata': {
                'atr_value': float(current_atr),
                'atr_raw': float(current_atr),
                'prev_atr': float(prev_atr),
                'volatility_trend': vol_trend,
                'period': self.period,
                'trend_direction': 'increasing' if vol_trend > 0 else ('decreasing' if vol_trend < 0 else 'stable')
            }
        }
