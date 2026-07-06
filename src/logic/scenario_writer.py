"""
Сценарист (Scenario Writer).
Формирует торговый сценарий на основе данных из Матрицы, индикаторов и режимов рынка.
Не принимает решений об исполнении, только описывает "Что делать, если...".
"""
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime
import logging

from src.matrix.probability_field import ProbabilityCluster
from src.math_core.market_regime import MarketRegime, RegimeResult
from src.math_core.order_book_sr import SRLevel

logger = logging.getLogger(__name__)

@dataclass
class TradeScenario:
    """Описание потенциальной сделки."""
    scenario_id: str
    timestamp: float
    symbol: str
    
    # Направление
    direction: str  # 'LONG', 'SHORT', 'WAIT'
    
    # Цели и входы
    entry_price: float
    target_prices: List[float]  # Тейк-профиты
    stop_loss: float
    
    # Обоснование
    confidence_score: float  # Итоговая уверенность (0-1)
    reasoning: List[str]     # Список причин (метки анализаторов)
    
    # Контекст
    regime: MarketRegime
    sr_levels: Dict[str, List[SRLevel]]
    
    # Параметры для Риск-менеджера
    expected_move_pct: float
    time_horizon_sec: int
    
    # Метаданные
    metadata: Dict = field(default_factory=dict)

class ScenarioWriter:
    def __init__(self, config: dict):
        self.config = config
        self.min_confidence = config.get("scenario", {}).get("min_confidence", 0.6)
        self.scenario_count = 0

    def generate_scenario(
        self,
        clusters: List[ProbabilityCluster],
        regime: RegimeResult,
        sr_levels: Dict[str, List[SRLevel]],
        current_price: float,
        sentiment: float
    ) -> Optional[TradeScenario]:
        """
        Генерирует сценарий на основе агрегированных данных.
        Возвращает None, если сценарий не соответствует критериям (например, низкая уверенность).
        """
        if not clusters:
            return None

        # 1. Анализ кластеров матрицы
        # Ищем самый сильный кластер (максимальная вероятность * объем)
        best_cluster = max(clusters, key=lambda c: c.probability * c.volume_factor)
        
        if best_cluster.probability < self.min_confidence:
            logger.debug(f"Confidence too low: {best_cluster.probability:.2f}")
            return None

        # 2. Определение направления
        direction = 'LONG' if best_cluster.expected_direction > 0 else 'SHORT'
        if abs(best_cluster.expected_direction) < 0.1: # Нейтрально
            direction = 'WAIT'

        # 3. Расчет уровней входа и выхода
        # Вход: текущая цена или ближайший уровень SR
        entry_price = current_price
        
        # Цели: на основе прогноза кластера и уровней SR
        target_prices = self._calculate_targets(direction, current_price, best_cluster, sr_levels)
        
        # Стоп: за ближайшим уровнем поддержки/сопротивления
        stop_loss = self._calculate_stop_loss(direction, current_price, sr_levels)

        # 4. Формирование обоснования (список меток)
        reasoning = [
            f"Matrix_Cluster_{best_cluster.id}",
            f"Regime_{regime.regime.value}",
            f"Sentiment_{sentiment:.2f}"
        ]
        
        # Добавляем метки от SR уровней, если они использовались
        if sr_levels:
            reasoning.append(f"SR_Levels_Found_{len(sr_levels.get('support', [])) + len(sr_levels.get('resistance', []))}")

        self.scenario_count += 1
        scenario = TradeScenario(
            scenario_id=f"SCN_{self.scenario_count}_{int(datetime.now().timestamp())}",
            timestamp=datetime.now().timestamp(),
            symbol="BTCUSDT", # TODO: брать из контекста
            direction=direction,
            entry_price=entry_price,
            target_prices=target_prices,
            stop_loss=stop_loss,
            confidence_score=best_cluster.probability,
            reasoning=reasoning,
            regime=regime.regime,
            sr_levels=sr_levels,
            expected_move_pct=abs(target_prices[0] - entry_price) / entry_price * 100 if target_prices else 0,
            time_horizon_sec=int(best_cluster.time_horizon.total_seconds()) if hasattr(best_cluster, 'time_horizon') else 300,
            metadata={
                "cluster_data": best_cluster.to_dict() if hasattr(best_cluster, 'to_dict') else {}
            }
        )

        logger.info(f"Scenario generated: {scenario.scenario_id} ({direction}, Conf: {scenario.confidence_score:.2f})")
        return scenario

    def _calculate_targets(self, direction: str, current: float, cluster: ProbabilityCluster, sr: Dict) -> List[float]:
        targets = []
        # Простая логика: цель на уровне прогноза кластера
        projected_price = current * (1 + cluster.expected_direction * 0.01) # Упрощение
        
        # Корректировка по SR уровням
        if direction == 'LONG':
            # Ищем сопротивление выше
            for level in sorted(sr.get('resistance', []), key=lambda x: x.price):
                if level.price > current:
                    targets.append(level.price)
                    break
            if not targets:
                targets.append(projected_price)
        else:
            # Ищем поддержку ниже
            for level in sorted(sr.get('support', []), key=lambda x: x.price, reverse=True):
                if level.price < current:
                    targets.append(level.price)
                    break
            if not targets:
                targets.append(projected_price)
                
        return targets

    def _calculate_stop_loss(self, direction: str, current: float, sr: Dict) -> float:
        # Стоп за ближайшим уровнем
        if direction == 'LONG':
            # Поддержка ниже
            supports = [l.price for l in sr.get('support', []) if l.price < current]
            if supports:
                return min(supports) * 0.995 # Чуть ниже уровня
            return current * 0.99 # Дефолт 1%
        else:
            # Сопротивление выше
            resists = [l.price for l in sr.get('resistance', []) if l.price > current]
            if resists:
                return max(resists) * 1.005
            return current * 1.01
