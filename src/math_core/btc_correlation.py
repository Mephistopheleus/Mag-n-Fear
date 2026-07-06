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

    def calculate(self, data: pl.DataFrame) -> Dict[str, Any]:
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

        # Сигнал на основе силы корреляции
        signal = 0
        confidence = abs(corr_value)
        
        if corr_value > 0.7:
            signal = 1  # Сильная прямая корреляция (движемся с BTC)
            tags = ['corr_btc_strong_pos']
        elif corr_value < -0.7:
            signal = -1 # Сильная обратная корреляция
            tags = ['corr_btc_strong_neg']
        else:
            tags = ['corr_btc_weak']

        return {
            'value': float(corr_value),
            'signal': signal,
            'confidence': float(confidence),
            'metadata': {'period': self.period},
            'tags': tags
        }
