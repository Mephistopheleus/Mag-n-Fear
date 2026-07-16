"""
Data Module: Card Creator
Создает "Карточку снимка" - полный слепок состояния системы на момент анализа/сделки.
Используется Auto-Tuner для анализа влияния параметров на результат.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime
import uuid


@dataclass
class AnalysisResult:
    """Результат работы одного анализатора."""
    analyzer_type: str  # "trend", "mean_reversion", "order_flow", etc.
    predicted_price: float
    predicted_time: datetime
    raw_probability: float  # Сырая вероятность от математической модели
    metadata: Dict[str, Any] = field(default_factory=dict)  # Дополнительные данные


@dataclass
class DataCard:
    """
    Карточка снимка состояния.
    Содержит ВСЕ данные и настройки на момент принятия решения.
    """
    card_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    # Рыночные данные на момент снимка
    symbol: str = ""
    current_price: float = 0.0
    order_book_snapshot: Dict[str, Any] = field(default_factory=dict)
    recent_trades: List[Dict] = field(default_factory=list)
    
    # Результаты анализов (от всех анализаторов)
    analyses: List[AnalysisResult] = field(default_factory=list)
    
    # Агрегированные данные из Матрицы (если есть)
    matrix_target: Optional[Dict[str, Any]] = None  # {"price": ..., "time": ..., "probability": ...}
    
    # Настройки системы на момент снимка (копия из конфига)
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    # Пример: {
    #   "tuner.confidence_factors": {...},
    #   "risk.max_position_size_usd": 1000,
    #   "scenario.min_confidence_threshold": 0.65
    # }
    
    # Решение системы
    scenario_generated: bool = False
    scenario_details: Optional[Dict[str, Any]] = None  # {"direction": "long", "entry": ..., "stop": ..., "target": ...}
    
    # Проверка риск-менеджером
    risk_approved: bool = False
    risk_details: Optional[Dict[str, Any]] = None  # {"position_size": ..., "risk_usd": ..., "reason": ...}
    
    # Результат исполнения (заполняется постфактум)
    executed: bool = False
    execution_details: Optional[Dict[str, Any]] = None  # {"order_id": ..., "fill_price": ..., "slippage": ...}
    
    # Итоговый результат сделки (заполняется после закрытия)
    trade_result: Optional[Dict[str, Any]] = None
    # Пример: {
    #   "pnl_usd": 15.5,
    #   "pnl_pct": 1.2,
    #   "duration_sec": 120,
    #   "exit_reason": "take_profit",
    #   "max_drawdown_pct": 0.3
    # }
    
    # Мета-информация для Тюнера
    tuner_notes: Dict[str, Any] = field(default_factory=dict)
    # Сюда записывается: какие анализаторы сработали, какое доверие было у каждого,
    # насколько прогноз совпал с реальностью
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертирует карточку в словарь для сохранения в БД/файл."""
        return {
            "card_id": self.card_id,
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "current_price": self.current_price,
            "analyses": [
                {
                    "analyzer_type": a.analyzer_type,
                    "predicted_price": a.predicted_price,
                    "predicted_time": a.predicted_time.isoformat() if a.predicted_time else None,
                    "raw_probability": a.raw_probability,
                    "metadata": a.metadata
                }
                for a in self.analyses
            ],
            "matrix_target": self.matrix_target,
            "config_snapshot": self.config_snapshot,
            "scenario_generated": self.scenario_generated,
            "scenario_details": self.scenario_details,
            "risk_approved": self.risk_approved,
            "risk_details": self.risk_details,
            "executed": self.executed,
            "execution_details": self.execution_details,
            "trade_result": self.trade_result,
            "tuner_notes": self.tuner_notes
        }


class CardCreator:
    """Фабрика для создания и управления карточками."""
    
    def __init__(self, config_snapshot: Dict[str, Any]):
        """
        :param config_snapshot: Снимок конфигурации на момент запуска.
        """
        self.config_snapshot = config_snapshot
    
    def create_card(
        self,
        symbol: str,
        current_price: float,
        order_book: Dict,
        trades: List[Dict],
        analyses: List[AnalysisResult]
    ) -> DataCard:
        """Создает новую карточку с базовыми данными."""
        card = DataCard(
            symbol=symbol,
            current_price=current_price,
            order_book_snapshot=order_book,
            recent_trades=trades,
            analyses=analyses,
            config_snapshot=self.config_snapshot.copy()
        )
        return card
    
    def update_with_matrix_target(self, card: DataCard, target: Dict[str, Any]):
        """Добавляет в карточку цель из Матрицы."""
        card.matrix_target = target
    
    def update_with_scenario(self, card: DataCard, scenario: Dict[str, Any]):
        """Добавляет сгенерированный сценарий."""
        card.scenario_generated = True
        card.scenario_details = scenario
    
    def update_with_risk_decision(self, card: DataCard, approved: bool, details: Dict[str, Any]):
        """Добавляет решение риск-менеджера."""
        card.risk_approved = approved
        card.risk_details = details
    
    def update_with_execution(self, card: DataCard, executed: bool, details: Optional[Dict] = None):
        """Обновляет информацию об исполнении."""
        card.executed = executed
        card.execution_details = details
    
    def finalize_card(self, card: DataCard, trade_result: Dict[str, Any]):
        """Завершает карточку результатами сделки."""
        card.trade_result = trade_result
        
        # Автоматический расчет метрик для Тюнера
        self._calculate_tuner_metrics(card)
    
    def _calculate_tuner_metrics(self, card: DataCard):
        """Вычисляет метрики влияния для Auto-Tuner."""
        if not card.trade_result or not card.analyses:
            return
        
        pnl = card.trade_result.get("pnl_usd", 0)
        is_profitable = pnl > 0
        
        # Для каждого анализатора считаем, насколько его прогноз был полезен
        for analysis in card.analyses:
            confidence_factor = card.config_snapshot.get("tuner", {}).get("confidence_factors", {}).get(analysis.analyzer_type, 0.5)
            
            # Простая эвристика: если сделка прибыльна, анализатор получил "+"
            # Если убыточна, "-"
            # В реальном Auto-Tuner будет более сложный анализ
            card.tuner_notes[f"analyzer_{analysis.analyzer_type}_useful"] = is_profitable
            card.tuner_notes[f"analyzer_{analysis.analyzer_type}_confidence"] = confidence_factor
