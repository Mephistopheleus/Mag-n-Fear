"""
FibonacciLevels - Расчет уровней Фибоначчи по экстремумам за период.
Используется для определения потенциальных зон поддержки/сопротивления и целей коррекции.
"""

import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FibLevel:
    """Структура уровня Фибоначчи"""
    level: float        # Коэффициент (0, 0.236, 0.382, 0.5, 0.618, 1)
    price: float        # Цена уровня
    type: str           # 'HIGH', 'LOW', 'SUPPORT', 'RESISTANCE'
    description: str = ""


class FibonacciLevels:
    """
    Анализатор уровней Фибоначчи.
    Рассчитывает уровни по последним экстремумам (High/Low) за заданный период.
    """
    
    # Стандартные уровни Фибоначчи
    FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 1.0]
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.symbol = config.get('symbol', 'BTCUSDT')
        logger.info(f"FibonacciLevels initialized for {self.symbol}")
    
    async def analyze(self, candles: List[Dict[str, Any]], current_price: float) -> Dict[str, Any]:
        """
        Главный метод анализа.
        
        Args:
            candles: Список свечей с полями ['high', 'low', 'close', 'time']
            current_price: Текущая цена
            
        Returns:
            Словарь с рассчитанными уровнями и прогнозом
        """
        if not candles or len(candles) < 2:
            return {'levels': [], 'trend': 'UNKNOWN', 'prediction': None}
        
        # Находим экстремумы за период
        highs = [c['high'] for c in candles]
        lows = [c['low'] for c in candles]
        
        period_high = max(highs)
        period_low = min(lows)
        
        if period_high == period_low:
            return {'levels': [], 'trend': 'FLAT', 'prediction': None}
        
        # Определяем направление тренда
        trend = 'UP' if current_price > (period_high + period_low) / 2 else 'DOWN'
        
        # Рассчитываем уровни Фибоначчи
        levels = self._calculate_levels(period_high, period_low, trend)
        
        # Определяем ближайший уровень и прогноз
        prediction = self._find_nearest_level(current_price, levels)
        
        result = {
            'period_high': period_high,
            'period_low': period_low,
            'trend': trend,
            'levels': [self._level_to_dict(l) for l in levels],
            'prediction': prediction,
            'current_price': current_price
        }
        
        logger.debug(f"Fibonacci analysis for {self.symbol}: {len(levels)} levels, trend={trend}")
        return result
    
    def _calculate_levels(self, high: float, low: float, trend: str) -> List[FibLevel]:
        """
        Рассчитывает уровни Фибоначчи в зависимости от тренда.
        
        Для восходящего тренда: от Low до High (коррекция вниз)
        Для нисходящего тренда: от High до Low (коррекция вверх)
        """
        levels = []
        diff = high - low
        
        if trend == 'UP':
            # Восходящий тренд: уровни коррекции снизу вверх
            for fib in self.FIB_LEVELS:
                price = low + diff * fib
                level_type = 'SUPPORT' if fib < 0.5 else 'RESISTANCE'
                if fib == 0.0:
                    level_type = 'LOW'
                elif fib == 1.0:
                    level_type = 'HIGH'
                
                levels.append(FibLevel(
                    level=fib,
                    price=price,
                    type=level_type,
                    description=f"Fib {fib:.3f} ({level_type})"
                ))
        else:
            # Нисходящий тренд: уровни коррекции сверху вниз
            for fib in self.FIB_LEVELS:
                price = high - diff * fib
                level_type = 'RESISTANCE' if fib < 0.5 else 'SUPPORT'
                if fib == 0.0:
                    level_type = 'HIGH'
                elif fib == 1.0:
                    level_type = 'LOW'
                
                levels.append(FibLevel(
                    level=fib,
                    price=price,
                    type=level_type,
                    description=f"Fib {fib:.3f} ({level_type})"
                ))
        
        # Сортируем по цене
        levels.sort(key=lambda x: x.price)
        return levels
    
    def _find_nearest_level(self, price: float, levels: List[FibLevel]) -> Optional[Dict[str, Any]]:
        """
        Находит ближайший уровень Фибоначчи и определяет прогноз (отскок или пробой).
        """
        if not levels:
            return None
        
        nearest = None
        min_dist = float('inf')
        
        for level in levels:
            dist = abs(price - level.price)
            if dist < min_dist:
                min_dist = dist
                nearest = level
        
        if not nearest:
            return None
        
        # Прогноз: если цена близко к уровню (< 0.5%), возможен отскок
        # Если далеко - движение к следующему уровню
        threshold_pct = 0.005  # 0.5%
        dist_pct = min_dist / price
        
        action = 'BOUNCE' if dist_pct < threshold_pct else 'MOVE_TO_NEXT'
        next_level = self._find_next_level(price, levels, nearest)
        
        return {
            'nearest_level': nearest.level,
            'nearest_price': nearest.price,
            'distance_pct': dist_pct * 100,
            'action': action,
            'next_target': next_level.price if next_level else None,
            'description': f"Цена {dist_pct*100:.2f}% от уровня Fib {nearest.level:.3f}. Ожидаемое действие: {action}"
        }
    
    def _find_next_level(self, price: float, levels: List[FibLevel], current: FibLevel) -> Optional[FibLevel]:
        """
        Находит следующий уровень Фибоначчи в направлении движения цены.
        """
        if price > current.price:
            # Движение вверх - ищем следующий уровень выше
            above = [l for l in levels if l.price > price]
            return min(above, key=lambda x: x.price) if above else None
        else:
            # Движение вниз - ищем следующий уровень ниже
            below = [l for l in levels if l.price < price]
            return max(below, key=lambda x: x.price) if below else None
    
    def _level_to_dict(self, level: FibLevel) -> Dict[str, Any]:
        """Преобразует объект FibLevel в словарь"""
        return {
            'level': level.level,
            'price': level.price,
            'type': level.type,
            'description': level.description
        }
