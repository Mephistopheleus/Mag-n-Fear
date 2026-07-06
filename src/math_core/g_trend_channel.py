"""
Пример реализации индикатора: G-Trend Channel.
Современный инструмент: динамические каналы на основе линейной регрессии с весами.
"""
from typing import Dict, Any, List
import polars as pl
import numpy as np

from .base_indicator import BaseIndicator
from .registry import register_indicator


@register_indicator
class G_TrendChannel(BaseIndicator):
    """
    G-Trend Channel: Динамический канал тренда.
    Строит линейную регрессию по взвешенным ценам и добавляет полосы отклонения.
    """

    def __init__(self, name: str, config: Dict[str, Any] = None):
        super().__init__(name, config)
        self.period = self.config.get('period', 100)
        self.std_dev = self.config.get('std_dev', 2.0)
        self.use_volume_weights = self.config.get('use_volume_weights', True)

    def calculate(self, data: pl.DataFrame) -> Dict[str, Any]:
        if not self.validate_data(data):
            return {'error': 'Invalid data'}

        # Берем последние N периодов
        df = data.tail(self.period)
        
        if len(df) < 10:
            return {'value': None, 'signal': 0, 'confidence': 0.0, 'tags': ['trend']}

        prices = df['price'].to_numpy()
        volumes = df['volume'].to_numpy()
        timestamps = df['timestamp'].to_numpy()

        # Веса: если volume weights включены, используем объем, иначе линейное затухание (новые данные важнее)
        if self.use_volume_weights:
            weights = volumes / np.sum(volumes)
        else:
            weights = np.linspace(0.5, 1.5, len(prices)) # Новые данные имеют больший вес
            weights /= np.sum(weights)

        # Линейная регрессия (y = mx + b)
        x = np.arange(len(prices))
        
        # Взвешенная регрессия
        sum_w = np.sum(weights)
        sum_wx = np.sum(weights * x)
        sum_wy = np.sum(weights * prices)
        sum_wxx = np.sum(weights * x * x)
        sum_wxy = np.sum(weights * x * prices)

        denom = sum_w * sum_wxx - sum_wx * sum_wx
        if denom == 0:
            m, b = 0, np.mean(prices)
        else:
            m = (sum_w * sum_wxy - sum_wx * sum_wy) / denom
            b = (sum_wy * sum_wxx - sum_wx * sum_wxy) / denom

        # Текущее значение канала (центр)
        current_center = m * (len(prices) - 1) + b
        
        # Расчет отклонений (standard error)
        predictions = m * x + b
        residuals = prices - predictions
        mse = np.sum(weights * residuals**2)
        std_err = np.sqrt(mse)

        upper_band = current_center + (std_err * self.std_dev)
        lower_band = current_center - (std_err * self.std_dev)
        
        current_price = prices[-1]

        # Определение сигнала
        signal = 0
        if current_price > upper_band:
            signal = -1  # Перекупленность (возврат к среднему)
        elif current_price < lower_band:
            signal = 1   # Перепроданность
        else:
            # Направление тренда
            signal = 1 if m > 0 else (-1 if m < 0 else 0)

        # Уверенность: зависит от расстояния до полос и крутизны тренда
        distance_to_center = abs(current_price - current_center)
        confidence = min(1.0, distance_to_center / (std_err * self.std_dev + 1e-9))
        
        # Метаданные
        metadata = {
            'center': current_center,
            'upper': upper_band,
            'lower': lower_band,
            'slope': m,
            'std_err': std_err
        }

        return {
            'value': current_center,
            'signal': signal,
            'confidence': float(confidence),
            'metadata': metadata,
            'tags': ['trend', 'g_channel', 'regression']
        }
