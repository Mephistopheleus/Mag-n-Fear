"""
Модуль корреляционного анализа.
Рассчитывает скользящую корреляцию с BTC и другими активами.
"""
from typing import Dict, Any
import polars as pl
import numpy as np

from .base_indicator import BaseIndicator
from .registry import register_indicator


@register_indicator
class BTCCorrelation(BaseIndicator):
    """
    Корреляция с BTC.
    Вычисляет скользящую корреляцию Пирсона между текущим активом и BTC.
    Требует наличия данных BTC в потоке или отдельном источнике.
    """

    def __init__(self, name: str, config: Dict[str, Any] = None):
        super().__init__(name, config)
        self.period = self.config.get('period', 50)
        # В реальном проекте здесь будет подключение к источнику данных BTC
        self.btc_data_cache = [] 

    def calculate(self, data: pl.DataFrame, current_price: float) -> Dict[str, Any]:
        if not self.validate_data(data):
            return {'error': 'Invalid data'}

        # Для примера генерируем "синтетическую" корреляцию или используем заглушку
        # В реальности: нужно брать цены BTC за те же timestamps из внешнего источника
        # Здесь эмулируем, что btc_prices уже синхронизированы с data
        # Если btc нет в данных, возвращаем нейтральное значение
        
        if 'btc_price' not in data.columns:
            # Заглушка: если BTC нет, считаем корреляцию 0 (нейтрально)
            # В полной версии тут будет запрос к кэшу BTC
            corr_value = 0.0
        else:
            asset_returns = data['price'].pct_change().drop_nulls()
            btc_returns = data['btc_price'].pct_change().drop_nulls()
            
            # Синхронизируем длины (после pct_change теряется 1 элемент)
            min_len = min(len(asset_returns), len(btc_returns))
            if min_len < 10:
                corr_value = 0.0
            else:
                ar = asset_returns[-min_len:].to_numpy()
                br = btc_returns[-min_len:].to_numpy()
                
                # Корреляция Пирсона
                if np.std(ar) == 0 or np.std(br) == 0:
                    corr_value = 0.0
                else:
                    corr_value = np.corrcoef(ar, br)[0, 1]

        # Прогноз на основе корреляции
        target_price = current_price
        time_sec = 300  # 5 минут
        probability = 0.5
        tags = ['btc_correlation']
        
        if corr_value > 0.7:
            # Сильная прямая корреляция - следуем за BTC
            # Предполагаем, что BTC продолжит движение
            target_price = current_price * (1 + corr_value * 0.01)  # +0.7% макс
            time_sec = int(300 / corr_value)
            probability = 0.5 + abs(corr_value) * 0.4
            tags.append('corr_btc_strong_pos')
        elif corr_value < -0.7:
            # Сильная обратная корреляция
            target_price = current_price * (1 + corr_value * 0.01)  # -0.7% макс
            time_sec = int(300 / abs(corr_value))
            probability = 0.5 + abs(corr_value) * 0.4
            tags.append('corr_btc_strong_neg')
        else:
            # Слабая корреляция - не даем сильного прогноза
            probability = 0.3
            tags.append('corr_btc_weak')

        return {
            'target_price': float(target_price),
            'time_sec': time_sec,
            'probability': float(probability),
            'tags': tags,
            'metadata': {'period': self.period, 'correlation': corr_value}
        }
