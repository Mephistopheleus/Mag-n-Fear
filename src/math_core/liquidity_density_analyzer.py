"""
Liquidity Density Analyzer (Уникальный модуль)
Анализ плотности ликвидности на полотне таймфреймов с учетом стакана.
Создает 3D-карту: Время (X) x Цена (Y) x Плотность (Z).
Используется для поиска зон пробоя, шпилек и точек входа.
"""
from typing import Dict, Any, List, Tuple
import polars as pl
import numpy as np

from .base_indicator import BaseIndicator
from .registry import register_indicator


@register_indicator
class LiquidityDensityAnalyzer(BaseIndicator):
    """
    Анализатор плотности ликвидности.
    Объединяет данные стакана, объемов и цены для построения карты ликвидности.
    """

    def __init__(self, name: str, config: Dict[str, Any] = None):
        super().__init__(name, config)
        self.lookback_candles = self.config.get('lookback', 50)
        self.price_bins = self.config.get('price_bins', 20)  # Количество ценовых уровней
        self.volume_threshold = self.config.get('volume_threshold', 0.7)  # Порог значимого объема

    def _build_liquidity_map(self, df: pl.DataFrame, order_book: Dict[str, List] = None) -> np.ndarray:
        """
        Построение карты ликвидности.
        Возвращает 2D массив [time_steps x price_bins] с плотностью ликвидности.
        """
        if len(df) < self.lookback_candles:
            return np.zeros((len(df), self.price_bins))
        
        df_slice = df.tail(self.lookback_candles)
        
        # Получаем диапазоны цен
        min_price = df_slice['low'].min()
        max_price = df_slice['high'].max()
        price_range = max_price - min_price
        
        if price_range == 0:
            return np.zeros((len(df_slice), self.price_bins))
        
        # Создаем бины для цен
        price_bins_edges = np.linspace(min_price, max_price, self.price_bins + 1)
        
        # Инициализируем карту ликвидности
        liquidity_map = np.zeros((len(df_slice), self.price_bins))
        
        # Заполняем карту на основе объемов свечей и их расположения
        for i, row in enumerate(df_slice.iter_rows(named=True)):
            close_price = row['close']
            volume = row['volume']
            
            # Определяем bin цены закрытия
            bin_idx = min(int((close_price - min_price) / price_range * self.price_bins), self.price_bins - 1)
            
            # Распределяем объем по соседним бинам (гауссово распределение)
            for j in range(max(0, bin_idx - 2), min(self.price_bins, bin_idx + 3)):
                weight = np.exp(-0.5 * ((j - bin_idx) ** 2) / 2)  # Гауссово ядро
                liquidity_map[i, j] += volume * weight
        
        # Если есть данные стакана, добавляем их
        if order_book and 'bids' in order_book and 'asks' in order_book:
            bids = order_book.get('bids', [])
            asks = order_book.get('asks', [])
            
            # Нормализуем объемы стакана
            all_orders = [(p, v, 1) for p, v in bids[:10]] + [(p, v, -1) for p, v in asks[:10]]
            
            for price, vol, side in all_orders:
                if min_price <= price <= max_price:
                    bin_idx = min(int((price - min_price) / price_range * self.price_bins), self.price_bins - 1)
                    # Добавляем ликвидность стакана к последнему временному шагу
                    liquidity_map[-1, bin_idx] += vol * 10  # Увеличиваем вес стакана
        
        return liquidity_map

    def _find_liquidity_zones(self, liquidity_map: np.ndarray) -> List[Dict[str, Any]]:
        """
        Поиск зон с высокой и низкой плотностью ликвидности.
        """
        zones = []
        
        # Находим среднюю плотность по времени
        avg_density_per_bin = np.mean(liquidity_map, axis=0)
        
        # Зоны высокой ликвидности (стены)
        high_liq_threshold = np.percentile(avg_density_per_bin, 80)
        low_liq_threshold = np.percentile(avg_density_per_bin, 20)
        
        for i, density in enumerate(avg_density_per_bin):
            if density > high_liq_threshold:
                zones.append({
                    'type': 'high_liquidity',
                    'bin_index': i,
                    'density': float(density),
                    'significance': 'support_resistance'
                })
            elif density < low_liq_threshold:
                zones.append({
                    'type': 'low_liquidity',
                    'bin_index': i,
                    'density': float(density),
                    'significance': 'breakout_zone'
                })
        
        return zones

    def calculate(self, data: pl.DataFrame, current_price: float) -> Dict[str, Any]:
        """
        Основной метод анализа ликвидности.
        
        Args:
            data: DataFrame с колонками ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            current_price: Текущая цена актива
        
        Returns:
            Dict с результатами:
            - 'liquidity_map': 2D массив плотности ликвидности
            - 'zones': Список зон ликвидности
            - 'poc_price': Price of Control (цена с максимальной ликвидностью)
            - 'imbalance_zones': Зоны дисбаланса
            - 'probability': 0.5 (прогноз на основе паттернов ликвидности)
            - 'tags': ['liquidity', 'order_flow']
        """
        if not self.validate_data(data):
            if 'high' not in data.columns or 'low' not in data.columns:
                return {
                    'error': 'MISSING_OHLC_DATA',
                    'liquidity_map': [],
                    'zones': [],
                    'probability': 0.0,
                    'tags': ['liquidity', 'error'],
                    'metadata': {'error': 'No high/low columns'}
                }
        
        # Строим карту ликвидности (order_book можно передать через metadata в будущем)
        liquidity_map = self._build_liquidity_map(data, order_book=None)
        
        # Находим зоны
        zones = self._find_liquidity_zones(liquidity_map)
        
        # Находим POC (Price of Control)
        if liquidity_map.size > 0:
            poc_bin = np.unravel_index(np.argmax(liquidity_map), liquidity_map.shape)
            # Конвертируем bin обратно в цену (приблизительно)
            df_slice = data.tail(self.lookback_candles)
            min_price = df_slice['low'].min()
            max_price = df_slice['high'].max()
            price_range = max_price - min_price
            poc_price = min_price + (poc_bin[1] / self.price_bins) * price_range
        else:
            poc_price = current_price
        
        # Определяем направление вероятного пробоя
        # Если ликвидность сконцентрирована выше текущей цены -> пробой вверх
        current_bin = int((current_price - min_price) / price_range * self.price_bins) if price_range > 0 else 0
        above_liq = np.sum(liquidity_map[:, current_bin+1:]) if current_bin < self.price_bins - 1 else 0
        below_liq = np.sum(liquidity_map[:, :current_bin]) if current_bin > 0 else 0
        
        if above_liq > below_liq * 1.5:
            breakout_direction = 1
            confidence = min(0.9, 0.5 + (above_liq - below_liq) / (above_liq + below_liq + 1e-6) * 0.4)
        elif below_liq > above_liq * 1.5:
            breakout_direction = -1
            confidence = min(0.9, 0.5 + (below_liq - above_liq) / (above_liq + below_liq + 1e-6) * 0.4)
        else:
            breakout_direction = 0
            confidence = 0.5
        
        return {
            'target_price': current_price * (1 + breakout_direction * 0.01),  # Прогноз 1% движения
            'time_sec': 600,  # Горизонт 10 минут
            'probability': float(confidence),  # Доверие к прогнозу пробоя
            'tags': ['liquidity', 'order_flow', 'breakout'],
            'metadata': {
                'liquidity_map_shape': liquidity_map.shape,
                'zones': zones,
                'poc_price': float(poc_price),
                'breakout_direction': breakout_direction,
                'above_liquidity': float(above_liq),
                'below_liquidity': float(below_liq),
                'imbalance_ratio': float(above_liq / (below_liq + 1e-6))
            }
        }
