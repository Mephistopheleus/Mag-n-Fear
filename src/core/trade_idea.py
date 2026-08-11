"""
TradeIdea Structure
Представляет собой "сырую" торговую идею, сгенерированную ScenarioWriter.
Не содержит данных о рисках или конечном объеме позиции.
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import time
import uuid


class ScenarioType(Enum):
    """Типы торговых сценариев для обучения Автотюнера"""
    SCALPING_LONG = "scalping_long"
    SCALPING_SHORT = "scalping_short"
    BREAKOUT_LONG = "breakout_long"
    BREAKOUT_SHORT = "breakout_short"
    PULLBACK_LONG = "pullback_long"
    PULLBACK_SHORT = "pullback_short"
    REVERSAL_LONG = "reversal_long"
    REVERSAL_SHORT = "reversal_short"
    LIQUIDITY_RUN = "liquidity_run"
    MEAN_REVERSION = "mean_reversion"
    ARBITRAGE_CORRELATION = "arbitrage_correlation"
    # Можно добавить более экзотические типы по мере развития системы


@dataclass
class TradeIdea:
    """
    Торговая идея, сгенерированная Сценаристом.
    Содержит геометрию сделки и прогноз, но не финальные параметры риска.
    """
    idea_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    
    # Базовая информация
    symbol: str = ""
    scenario_type: ScenarioType = ScenarioType.SCALPING_LONG
    direction: int = 1  # 1 = Long, -1 = Short
    
    # Геометрия сделки (рассчитывается Сценаристом)
    entry_price: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    
    # Прогнозные данные (от Анализаторов)
    target_price: float = 0.0       # Куда цена должна прийти
    target_time_sec: float = 0.0    # Ожидаемое время движения
    expected_volatility: float = 0.0 # Ожидаемая волатильность на период
    
    # Математическое обоснование
    confidence: float = 0.5         # Базовая уверенность (от Анализаторов)
    logic_tags: List[str] = field(default_factory=list)  # Теги логики (напр. ["POC_Above", "Trend_Resonance"])
    reason: str = ""                # Текстовое описание причины входа
    
    # Рекомендуемое плечо (рассчитывается Сценаристом исходя из геометрии)
    # Формула: (Risk_Reward_Target * Distance_to_SL) / Distance_to_TP ... упрощенно
    recommended_leverage: float = 1.0
    
    # Сырые данные из матрицы (для отладки и Тюнера)
    matrix_snapshot: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if self.scenario_type is None:
            self.scenario_type = Scenario.SCALPING_LONG
            
    @property
    def risk_reward_ratio(self) -> float:
        """Расчет потенциального соотношения Риск/Прибыль"""
        if self.direction == 1:  # Long
            risk = self.entry_price - self.stop_loss_price
            reward = self.take_profit_price - self.entry_price
        else:  # Short
            risk = self.stop_loss_price - self.entry_price
            reward = self.entry_price - self.take_profit_price
            
        if risk <= 0:
            return 0.0
        return reward / risk
    
    @property
    def distance_to_sl_pct(self) -> float:
        """Расстояние до стопа в процентах"""
        if self.direction == 1:
            dist = self.entry_price - self.stop_loss_price
        else:
            dist = self.stop_loss_price - self.entry_price
        return abs(dist / self.entry_price * 100) if self.entry_price > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация для передачи в Risk Manager и БД"""
        return {
            "idea_id": self.idea_id,
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "scenario_type": self.scenario_type.value,
            "direction": "LONG" if self.direction == 1 else "SHORT",
            "entry_price": self.entry_price,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
            "target_price": self.target_price,
            "target_time_sec": self.target_time_sec,
            "confidence": self.confidence,
            "logic_tags": self.logic_tags,
            "reason": self.reason,
            "recommended_leverage": self.recommended_leverage,
            "risk_reward_ratio": self.risk_reward_ratio,
            "distance_to_sl_pct": self.distance_to_sl_pct
        }
