"""
CandleAnalyzer Module.
Детальный анализ отдельных свечей: паттерны, движение внутри, аномалии.
Используется для скальпинга и детекции микро-сигналов.
"""
import logging
from typing import Dict, List, Optional
from src.math_core.base_indicator import BaseIndicator

logger = logging.getLogger(__name__)


class CandleAnalyzer(BaseIndicator):
    """Анализ конкретных свечей."""
    
    def __init__(self, config: Dict):
        super().__init__(config)
        self.patterns = {
            'doji': self._detect_doji,
            'hammer': self._detect_hammer,
            'shooting_star': self._detect_shooting_star,
            'engulfing_bullish': self._detect_engulfing_bullish,
            'engulfing_bearish': self._detect_engulfing_bearish,
            'inside_bar': self._detect_inside_bar,
            'outside_bar': self._detect_outside_bar,
            'pin_bar': self._detect_pin_bar
        }
    
    def analyze(self, symbol: str, current_candle: Dict, 
                prev_candle: Optional[Dict] = None, 
                prev_prev_candle: Optional[Dict] = None) -> Dict:
        """
        Детальный анализ свечи.
        
        :param current_candle: {'open', 'high', 'low', 'close', 'volume', 'timestamp'}
        :param prev_candle: предыдущая свеча
        :param prev_prev_candle: свеча перед предыдущей (для 3-свечных паттернов)
        :return: {
            'patterns': [...], 
            'movement_analysis': {...}, 
            'anomalies': [...],
            'confidence': 0.0,
            'signal': 'BULLISH'/'BEARISH'/'NEUTRAL'
        }
        """
        result = {
            'symbol': symbol,
            'timestamp': current_candle.get('timestamp'),
            'patterns': [],
            'movement_analysis': {},
            'anomalies': [],
            'confidence': 0.0,
            'signal': 'NEUTRAL'
        }
        
        # 1. Детекция паттернов
        for pattern_name, detector in self.patterns.items():
            try:
                if detector(current_candle, prev_candle, prev_prev_candle):
                    result['patterns'].append(pattern_name)
                    logger.debug(f"[CandleAnalyzer] {symbol}: Detected pattern '{pattern_name}'")
            except Exception as e:
                logger.error(f"Error detecting pattern {pattern_name}: {e}")
        
        # 2. Анализ движения внутри свечи
        result['movement_analysis'] = self._analyze_internal_movement(current_candle)
        
        # 3. Поиск аномалий
        result['anomalies'] = self._detect_anomalies(current_candle, prev_candle)
        
        # 4. Расчет уверенности и сигнала
        result['confidence'], result['signal'] = self._calculate_confidence_and_signal(result)
        
        return result
    
    def _analyze_internal_movement(self, candle: Dict) -> Dict:
        """Анализ того, как цена двигалась внутри свечи."""
        open_p = candle['open']
        close_p = candle['close']
        high_p = candle['high']
        low_p = candle['low']
        
        range_p = high_p - low_p
        body = abs(close_p - open_p)
        upper_wick = high_p - max(open_p, close_p)
        lower_wick = min(open_p, close_p) - low_p
        
        # Определение типа свечи
        if range_p == 0:
            candle_type = 'flat'
        elif body / range_p > 0.7:
            candle_type = 'strong_body'
        elif body / range_p < 0.3:
            candle_type = 'doji_like'
        else:
            candle_type = 'normal'
        
        # Направление давления
        if close_p > open_p:
            pressure = 'bullish'
            pressure_ratio = (close_p - low_p) / range_p if range_p > 0 else 0.5
        elif close_p < open_p:
            pressure = 'bearish'
            pressure_ratio = (high_p - close_p) / range_p if range_p > 0 else 0.5
        else:
            pressure = 'neutral'
            pressure_ratio = 0.5
        
        # Процент закрытия в диапазоне
        close_position = (close_p - low_p) / range_p if range_p > 0 else 0.5
        
        return {
            'candle_type': candle_type,
            'body_ratio': body / range_p if range_p > 0 else 0,
            'upper_wick_ratio': upper_wick / range_p if range_p > 0 else 0,
            'lower_wick_ratio': lower_wick / range_p if range_p > 0 else 0,
            'pressure': pressure,
            'pressure_ratio': pressure_ratio,
            'close_position': close_position,
            'range': range_p,
            'volume': candle.get('volume', 0)
        }
    
    def _detect_anomalies(self, current: Dict, prev: Optional[Dict]) -> List[str]:
        """Поиск аномалий в свече."""
        anomalies = []
        
        if not prev:
            return anomalies
        
        curr_range = current['high'] - current['low']
        prev_range = prev['high'] - prev['low']
        
        # Аномально большой диапазон
        if prev_range > 0 and curr_range > prev_range * 3:
            anomalies.append('explosive_range')
        
        # Аномальный объем
        if prev.get('volume', 0) > 0:
            volume_ratio = current.get('volume', 0) / prev['volume']
            if volume_ratio > 5:
                anomalies.append('volume_spike')
            elif volume_ratio < 0.2:
                anomalies.append('volume_dry')
        
        # Длинная верхняя тень (отклонение вверх)
        upper_wick = current['high'] - max(current['open'], current['close'])
        body = abs(current['close'] - current['open'])
        if body > 0 and upper_wick > body * 3:
            anomalies.append('long_upper_wick')
        
        # Длинная нижняя тень (отклонение вниз)
        lower_wick = min(current['open'], current['close']) - current['low']
        if body > 0 and lower_wick > body * 3:
            anomalies.append('long_lower_wick')
        
        return anomalies
    
    def _calculate_confidence_and_signal(self, analysis: Dict) -> tuple[float, str]:
        """
        Расчет уверенности в сигнале и определение направления.
        
        :return: (confidence, signal)
        """
        patterns = analysis.get('patterns', [])
        anomalies = analysis.get('anomalies', [])
        movement = analysis.get('movement_analysis', {})
        
        confidence = 0.0
        bullish_signals = 0
        bearish_signals = 0
        
        # Веса паттернов
        pattern_weights = {
            'doji': 0.3,  # Нейтральный, но важен
            'hammer': 0.6,  # Бычий разворот
            'shooting_star': 0.6,  # Медвежий разворот
            'engulfing_bullish': 0.8,
            'engulfing_bearish': 0.8,
            'inside_bar': 0.4,  # Консолидация
            'outside_bar': 0.5,  # Пробой
            'pin_bar': 0.7
        }
        
        for pattern in patterns:
            weight = pattern_weights.get(pattern, 0.3)
            if pattern in ['hammer', 'engulfing_bullish']:
                bullish_signals += weight
            elif pattern in ['shooting_star', 'engulfing_bearish']:
                bearish_signals += weight
            else:
                # Нейтральные паттерны усиливают общую уверенность
                confidence += weight * 0.5
        
        # Веса аномалий
        for anomaly in anomalies:
            if anomaly == 'volume_spike':
                confidence += 0.3
            elif anomaly == 'explosive_range':
                confidence += 0.4
            elif anomaly in ['long_upper_wick', 'long_lower_wick']:
                confidence += 0.2
        
        # Учитываем тип свечи
        candle_type = movement.get('candle_type', 'normal')
        if candle_type == 'strong_body':
            confidence += 0.3
            if movement.get('pressure') == 'bullish':
                bullish_signals += 0.3
            else:
                bearish_signals += 0.3
        elif candle_type == 'doji_like':
            confidence += 0.2  # Низкая определенность
        
        # Нормализация уверенности (максимум 1.0)
        confidence = min(1.0, confidence + (bullish_signals + bearish_signals) * 0.3)
        
        # Определение сигнала
        if bullish_signals > bearish_signals * 1.2:  # 20% порог для бычьего сигнала
            signal = 'BULLISH'
        elif bearish_signals > bullish_signals * 1.2:
            signal = 'BEARISH'
        else:
            signal = 'NEUTRAL'
        
        return confidence, signal
    
    # === ДЕТЕКТОРЫ ПАТТЕРНОВ ===
    
    def _detect_doji(self, curr: Dict, prev: Optional[Dict], prev_prev: Optional[Dict]) -> bool:
        """Doji: тело свечи очень маленькое (< 10% диапазона)."""
        range_p = curr['high'] - curr['low']
        body = abs(curr['close'] - curr['open'])
        return range_p > 0 and body / range_p < 0.1
    
    def _detect_hammer(self, curr: Dict, prev: Optional[Dict], prev_prev: Optional[Dict]) -> bool:
        """Hammer: длинная нижняя тень, маленькое тело наверху."""
        range_p = curr['high'] - curr['low']
        body = abs(curr['close'] - curr['open'])
        lower_wick = min(curr['open'], curr['close']) - curr['low']
        
        if range_p == 0:
            return False
        
        # Нижняя тень >= 2/3 диапазона, тело <= 1/3
        return lower_wick >= range_p * 0.66 and body / range_p < 0.33
    
    def _detect_shooting_star(self, curr: Dict, prev: Optional[Dict], prev_prev: Optional[Dict]) -> bool:
        """Shooting Star: длинная верхняя тень, маленькое тело внизу."""
        range_p = curr['high'] - curr['low']
        body = abs(curr['close'] - curr['open'])
        upper_wick = curr['high'] - max(curr['open'], curr['close'])
        
        if range_p == 0:
            return False
        
        return upper_wick >= range_p * 0.66 and body / range_p < 0.33
    
    def _detect_engulfing_bullish(self, curr: Dict, prev: Optional[Dict], prev_prev: Optional[Dict]) -> bool:
        """Bullish Engulfing: текущая бычья свеча полностью перекрывает предыдущую медвежью."""
        if not prev:
            return False
        
        # Предыдущая медвежья
        prev_bearish = prev['close'] < prev['open']
        # Текущая бычья
        curr_bullish = curr['close'] > curr['open']
        
        if not (prev_bearish and curr_bullish):
            return False
        
        # Текущая свеча перекрывает предыдущую
        return curr['open'] <= prev['close'] and curr['close'] >= prev['open']
    
    def _detect_engulfing_bearish(self, curr: Dict, prev: Optional[Dict], prev_prev: Optional[Dict]) -> bool:
        """Bearish Engulfing: текущая медвежья свеча полностью перекрывает предыдущую бычью."""
        if not prev:
            return False
        
        prev_bullish = prev['close'] > prev['open']
        curr_bearish = curr['close'] < curr['open']
        
        if not (prev_bullish and curr_bearish):
            return False
        
        return curr['open'] >= prev['close'] and curr['close'] <= prev['open']
    
    def _detect_inside_bar(self, curr: Dict, prev: Optional[Dict], prev_prev: Optional[Dict]) -> bool:
        """Inside Bar: текущая свеча полностью внутри диапазона предыдущей."""
        if not prev:
            return False
        
        return (curr['high'] <= prev['high'] and 
                curr['low'] >= prev['low'] and
                curr['high'] != curr['low'])  # Не flat
    
    def _detect_outside_bar(self, curr: Dict, prev: Optional[Dict], prev_prev: Optional[Dict]) -> bool:
        """Outside Bar: текущая свеча полностью перекрывает диапазон предыдущей."""
        if not prev:
            return False
        
        return (curr['high'] >= prev['high'] and 
                curr['low'] <= prev['low'] and
                curr['high'] != curr['low'])
    
    def _detect_pin_bar(self, curr: Dict, prev: Optional[Dict], prev_prev: Optional[Dict]) -> bool:
        """Pin Bar: длинная тень с одной стороны, маленькое тело."""
        range_p = curr['high'] - curr['low']
        body = abs(curr['close'] - curr['open'])
        upper_wick = curr['high'] - max(curr['open'], curr['close'])
        lower_wick = min(curr['open'], curr['close']) - curr['low']
        
        if range_p == 0:
            return False
        
        # Тело <= 30% диапазона
        if body / range_p > 0.3:
            return False
        
        # Одна из теней >= 60% диапазона
        return upper_wick >= range_p * 0.6 or lower_wick >= range_p * 0.6
