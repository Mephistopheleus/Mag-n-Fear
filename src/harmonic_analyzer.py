"""
HarmonicAnalyzer Module
Implements G-Trend Channel and Harmonic Patterns (Gartley, Bat, Crab)
for precise target calculation and risk assessment.
"""
import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Tuple

class HarmonicAnalyzer:
    def __init__(self, config: dict):
        self.config = config
        # Fibonacci ratios for harmonic patterns
        self.fib_ratios = {
            'gartley': {'b': 0.618, 'c': [0.382, 0.886], 'd': 0.786},
            'bat': {'b': 0.5, 'c': [0.382, 0.886], 'd': 0.886},
            'crab': {'b': 0.618, 'c': [0.382, 0.886], 'd': 1.618}
        }
        self.min_swing_points = 5
        
    def find_extremums(self, prices: pd.Series, window: int = 5) -> List[Tuple[int, float, str]]:
        """
        Find local extremums (Highs/Lows) in price data.
        Returns list of (index, price, type) tuples.
        """
        extremums = []
        n = len(prices)
        
        for i in range(window, n - window):
            current_window = prices.iloc[i-window:i+window+1]
            center = prices.iloc[i]
            
            if center == current_window.max():
                extremums.append((i, center, 'H'))
            elif center == current_window.min():
                extremums.append((i, center, 'L'))
                
        return extremums

    def calculate_g_channel(self, prices: pd.Series) -> Optional[Dict]:
        """
        Calculate G-Trend Channel based on recent significant swings.
        Returns channel boundaries and confidence score.
        """
        extremums = self.find_extremums(prices)
        
        if len(extremums) < 4:
            return None
            
        # Take last 4 significant points to form channel
        recent = extremums[-4:]
        
        highs = [p for i, p, t in recent if t == 'H']
        lows = [p for i, p, t in recent if t == 'L']
        
        if not highs or not lows:
            return None
            
        # Simple linear regression for channel lines
        upper_line = max(highs)
        lower_line = min(lows)
        width = upper_line - lower_line
        
        current_price = prices.iloc[-1]
        position_in_channel = (current_price - lower_line) / width if width > 0 else 0.5
        
        return {
            'upper': upper_line,
            'lower': lower_line,
            'width': width,
            'position': position_in_channel, # 0.0 = bottom, 1.0 = top
            'trend': 'BULLISH' if position_in_channel > 0.5 else 'BEARISH',
            'confidence': min(1.0, len(recent) / 5.0)
        }

    def detect_pattern(self, prices: pd.Series, pattern_type: str = 'gartley') -> Optional[Dict]:
        """
        Detect specific harmonic pattern (Gartley, Bat, Crab).
        Returns pattern details if found, None otherwise.
        """
        extremums = self.find_extremums(prices)
        if len(extremums) < 5:
            return None
            
        # Analyze last 5 points for XA, AB, BC, CD legs
        points = extremums[-5:]
        if len(points) != 5:
            return None
            
        x, a, b, c, d = points
        px, pa, pb, pc, pd = [p[1] for p in points]
        types = [p[2] for p in points]
        
        # Validate pattern structure (alternating High/Low)
        if not all(types[i] != types[i+1] for i in range(4)):
            return None
            
        # Calculate ratios
        try:
            xa = abs(pa - px)
            ab = abs(pb - pa)
            bc = abs(pc - pb)
            cd = abs(pd - pc)
            
            ratio_b = ab / xa if xa > 0 else 0
            ratio_c = bc / ab if ab > 0 else 0
            ratio_d = cd / bc if bc > 0 else 0
            
            target_ratio = self.fib_ratios[pattern_type]
            
            # Check if ratios match pattern requirements (with tolerance)
            tol = 0.15 # 15% tolerance
            b_match = abs(ratio_b - target_ratio['b']) < tol
            c_match = any(abs(ratio_c - c) < tol for c in target_ratio['c'])
            d_match = abs(ratio_d - target_ratio['d']) < tol
            
            if b_match and c_match and d_match:
                # Calculate potential reversal zone (PRZ)
                prz = pd + (pd - pc) * 0.5 # Simple extension
                
                return {
                    'type': pattern_type,
                    'points': {'X': px, 'A': pa, 'B': pb, 'C': pc, 'D': pd},
                    'ratios': {'AB/XA': ratio_b, 'BC/AB': ratio_c, 'CD/BC': ratio_d},
                    'prz': prz,
                    'direction': 'SELL' if types[4] == 'H' else 'BUY',
                    'confidence': 0.8 # Base confidence for valid pattern
                }
        except ZeroDivisionError:
            return None
            
        return None

    def get_targets(self, entry_price: float, direction: str, channel_data: Optional[Dict]) -> Dict:
        """
        Calculate take-profit targets based on G-Channel and harmonic levels.
        """
        if not channel_data:
            return {'tp1': entry_price * 1.01, 'tp2': entry_price * 1.02}
            
        width = channel_data['width']
        upper = channel_data['upper']
        lower = channel_data['lower']
        
        if direction == 'BUY':
            tp1 = lower + width * 0.5
            tp2 = upper
            sl = lower - width * 0.1
        else:
            tp1 = upper - width * 0.5
            tp2 = lower
            sl = upper + width * 0.1
            
        return {'tp1': tp1, 'tp2': tp2, 'sl': sl}
