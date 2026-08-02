"""
Модуль корреляционного анализа.
Рассчитывает скользящую корреляцию с BTC и другими активами.
Использует данные из BinanceFuturesFeed для получения цен BTC.
"""
from typing import Dict, Any, Optional
import polars as pl
import numpy as np

from .base_indicator import BaseIndicator
from .registry import register_indicator


@register_indicator
class BTCCorrelation(BaseIndicator):
    """
    Корреляция с BTC.
    Вычисляет скользящую корреляцию Пирсона между текущим активом и BTC.
    Использует реальные данные BTC из feed через btc_prices кэш.
    """

    def __init__(self, name: str, config: Dict[str, Any] = None):
        super().__init__(name, config)
        self.period = self.config.get('period', 50)
        self.btc_feed = None  # Будет установлен извне при инициализации

    def set_btc_feed(self, btc_feed):
        """Установка ссылки на feed для получения данных BTC."""
        self.btc_feed = btc_feed

    def calculate(self, data: pl.DataFrame, current_price: float) -> Dict[str, Any]:
        if not self.validate_data(data):
            return {'error': 'Invalid data'}

        # Получаем цены BTC из feed если доступен
        btc_prices_list = []
        asset_prices_list = []
        
        if self.btc_feed and hasattr(self.btc_feed, 'btc_prices'):
            # Синхронизируем данные по timestamp
            for idx in range(len(data)):
                try:
                    ts = int(data[idx, 'timestamp'] * 1000) if 'timestamp' in data.columns else 0
                    if ts == 0:
                        # Если timestamp нет, используем индекс
                        ts = int(idx * 300000)  # предполагаем 5м свечи
                    
                    btc_price = self.btc_feed.get_btc_price_for_timestamp(ts)
                    if btc_price is not None:
                        btc_prices_list.append(btc_price)
                        asset_prices_list.append(data[idx, 'price'])
                except Exception:
                    continue
        
        # Если не удалось получить синхронизированные данные, пробуем альтернативный метод
        if len(btc_prices_list) < 10:
            if 'btc_price' in data.columns:
                # Используем btc_price из данных если есть
                asset_returns = data['price'].pct_change().drop_nulls()
                btc_returns = data['btc_price'].pct_change().drop_nulls()
                
                min_len = min(len(asset_returns), len(btc_returns))
                if min_len >= 10:
                    ar = asset_returns[-min_len:].to_numpy()
                    br = btc_returns[-min_len:].to_numpy()
                    
                    if np.std(ar) == 0 or np.std(br) == 0:
                        corr_value = 0.0
                    else:
                        corr_value = np.corrcoef(ar, br)[0, 1]
                else:
                    corr_value = 0.0
            else:
                # Заглушка: если BTC нет, считаем корреляцию 0 (нейтрально)
                corr_value = 0.0
        else:
            # Вычисляем корреляцию на основе синхронизированных данных
            asset_np = np.array(asset_prices_list)
            btc_np = np.array(btc_prices_list)
            
            asset_returns = np.diff(asset_np) / asset_np[:-1]
            btc_returns = np.diff(btc_np) / btc_np[:-1]
            
            min_len = min(len(asset_returns), len(btc_returns))
            if min_len >= 10:
                ar = asset_returns[-min_len:]
                br = btc_returns[-min_len:]
                
                if np.std(ar) == 0 or np.std(br) == 0:
                    corr_value = 0.0
                else:
                    corr_value = np.corrcoef(ar, br)[0, 1]
            else:
                corr_value = 0.0

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
