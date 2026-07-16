"""
Адаптивный Микроскоп - модуль детального анализа внутри свечей.
Активируется при приближении к ключевым уровням или резких скачках волатильности.
"""
import numpy as np
from collections import deque
from typing import Dict, List, Optional, Any


class AdaptiveMicroscope:
    """
    Адаптивный микроскоп для посекундного анализа рынка.
    
    Принцип работы:
    1. В обычном режиме работает в "спящем" состоянии
    2. При срабатывании триггеров (уровни, волатильность) активируется
    3. Анализирует поток тиков с высокой детализацией
    4. Возвращает уточненные сигналы для ProbabilityField
    5. Автоматически отключается после затухания активности
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # Параметры активации
        self.threshold_pct = config.get('microscope', {}).get('threshold_pct', 0.0005)  # 0.05%
        self.min_ticks_for_analysis = config.get('microscope', {}).get('min_ticks', 10)
        self.max_buffer_size = config.get('microscope', {}).get('max_buffer_size', 1000)
        
        # Состояние
        self.active = False
        self.tick_buffer: deque = deque(maxlen=self.max_buffer_size)
        
        # Уровни для триггеров (обновляются извне)
        self.support_levels: List[float] = []
        self.resistance_levels: List[float] = []
        
        # Статистика
        self.activation_count = 0
        self.total_ticks_analyzed = 0
        
    def set_levels(self, supports: List[float], resistances: List[float]):
        """Установка уровней поддержки/сопротивления для триггеров."""
        self.support_levels = supports
        self.resistance_levels = resistances
    
    def process_tick(self, tick_data: Dict) -> Optional[Dict]:
        """
        Обработка тика. Возвращает уточненный анализ если микроскоп активен.
        
        :param tick_data: {'price': float, 'volume': float, 'timestamp': int, ...}
        :return: Dict с микроструктурой или None
        """
        price = tick_data.get('price', 0)
        
        # Проверка триггеров активации
        if not self.active:
            if self._check_activation_triggers(price):
                self._activate()
        else:
            # Добавляем тик в буфер активного анализа
            self.tick_buffer.append(tick_data)
            self.total_ticks_analyzed += 1
            
            # Анализ микроструктуры
            if len(self.tick_buffer) >= self.min_ticks_for_analysis:
                analysis = self._analyze_micro_structure()
                
                # Проверка условий деактивации
                if self._check_deactivation_triggers(price):
                    self._deactivate()
                
                return analysis
        
        return None
    
    def _check_activation_triggers(self, current_price: float) -> bool:
        """Проверка условий для активации микроскопа."""
        # Триггер 1: Близость к уровням
        for level in self.support_levels + self.resistance_levels:
            if level == 0:
                continue
            distance_pct = abs(current_price - level) / level
            if distance_pct < self.threshold_pct:
                print(f"[Microscope] Trigger: Near level {level} (distance: {distance_pct:.6f})")
                return True
        
        # Триггер 2: Резкий скачок волатильности
        # Активируем микроскоп если волатильность выросла более чем в 2 раза за последний тик
        if len(self.tick_buffer) >= 2:
            recent_volatility = self._calculate_recent_volatility()
            if recent_volatility > self.base_volatility * 2.0:
                print(f"[Microscope] Trigger: Volatility spike detected ({recent_volatility:.6f} vs base {self.base_volatility:.6f})")
                return True
        
        return False
    
    def _calculate_recent_volatility(self) -> float:
        """Расчет волатильности по последним тикам в буфере."""
        if len(self.tick_buffer) < 5:
            return 0.0
        
        prices = [t['price'] for t in self.tick_buffer[-10:]]
        if not prices or min(prices) == 0:
            return 0.0
        
        avg_price = sum(prices) / len(prices)
        variance = sum((p - avg_price) ** 2 for p in prices) / len(prices)
        std_dev = variance ** 0.5
        
        return std_dev / avg_price if avg_price > 0 else 0.0
    
    def _check_deactivation_triggers(self, current_price: float) -> bool:
        """Проверка условий для деактивации микроскопа."""
        # Деактивируем если цена ушла далеко от всех уровней
        for level in self.support_levels + self.resistance_levels:
            if level == 0:
                continue
            distance_pct = abs(current_price - level) / level
            if distance_pct < self.threshold_pct * 2:  # Двойной порог для гистерезиса
                return False  # Еще близко, остаемся активными
        
        print(f"[Microscope] Deactivation: Price moved away from levels")
        return True
    
    def _activate(self):
        """Активация микроскопа."""
        self.active = True
        self.activation_count += 1
        self.tick_buffer.clear()
        print(f"[Microscope] ACTIVATED (activation #{self.activation_count})")
    
    def _deactivate(self):
        """Деактивация микроскопа."""
        self.active = False
        print(f"[Microscope] DEACTIVATED. Analyzed {len(self.tick_buffer)} ticks.")
        self.tick_buffer.clear()
    
    def _analyze_micro_structure(self) -> Dict:
        """
        Детальный анализ микроструктуры рынка по тику.
        
        :return: Dict с метриками микроструктуры
        """
        if len(self.tick_buffer) < self.min_ticks_for_analysis:
            return {}
        
        prices = [t['price'] for t in self.tick_buffer]
        volumes = [t.get('volume', 0) for t in self.tick_buffer]
        timestamps = [t.get('timestamp', 0) for t in self.tick_buffer]
        
        # Расчет VWAP за период анализа
        total_volume = sum(volumes)
        if total_volume > 0:
            micro_vwap = sum(p * v for p, v in zip(prices, volumes)) / total_volume
        else:
            micro_vwap = sum(prices) / len(prices)
        
        # Направление движения (наклон)
        price_change = prices[-1] - prices[0]
        price_change_pct = (price_change / prices[0]) * 100 if prices[0] > 0 else 0
        
        # Волатильность внутри периода
        price_std = np.std(prices)
        price_mean = np.mean(prices)
        volatility = price_std / price_mean if price_mean > 0 else 0
        
        # Баланс покупателей/продавцов
        buy_volume = sum(v for t, v in zip(self.tick_buffer, volumes) 
                        if not t.get('is_buyer_maker', False))
        sell_volume = sum(v for t, v in zip(self.tick_buffer, volumes) 
                         if t.get('is_buyer_maker', False))
        
        buyer_pressure = buy_volume / (buy_volume + sell_volume) if (buy_volume + sell_volume) > 0 else 0.5
        
        # Время анализа
        time_span_ms = timestamps[-1] - timestamps[0] if timestamps else 0
        
        return {
            'micro_vwap': micro_vwap,
            'price_change_pct': price_change_pct,
            'volatility': volatility,
            'buyer_pressure': buyer_pressure,
            'tick_count': len(self.tick_buffer),
            'time_span_ms': time_span_ms,
            'avg_tick_size': total_volume / len(self.tick_buffer) if len(self.tick_buffer) > 0 else 0,
            'active': True,
            'activation_id': self.activation_count
        }
    
    def get_status(self) -> Dict:
        """Получение текущего статуса микроскопа."""
        return {
            'active': self.active,
            'buffer_size': len(self.tick_buffer),
            'activation_count': self.activation_count,
            'total_ticks_analyzed': self.total_ticks_analyzed,
            'support_levels': len(self.support_levels),
            'resistance_levels': len(self.resistance_levels)
        }
