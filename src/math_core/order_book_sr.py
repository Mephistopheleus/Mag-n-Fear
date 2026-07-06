"""
Динамический анализ стакана (Order Book) для поиска уровней поддержки и сопротивления (SR).
Анализирует плотность лимитных ордеров и выявляет "стены".
"""
import numpy as np
from typing import List, Tuple, Dict
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

@dataclass
class SRLevel:
    price: float
    strength: float  # 0.0 - 1.0 (сила уровня)
    volume: float    # Объем на уровне
    side: str        # 'BID' (поддержка) или 'ASK' (сопротивление)
    distance_pct: float # Расстояние от текущей цены в %

class OrderBookAnalyzer:
    def __init__(self, config: dict):
        self.config = config
        self.min_wall_volume = config.get("order_book", {}).get("min_wall_volume", 10000) # Мин объем для "стены"
        self.cluster_tolerance = config.get("order_book", {}).get("cluster_tolerance_pct", 0.5) # % кластеризации цен

    def analyze_snapshot(self, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]], current_price: float) -> Dict[str, List[SRLevel]]:
        """
        Анализирует снимок стакана (bids: [(price, vol)], asks: [(price, vol)]).
        Возвращает словари с уровнями поддержки и сопротивления.
        """
        support_levels = self._find_levels(bids, current_price, 'BID')
        resistance_levels = self._find_levels(asks, current_price, 'ASK')

        return {
            "support": support_levels,
            "resistance": resistance_levels
        }

    def _find_levels(self, orders: List[Tuple[float, float]], current_price: float, side: str) -> List[SRLevel]:
        levels = []
        if not orders:
            return levels

        # Группировка ордеров по ценовым кластерам
        clusters = {}
        for price, volume in orders:
            # Округляем цену до ближайшего кластера (упрощенно)
            cluster_key = round(price / self.cluster_tolerance) * self.cluster_tolerance
            
            if cluster_key not in clusters:
                clusters[cluster_key] = 0.0
            clusters[cluster_key] += volume

        # Поиск значимых кластеров ("стен")
        for price, total_vol in clusters.items():
            if total_vol >= self.min_wall_volume:
                dist = abs(price - current_price) / current_price * 100
                strength = min(1.0, total_vol / (self.min_wall_volume * 5)) # Нормализация силы
                
                level = SRLevel(
                    price=price,
                    strength=strength,
                    volume=total_vol,
                    side=side,
                    distance_pct=dist
                )
                levels.append(level)
        
        # Сортировка по силе
        levels.sort(key=lambda x: x.strength, reverse=True)
        return levels[:5]  # Возвращаем топ-5 уровней

    def get_imbalance(self, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]], depth: int = 10) -> float:
        """
        Считает дисбаланс стакана (CVD локальный) на глубину N уровней.
        Возвращает значение от -1.0 (преобладание асков) до 1.0 (преобладание бидов).
        """
        bid_vol = sum(v for _, v in bids[:depth])
        ask_vol = sum(v for _, v in asks[:depth])
        
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        
        return (bid_vol - ask_vol) / total
