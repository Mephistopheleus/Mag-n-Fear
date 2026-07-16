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

    def calculate(self, data: pl.DataFrame, current_price: float) -> Dict[str, Any]:
        if not self.validate_data(data):
            return {'error': 'Invalid data'}

        # Берем последние N периодов
        df = data.tail(self.period)
        
        if len(df) < 10:
            return {
                'target_price': current_price,
                'time_sec': 300,
                'probability': 0.0,
                'tags': ['trend'],
                'metadata': {}
            }

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
        
        # Определение прогноза
        target_price = current_price
        time_sec = 300
        probability = 0.5
        tags = ['trend', 'g_channel', 'regression']
        
        # Прогноз на основе направления тренда и положения цены
        if m > 0.0001:  # Восходящий тренд
            target_price = current_price + abs(m) * 5  # Проекция на 5 шагов вперед
            time_sec = int(300 / (abs(m) * 1000 + 0.1))  # Чем круче тренд, тем быстрее
            probability = min(0.9, 0.5 + abs(m) * 500)
            tags.append('uptrend')
        elif m < -0.0001:  # Нисходящий тренд
            target_price = current_price - abs(m) * 5
            time_sec = int(300 / (abs(m) * 1000 + 0.1))
            probability = min(0.9, 0.5 + abs(m) * 500)
            tags.append('downtrend')
        else:  # Флэт
            target_price = current_center  # Возврат к среднему
            time_sec = 600
            probability = 0.6
            tags.append('sideways')
        
        # Проверка на перекупленность/перепроданность
        if current_price > upper_band:
            # Перекупленность - прогноз возврата к средней линии
            target_price = current_center
            time_sec = 180  # Быстрый возврат
            probability = 0.75
            tags.append('overbought_reversal')
        elif current_price < lower_band:
            # Перепроданность - прогноз возврата к средней линии
            target_price = current_center
            time_sec = 180
            probability = 0.75
            tags.append('oversold_bounce')

        # Метаданные
        metadata = {
            'center': current_center,
            'upper': upper_band,
            'lower': lower_band,
            'slope': m,
            'std_err': std_err
        }

        return {
            'target_price': float(target_price),
            'time_sec': time_sec,
            'probability': float(probability),
            'tags': tags,
            'metadata': metadata
        }
