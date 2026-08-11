"""
Liquidity Density Analyzer (Уникальный модуль)
Анализ плотности ликвидности на полотне таймфреймов с учетом стакана.
Создает 3D-карту: Время (X) x Цена (Y) x Плотность (Z).
Используется для поиска зон пробоя, шпилек и точек входа.
"""
from typing import Dict, Any, List, Tuple
import polars as pl
import numpy as np
import uuid
from datetime import datetime

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
        self.smoothing_window = self.config.get('smoothing_window', 3)  # Окно сглаживания для анти-спуфинга

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
        
        # Применяем сглаживание скользящим средним для устранения шума (анти-спуфинг)
        if self.smoothing_window > 1:
            kernel = np.ones(self.smoothing_window) / self.smoothing_window
            for j in range(self.price_bins):
                liquidity_map[:, j] = np.convolve(liquidity_map[:, j], kernel, mode='same')
        
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

    def _find_liquidity_zones(self, liquidity_map: np.ndarray, df_slice: pl.DataFrame) -> List[Dict[str, Any]]:
        """
        Поиск зон с высокой и низкой плотностью ликвидности.
        Возвращает POC, Walls (стены) и Voids (вакуумы).
        """
        zones = []
        
        # Находим среднюю плотность по времени
        avg_density_per_bin = np.mean(liquidity_map, axis=0)
        
        # Зоны высокой ликвидности (стены) - выше 80-го процентиля
        high_liq_threshold = np.percentile(avg_density_per_bin, 80)
        # Зоны низкой ликвидности (вакуумы) - ниже 20-го процентиля
        low_liq_threshold = np.percentile(avg_density_per_bin, 20)
        
        # Для нормировки strength используем диапазон между 20-м и 80-м процентилями
        p20 = np.percentile(avg_density_per_bin, 20)
        p80 = np.percentile(avg_density_per_bin, 80)
        density_range = p80 - p20 if p80 > p20 else 1.0
        
        min_price = df_slice['low'].min()
        max_price = df_slice['high'].max()
        price_range = max_price - min_price
        
        for i, density in enumerate(avg_density_per_bin):
            # Конвертируем bin обратно в цену
            price_level = min_price + (i / self.price_bins) * price_range
            
            if density > high_liq_threshold:
                # Нормировка для стен: (density - p80) / (max_density - p80)
                max_density = np.max(avg_density_per_bin)
                wall_strength = (density - p80) / (max_density - p80 + 1e-6)
                wall_strength = max(0.0, min(1.0, wall_strength))  # Clamp to [0, 1]
                
                zones.append({
                    'type': 'wall',
                    'price_level': float(price_level),
                    'bin_index': i,
                    'density': float(density),
                    'significance': 'support_resistance',
                    'strength': float(wall_strength)
                })
            elif density < low_liq_threshold:
                # Нормировка для вакуумов: (p20 - density) / (p20 - min_density)
                min_density = np.min(avg_density_per_bin)
                void_strength = (p20 - density) / (p20 - min_density + 1e-6)
                void_strength = max(0.0, min(1.0, void_strength))  # Clamp to [0, 1]
                
                zones.append({
                    'type': 'void',
                    'price_level': float(price_level),
                    'bin_index': i,
                    'density': float(density),
                    'significance': 'breakout_zone',
                    'strength': float(void_strength)
                })
        
        return zones

    def calculate(self, data: pl.DataFrame, current_price: float) -> Dict[str, Any]:
        """
        Основной метод анализа ликвидности.
        
        Args:
            data: DataFrame с колонками ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            current_price: Текущая цена актива
        
        Returns:
            Dict с результатами в формате карточки анализатора:
            - type: "LiquidityMap", "POC_Found", "Imbalance_Detected"
            - timeframe: "5m_dense"
            - horizon: Горизонт актуальности
            - data: JSON с координатами POC, списком стен и вакуумов
            - prediction: Направление движения к ближайшей крупной ликвидности
            - confidence: 0.5 (базовое, настраивается тюнером)
            - unique_id: Уникальный ID для отслеживания эффективности
        """
        if not self.validate_data(data):
            if 'high' not in data.columns or 'low' not in data.columns:
                return {
                    'error': 'MISSING_OHLC_DATA',
                    'type': 'LiquidityMap_Error',
                    'timeframe': '5m_dense',
                    'horizon': 0,
                    'data': {},
                    'prediction': 'NEUTRAL',
                    'confidence': 0.0,
                    'unique_id': str(uuid.uuid4()),
                    'tags': ['liquidity', 'error']
                }
        
        df_slice = data.tail(self.lookback_candles)
        
        # Строим карту ликвидности (order_book можно передать через metadata в будущем)
        liquidity_map = self._build_liquidity_map(data, order_book=None)
        
        # Находим зоны (POC, стены, вакуумы)
        zones = self._find_liquidity_zones(liquidity_map, df_slice)
        
        # Находим POC (Price of Control) - уровень с максимальной плотностью
        if liquidity_map.size > 0:
            poc_bin = np.unravel_index(np.argmax(liquidity_map), liquidity_map.shape)
            min_price = df_slice['low'].min()
            max_price = df_slice['high'].max()
            price_range = max_price - min_price
            poc_price = min_price + (poc_bin[1] / self.price_bins) * price_range
        else:
            poc_price = current_price
        
        # Определяем направление вероятного пробоя
        min_price = df_slice['low'].min()
        max_price = df_slice['high'].max()
        price_range = max_price - min_price
        
        current_bin = int((current_price - min_price) / price_range * self.price_bins) if price_range > 0 else 0
        current_bin = max(0, min(self.price_bins - 1, current_bin))
        
        above_liq = np.sum(liquidity_map[:, current_bin+1:]) if current_bin < self.price_bins - 1 else 0
        below_liq = np.sum(liquidity_map[:, :current_bin]) if current_bin > 0 else 0
        
        # Прогнозирование движения
        if above_liq > below_liq * 1.5:
            breakout_direction = 1
            prediction = 'UP_TO_LIQUIDITY'
            confidence = min(0.9, 0.5 + (above_liq - below_liq) / (above_liq + below_liq + 1e-6) * 0.4)
        elif below_liq > above_liq * 1.5:
            breakout_direction = -1
            prediction = 'DOWN_TO_LIQUIDITY'
            confidence = min(0.9, 0.5 + (below_liq - above_liq) / (above_liq + below_liq + 1e-6) * 0.4)
        else:
            breakout_direction = 0
            prediction = 'NEUTRAL'
            confidence = 0.5
        
        # Проверка: цена в вакууме - ожидается ускорение
        in_void = any(z['type'] == 'void' and abs(z['price_level'] - current_price) / current_price < 0.01 for z in zones)
        if in_void:
            prediction = 'ACCELERATION_IN_VOID'
            confidence = min(0.85, confidence + 0.15)
        
        # Формируем data JSON
        walls = [z for z in zones if z['type'] == 'wall']
        voids = [z for z in zones if z['type'] == 'void']
        
        data_json = {
            'poc_price': float(poc_price),
            'poc_bin': int(poc_bin[1]) if liquidity_map.size > 0 else 0,
            'walls': walls,
            'voids': voids,
            'imbalance_ratio': float(above_liq / (below_liq + 1e-6)),
            'above_liquidity': float(above_liq),
            'below_liquidity': float(below_liq)
        }
        
        # Определяем тип сигнала
        signal_type = 'LiquidityMap'
        if poc_price and abs(poc_price - current_price) / current_price < 0.005:
            signal_type = 'POC_Found'
        if in_void:
            signal_type = 'Imbalance_Detected'
        
        # Горизонт актуальности - пока не изменится структура стакана (15 минут)
        horizon_sec = 900
        
        return {
            'type': signal_type,
            'timeframe': '5m_dense',
            'horizon': horizon_sec,
            'data': data_json,
            'prediction': prediction,
            'confidence': float(confidence),
            'unique_id': str(uuid.uuid4()),
            'target_price': current_price * (1 + breakout_direction * 0.01),
            'time_sec': horizon_sec,
            'probability': float(confidence),
            'tags': ['liquidity', 'order_flow', 'breakout', signal_type],
            'metadata': {
                'liquidity_map_shape': liquidity_map.shape,
                'zones_count': len(zones),
                'walls_count': len(walls),
                'voids_count': len(voids),
                'poc_price': float(poc_price),
                'breakout_direction': breakout_direction
            }
        }
