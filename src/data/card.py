"""
Data Module: Card Creator v2.0
Создает "Карточку анализа" и "Снимок сделки" - полные слепки состояния системы.
Используется Auto-Tuner для анализа влияния параметров на результат.

Изменения v2.0:
- AnalysisCard: снимок результата анализатора в конкретный момент (время, цена, значение, уверенность, доверие)
- TradeSnapshot: полный снимок закрытой сделки для лаборатории и автотюнера
- Разделение доверия (от автотюнера) и уверенности (от математической модели)
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime
import uuid


@dataclass
class AnalysisCard:
    """
    Карточка одного анализа.
    Содержит результат работы анализатора в конкретной рыночной ситуации.
    """
    card_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    # Идентификатор анализатора
    analyzer_id: str = ""  # например "g_trend_channel", "btc_correlation_pearson"
    
    # Рыночный контекст на момент анализа
    symbol: str = ""
    price: float = 0.0
    timeframe: str = ""  # "1m", "5m", etc.
    
    # Результат анализа
    analysis_type: str = ""  # "trend", "support_resistance", "correlation", "volatility", etc.
    value: Any = None  # конкретное значение (направление тренда, уровень корреляции, и т.д.)
    predicted_price: Optional[float] = None  # прогнозируемая цена (если применимо)
    predicted_time: Optional[datetime] = None  # горизонт прогноза
    horizon_seconds: int = 0  # горизонт прогноза в секундах
    
    # Вероятностные метрики
    confidence: float = 0.0  # Уверенность от математической модели (0.0-1.0)
    trust_score: float = 0.5  # Очки доверия от автотюнера (0.0-1.0, по умолчанию 0.5)
    combined_probability: float = 0.0  # (confidence + trust_score) / 2
    
    # Метаданные для воспроизведения
    input_params: Dict[str, Any] = field(default_factory=dict)  # параметры на входе
    calculation_method: str = ""  # метод расчёта (Пирсон, Спирмен, и т.д.)
    
    def __post_init__(self):
        """Вычисляет комбинированную вероятность после инициализации."""
        if self.confidence >= 0 and self.trust_score >= 0:
            self.combined_probability = (self.confidence + self.trust_score) / 2.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертирует карточку в словарь для сохранения."""
        return {
            "card_id": self.card_id,
            "timestamp": self.timestamp.isoformat(),
            "analyzer_id": self.analyzer_id,
            "symbol": self.symbol,
            "price": self.price,
            "timeframe": self.timeframe,
            "analysis_type": self.analysis_type,
            "value": self.value,
            "predicted_price": self.predicted_price,
            "predicted_time": self.predicted_time.isoformat() if self.predicted_time else None,
            "horizon_seconds": self.horizon_seconds,
            "confidence": self.confidence,
            "trust_score": self.trust_score,
            "combined_probability": self.combined_probability,
            "input_params": self.input_params,
            "calculation_method": self.calculation_method
        }


@dataclass
class ScenarioCard:
    """
    Карточка сценария от Сценариста.
    Содержит план сделки с обоснованием.
    """
    scenario_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    # Параметры сделки
    symbol: str = ""
    direction: str = ""  # "LONG", "SHORT"
    entry_price: float = 0.0
    target_price: float = 0.0
    stop_loss: float = 0.0
    leverage: float = 1.0
    position_size_usd: float = 0.0
    
    # Метрики
    risk_reward_ratio: float = 0.0
    expected_profit_pct: float = 0.0
    expected_profit_usd: float = 0.0
    confidence: float = 0.0  #综合 уверенность сценария
    
    # Обоснование
    reasoning: str = ""
    strategy_type: str = ""  # "scalp", "swing", "trap" (опционально)
    
    # Ссылки на анализы, использованные для создания сценария
    analysis_cards_ids: List[str] = field(default_factory=list)
    
    # Настройки системы на момент создания
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "target_price": self.target_price,
            "stop_loss": self.stop_loss,
            "leverage": self.leverage,
            "position_size_usd": self.position_size_usd,
            "risk_reward_ratio": self.risk_reward_ratio,
            "expected_profit_pct": self.expected_profit_pct,
            "expected_profit_usd": self.expected_profit_usd,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "strategy_type": self.strategy_type,
            "analysis_cards_ids": self.analysis_cards_ids,
            "config_snapshot": self.config_snapshot
        }


@dataclass
class RiskCard:
    """
    Карточка решения Риск-менеджера.
    """
    risk_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    scenario_id: str = ""
    decision: str = ""  # "REAL", "SHADOW" (Reject удалён)
    
    # Метрики риска
    volatility_index: float = 0.0
    liquidity_risk: float = 0.0
    drawdown_prob: float = 0.0
    max_leverage_allowed: float = 0.0
    exposure_limit: float = 0.0
    
    # Решение
    approved_leverage: float = 0.0
    approved_position_size: float = 0.0
    dynamic_stop_loss: Optional[float] = None
    
    reason: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "risk_id": self.risk_id,
            "timestamp": self.timestamp.isoformat(),
            "scenario_id": self.scenario_id,
            "decision": self.decision,
            "volatility_index": self.volatility_index,
            "liquidity_risk": self.liquidity_risk,
            "drawdown_prob": self.drawdown_prob,
            "max_leverage_allowed": self.max_leverage_allowed,
            "exposure_limit": self.exposure_limit,
            "approved_leverage": self.approved_leverage,
            "approved_position_size": self.approved_position_size,
            "dynamic_stop_loss": self.dynamic_stop_loss,
            "reason": self.reason
        }


@dataclass
class TradeSnapshot:
    """
    Полный снимок закрытой сделки.
    Содержит ВСЮ информацию для Лаборатории и Автодюнера.
    Формируется Executor после закрытия позиции.
    """
    snapshot_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_open: datetime = field(default_factory=datetime.utcnow)
    timestamp_close: Optional[datetime] = None
    
    # Идентификаторы
    scenario_id: str = ""
    risk_id: str = ""
    order_id: str = ""  # ID ордера на бирже
    
    # Параметры сделки
    symbol: str = ""
    direction: str = ""  # "LONG", "SHORT"
    entry_price: float = 0.0
    exit_price: float = 0.0
    highest_price: float = 0.0  # Для трейлинга
    lowest_price: float = 0.0   # Для трейлинга
    
    # Размеры
    leverage: float = 1.0
    position_size_usd: float = 0.0
    quantity: float = 0.0
    
    # Результаты
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    commission_usd: float = 0.0
    slippage_usd: float = 0.0
    net_pnl_usd: float = 0.0  # PnL за вычетом комиссий и проскальзывания
    
    # Длительность
    duration_seconds: int = 0
    exit_reason: str = ""  # "trailing_stop", "manual", "liquidation", etc.
    
    # MAE (Max Adverse Excursion) - максимальное движение против позиции
    mae_pct: float = 0.0
    mae_price: float = 0.0
    
    # MFE (Max Favorable Excursion) - максимальное движение в пользу позиции
    mfe_pct: float = 0.0
    mfe_price: float = 0.0
    
    # Трейлинг-стоп данные
    trailing_stop_activated: bool = False
    trailing_stop_distance_pct: float = 0.0
    trailing_stop_activation_pct: float = 0.0
    
    # Снимки карточек (полные данные)
    analysis_cards: List[Dict[str, Any]] = field(default_factory=list)  # Список AnalysisCard.to_dict()
    scenario_card: Optional[Dict[str, Any]] = None  # ScenarioCard.to_dict()
    risk_card: Optional[Dict[str, Any]] = None  # RiskCard.to_dict()
    
    # Рыночный контекст на момент входа (сжатые данные для лаборатории)
    market_context: Dict[str, Any] = field(default_factory=dict)
    # {
    #   "volatility": ...,
    #   "trend": ...,
    #   "key_levels": {...},
    #   "correlations": {...},
    #   "compressed_candles": [...]  # Сжатые свечи
    # }
    
    # Настройки системы на момент сделки
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    
    # Ожидаемый vs Фактический результат
    expected_profit_usd: float = 0.0
    profit_deviation_usd: float = 0.0  # факт - ожидание
    profit_deviation_pct: float = 0.0
    
    # Метаданные для автотюнера
    tuner_analysis: Dict[str, Any] = field(default_factory=dict)
    # {
    #   "analyzer_impact": {...},  # влияние каждого анализа
    #   "config_impact": {...},     # влияние настроек
    #   "market_condition": "trending"/"ranging"/"volatile"
    # }
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертирует снимок в словарь для сохранения в БД."""
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp_open": self.timestamp_open.isoformat(),
            "timestamp_close": self.timestamp_close.isoformat() if self.timestamp_close else None,
            "scenario_id": self.scenario_id,
            "risk_id": self.risk_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "highest_price": self.highest_price,
            "lowest_price": self.lowest_price,
            "leverage": self.leverage,
            "position_size_usd": self.position_size_usd,
            "quantity": self.quantity,
            "pnl_usd": self.pnl_usd,
            "pnl_pct": self.pnl_pct,
            "commission_usd": self.commission_usd,
            "slippage_usd": self.slippage_usd,
            "net_pnl_usd": self.net_pnl_usd,
            "duration_seconds": self.duration_seconds,
            "exit_reason": self.exit_reason,
            "mae_pct": self.mae_pct,
            "mae_price": self.mae_price,
            "mfe_pct": self.mfe_pct,
            "mfe_price": self.mfe_price,
            "trailing_stop_activated": self.trailing_stop_activated,
            "trailing_stop_distance_pct": self.trailing_stop_distance_pct,
            "trailing_stop_activation_pct": self.trailing_stop_activation_pct,
            "analysis_cards": self.analysis_cards,
            "scenario_card": self.scenario_card,
            "risk_card": self.risk_card,
            "market_context": self.market_context,
            "config_snapshot": self.config_snapshot,
            "expected_profit_usd": self.expected_profit_usd,
            "profit_deviation_usd": self.profit_deviation_usd,
            "profit_deviation_pct": self.profit_deviation_pct,
            "tuner_analysis": self.tuner_analysis
        }


class CardCreator:
    """Фабрика для создания и управления карточками."""
    
    def __init__(self, config_snapshot: Dict[str, Any]):
        """
        :param config_snapshot: Снимок конфигурации на момент запуска.
        """
        self.config_snapshot = config_snapshot
    
    def create_analysis_card(
        self,
        analyzer_id: str,
        symbol: str,
        price: float,
        timeframe: str,
        analysis_type: str,
        value: Any,
        confidence: float,
        trust_score: float = 0.5,
        predicted_price: Optional[float] = None,
        horizon_seconds: int = 0,
        input_params: Optional[Dict] = None,
        calculation_method: str = ""
    ) -> AnalysisCard:
        """Создаёт карточку анализа."""
        card = AnalysisCard(
            analyzer_id=analyzer_id,
            symbol=symbol,
            price=price,
            timeframe=timeframe,
            analysis_type=analysis_type,
            value=value,
            confidence=confidence,
            trust_score=trust_score,
            predicted_price=predicted_price,
            horizon_seconds=horizon_seconds,
            input_params=input_params or {},
            calculation_method=calculation_method
        )
        return card
    
    def create_scenario_card(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        target_price: float,
        stop_loss: float,
        leverage: float,
        position_size_usd: float,
        risk_reward_ratio: float,
        expected_profit_pct: float,
        confidence: float,
        reasoning: str,
        analysis_cards_ids: List[str],
        strategy_type: str = ""
    ) -> ScenarioCard:
        """Создаёт карточку сценария."""
        expected_profit_usd = position_size_usd * leverage * (expected_profit_pct / 100.0)
        
        card = ScenarioCard(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            target_price=target_price,
            stop_loss=stop_loss,
            leverage=leverage,
            position_size_usd=position_size_usd,
            risk_reward_ratio=risk_reward_ratio,
            expected_profit_pct=expected_profit_pct,
            expected_profit_usd=expected_profit_usd,
            confidence=confidence,
            reasoning=reasoning,
            strategy_type=strategy_type,
            analysis_cards_ids=analysis_cards_ids,
            config_snapshot=self.config_snapshot.copy()
        )
        return card
    
    def create_risk_card(
        self,
        scenario_id: str,
        decision: str,
        volatility_index: float,
        liquidity_risk: float,
        drawdown_prob: float,
        max_leverage_allowed: float,
        exposure_limit: float,
        approved_leverage: float,
        approved_position_size: float,
        reason: str = "",
        dynamic_stop_loss: Optional[float] = None
    ) -> RiskCard:
        """Создаёт карточку решения риск-менеджера."""
        card = RiskCard(
            scenario_id=scenario_id,
            decision=decision,
            volatility_index=volatility_index,
            liquidity_risk=liquidity_risk,
            drawdown_prob=drawdown_prob,
            max_leverage_allowed=max_leverage_allowed,
            exposure_limit=exposure_limit,
            approved_leverage=approved_leverage,
            approved_position_size=approved_position_size,
            dynamic_stop_loss=dynamic_stop_loss,
            reason=reason
        )
        return card
    
    def create_trade_snapshot(
        self,
        scenario_card: ScenarioCard,
        risk_card: RiskCard,
        analysis_cards: List[AnalysisCard],
        entry_price: float,
        direction: str,
        symbol: str,
        leverage: float,
        position_size_usd: float,
        quantity: float,
        config_snapshot: Optional[Dict] = None
    ) -> TradeSnapshot:
        """
        Создаёт базовый снимок сделки при открытии.
        Полное заполнение происходит при закрытии.
        """
        snapshot = TradeSnapshot(
            scenario_id=scenario_card.scenario_id,
            risk_id=risk_card.risk_id,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            leverage=leverage,
            position_size_usd=position_size_usd,
            quantity=quantity,
            analysis_cards=[card.to_dict() for card in analysis_cards],
            scenario_card=scenario_card.to_dict(),
            risk_card=risk_card.to_dict(),
            config_snapshot=config_snapshot or self.config_snapshot.copy(),
            expected_profit_usd=scenario_card.expected_profit_usd
        )
        return snapshot
    
    def finalize_trade_snapshot(
        self,
        snapshot: TradeSnapshot,
        exit_price: float,
        pnl_usd: float,
        pnl_pct: float,
        commission_usd: float,
        slippage_usd: float,
        duration_seconds: int,
        exit_reason: str,
        highest_price: float,
        lowest_price: float,
        mae_pct: float,
        mae_price: float,
        mfe_pct: float,
        mfe_price: float,
        trailing_activated: bool = False,
        trailing_distance_pct: float = 0.0,
        trailing_activation_pct: float = 0.0,
        market_context: Optional[Dict] = None
    ):
        """
        Заполняет снимок данными после закрытия сделки.
        Вычисляет отклонения и метрики для автотюнера.
        """
        snapshot.timestamp_close = datetime.utcnow()
        snapshot.exit_price = exit_price
        snapshot.pnl_usd = pnl_usd
        snapshot.pnl_pct = pnl_pct
        snapshot.commission_usd = commission_usd
        snapshot.slippage_usd = slippage_usd
        snapshot.net_pnl_usd = pnl_usd - commission_usd - slippage_usd
        snapshot.duration_seconds = duration_seconds
        snapshot.exit_reason = exit_reason
        snapshot.highest_price = highest_price
        snapshot.lowest_price = lowest_price
        snapshot.mae_pct = mae_pct
        snapshot.mae_price = mae_price
        snapshot.mfe_pct = mfe_pct
        snapshot.mfe_price = mfe_price
        snapshot.trailing_stop_activated = trailing_activated
        snapshot.trailing_stop_distance_pct = trailing_distance_pct
        snapshot.trailing_stop_activation_pct = trailing_activation_pct
        
        if market_context:
            snapshot.market_context = market_context
        
        # Вычисление отклонений
        snapshot.profit_deviation_usd = snapshot.net_pnl_usd - snapshot.expected_profit_usd
        if snapshot.expected_profit_usd != 0:
            snapshot.profit_deviation_pct = (snapshot.profit_deviation_usd / snapshot.expected_profit_usd) * 100
        else:
            snapshot.profit_deviation_pct = 0.0
        
        # Автоматический расчёт влияния для автотюнера
        self._calculate_tuner_analysis(snapshot)
    
    def _calculate_tuner_analysis(self, snapshot: TradeSnapshot):
        """
        Вычисляет влияние каждого анализа и настройки на результат.
        Используется автотюнером для корректировки доверия и параметров.
        """
        is_profitable = snapshot.net_pnl_usd > 0
        
        # Влияние анализов
        analyzer_impact = {}
        for analysis in snapshot.analysis_cards:
            analyzer_id = analysis.get('analyzer_id', 'unknown')
            confidence = analysis.get('confidence', 0.5)
            trust_score = analysis.get('trust_score', 0.5)
            combined = analysis.get('combined_probability', 0.5)
            
            # Простая эвристика: если сделка прибыльна, анализ с высокой вероятностью полезен
            # В реальном автотюнере будет более сложный статистический анализ
            impact_score = combined if is_profitable else -combined
            
            analyzer_impact[analyzer_id] = {
                "confidence": confidence,
                "trust_score": trust_score,
                "combined_probability": combined,
                "impact_score": impact_score,
                "was_useful": is_profitable
            }
        
        # Влияние настроек
        config_impact = {}
        config = snapshot.config_snapshot
        if 'risk' in config:
            config_impact['leverage'] = {
                "value": config['risk'].get('max_leverage', 1.0),
                "impact": impact_score if is_profitable else -impact_score
            }
            config_impact['position_size_pct'] = {
                "value": config['risk'].get('max_position_size_pct', 0.66),
                "impact": impact_score if is_profitable else -impact_score
            }
        
        # Определение рыночных условий
        market_condition = "unknown"
        if 'market_context' in snapshot.market_context:
            ctx = snapshot.market_context['market_context']
            vol = ctx.get('volatility', 0.01)
            if vol > 0.03:
                market_condition = "volatile"
            elif ctx.get('trend', '') == 'SIDEWAYS':
                market_condition = "ranging"
            else:
                market_condition = "trending"
        
        snapshot.tuner_analysis = {
            "analyzer_impact": analyzer_impact,
            "config_impact": config_impact,
            "market_condition": market_condition,
            "is_profitable": is_profitable,
            "profit_deviation_pct": snapshot.profit_deviation_pct
        }
