"""
Модуль фрактального анализа и поиска паттернов.
Использует индекс Херста для оценки самоподобия и простые алгоритмы поиска фигур.
"""
from typing import Dict, Any, List, Tuple
import polars as pl
import numpy as np

from .base_indicator import BaseIndicator
from .registry import register_indicator


@register_indicator
class FractalPatternAnalyzer(BaseIndicator):
    """
    Фрактальный анализ и поиск паттернов.
    1. Индекс Херста (H) для оценки трендовости/флэта.
    2. Поиск простых графических паттернов (Голова-Плечи, Треугольники).
    """

    def __init__(self, name: str, config: Dict[str, Any] = None):
        super().__init__(name, config)
        self.lookback = self.config.get('lookback', 100)
        self.hurst_threshold = self.config.get('hurst_threshold', 0.55)

    def _calculate_hurst(self, prices: np.ndarray) -> float:
        """
        Упрощенный расчет индекса Херста через R/S анализ.
        H > 0.5 -> Тренд
        H < 0.5 -> Возврат к среднему (флэт)
        H ≈ 0.5 -> Случайное блуждание
        """
        n = len(prices)
        if n < 20:
            return 0.5
        
        # Логарифмические доходности
        returns = np.log(prices[1:] / prices[:-1])
        
        # Для простоты берем одно окно на весь период (можно улучшить до скользящего)
        mean_ret = np.mean(returns)
        std_ret = np.std(returns)
        
        if std_ret == 0:
            return 0.5
            
        cum_dev = np.cumsum(returns - mean_ret)
        r = np.max(cum_dev) - np.min(cum_dev)
        s = std_ret * np.sqrt(n-1)
        
        rs = r / s if s != 0 else 0
        hurst = np.log(rs) / np.log(n-1)
        
        return hurst

    def _find_head_shoulders(self, prices: np.ndarray) -> bool:
        """
        Очень упрощенный поиск паттерна "Голова и Плечи".
        Ищет локальный максимум (голова), окруженный двумя меньшими максимумами (плечи).
        """
        if len(prices) < 10:
            return False
            
        # Находим локальные максимумы (упрощенно: просто пики)
        peaks = []
        for i in range(2, len(prices)-2):
            if prices[i] > prices[i-1] and prices[i] > prices[i+1] and \
               prices[i] > prices[i-2] and prices[i] > prices[i+2]:
                peaks.append((i, prices[i]))
        
        if len(peaks) < 3:
            return False
            
        # Проверяем последние 3 пика
        last_three = peaks[-3:]
        left_shoulder = last_three[0][1]
        head = last_three[1][1]
        right_shoulder = last_three[2][1]
        
        # Условия: голова выше плеч, плечи примерно равны
        if head > left_shoulder and head > right_shoulder:
            if abs(left_shoulder - right_shoulder) / head < 0.1: # Разница < 10%
                return True
                
        return False

    def calculate(self, data: pl.DataFrame) -> Dict[str, Any]:
        if not self.validate_data(data):
            return {'error': 'Invalid data'}

        df = data.tail(self.lookback)
        prices = df['price'].to_numpy()

        if len(prices) < 20:
            return {'value': 0.5, 'signal': 0, 'confidence': 0.0, 'tags': ['fractal']}

        # 1. Расчет Херста
        hurst = self._calculate_hurst(prices)
        
        # 2. Поиск паттернов
        is_hs = self._find_head_shoulders(prices)
        
        # Определение сигнала
        signal = 0
        tags = ['fractal']
        
        if hurst > self.hurst_threshold:
            trend_signal = 1 if prices[-1] > prices[0] else -1
            signal = trend_signal
            tags.append('trend_mode')
        elif hurst < (1 - self.hurst_threshold):
            signal = 0 # Флэт, сигнал нейтрален или на возврат к среднему
            tags.append('mean_reversion_mode')
        
        if is_hs:
            tags.append('pattern_head_shoulders')
            # Паттерн Голова-Плечи обычно разворотный
            if prices[-1] < prices[-5]: # После правого плеча цена пошла вниз
                signal = -1 
                tags.append('reversal_short')

        confidence = abs(hurst - 0.5) * 2 # Нормализация 0..1 относительно 0.5
        confidence = min(1.0, confidence + (0.2 if is_hs else 0))

        return {
            'value': float(hurst),
            'signal': signal,
            'confidence': float(confidence),
            'metadata': {'hurst': hurst, 'patterns_found': ['HS'] if is_hs else []},
            'tags': tags
        }
