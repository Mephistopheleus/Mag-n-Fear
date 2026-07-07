"""
Core Data Models for Mag-n-Fear Robot.
Unified data structures for asynchronous communication between modules.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum
import time


class SignalType(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"
    CLOSE_ALL = "CLOSE_ALL"


@dataclass
class NewsVector:
    """
    Вектор влияния новости.
    Направление: +1 (бычье), -1 (медвежье), 0 (нейтрально).
    Сила: 0.0 - 1.0 (impact score).
    Время_действия: секунды до затухания эффекта.
    Вероятность: 0.0 - 1.0 (достоверность источника/события).
    """
    direction: float  # -1.0 to 1.0
    strength: float   # 0.0 to 1.0
    duration_sec: int
    probability: float # 0.0 to 1.0
    source_id: str
    timestamp: float = field(default_factory=time.time)
    headline: str = ""

    def get_impact_score(self) -> float:
        """Итоговый вес новости для матрицы."""
        return self.direction * self.strength * self.probability


@dataclass
class RiskMetrics:
    """
    Метрики риска, рассчитанные RiskManager.
    Используются как ограничения для ScenarioWriter.
    """
    max_leverage: float       # Максимально допустимое плечо сейчас
    liquidity_risk: float     # 0.0 (ok) - 1.0 (critical)
    drawdown_prob: float      # Вероятность сильного просадки
    volatility_index: float   # Текущий индекс волатильности
    exposure_limit: float     # Максимальный % баланса в сделке
    is_emergency: bool        # Флаг аварийной ситуации
    reason: str = ""


@dataclass
class DataCard:
    """
    Основной контейнер данных, передаваемый между модулями.
    Содержит сырые данные, результаты анализа и метрики.
    """
    symbol: str
    timestamp: float
    
    # --- Рыночные данные (сырые и агрегированные) ---
    price: float
    volume_24h: float
    orderbook_snapshot: Dict[str, Any] = field(default_factory=dict) # {bids: [], asks: []}
    recent_trades: List[Dict] = field(default_factory=list)
    
    # --- Данные кросс-корреляции (например, BTC) ---
    correlation_data: Dict[str, float] = field(default_factory=dict) # {"BTCUSDT": price, "change": %}
    
    # --- Результаты работы MathCore (производные) ---
    # "Простыни" данных: ключ - таймфрейм/индикатор, значение - массив/значение
    math_surfaces: Dict[str, Any] = field(default_factory=dict) 
    
    # --- Векторы новостей (от NewsAggregator) ---
    news_vectors: List[NewsVector] = field(default_factory=list)
    
    # --- Метрики риска (от RiskManager) ---
    risk_metrics: Optional[RiskMetrics] = None
    
    # --- Мета-данные от AutoTuner ---
    tuner_confidence: float = 0.5  # Доверие к текущей стратегии (0-1)
    active_strategy_id: str = "default"
    # --- Результаты теневого расчета (от RiskManager) ---
    shadow_results: Dict[str, Any] = field(default_factory=dict)
    
    # --- Служебное ---
    sequence_id: int = 0  # Для отслеживания порядка событий

    def update_risk(self, metrics: RiskMetrics):
        """Безопасное обновление метрик риска."""
        self.risk_metrics = metrics

    def add_news_vector(self, vector: NewsVector):
        """Добавление новости с очисткой устаревших."""
        now = time.time()
        # Удаляем протухшие новости
        self.news_vectors = [
            n for n in self.news_vectors 
            if (now - n.timestamp) < n.duration_sec
        ]
        self.news_vectors.append(vector)

    def get_aggregated_sentiment(self) -> float:
        """
        Агрегированный сентимент по всем новостям и техническим данным.
        Возвращает значение от -1.0 (медвежье) до 1.0 (бычье).
        """
        if not self.news_vectors:
            return 0.0
        
        total_weight = 0.0
        weighted_sum = 0.0
        
        for vec in self.news_vectors:
            weight = vec.strength * vec.probability
            weighted_sum += vec.direction * weight
            total_weight += weight
            
        if total_weight == 0:
            return 0.0
            
        return weighted_sum / total_weight
