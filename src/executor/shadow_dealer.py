"""
Shadow Dealer - "Теневик".
Исполняет сделки в симуляции, записывая результаты для обучения AutoTuner.
Не использует реальные деньги, но максимально приближен к реальности.
"""
import asyncio
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class ShadowTrade:
    """Запись о теневой сделке."""
    id: str
    symbol: str
    direction: str  # BUY / SELL
    entry_price: float
    quantity: float
    leverage: float
    timestamp_open: float
    scenario_id: str
    
    # Результаты
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_percent: Optional[float] = None
    timestamp_close: Optional[float] = None
    reason: Optional[str] = None  # 'take_profit', 'stop_loss', 'timeout', 'manual'
    
    # Метрики для обучения
    max_drawdown: float = 0.0
    max_profit: float = 0.0
    duration_sec: float = 0.0
    
    # Уровни TP/SL из сценария
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    
    # Данные анализаторов для обучения AutoTuner (из оригинального сценария)
    analyzer_trend_useful: bool = False
    analyzer_mean_reversion_useful: bool = False
    analyzer_order_flow_useful: bool = False
    analyzer_volatility_useful: bool = False
    analyzer_matrix_useful: bool = False
    
    analyzer_trend_confidence: float = 0.0
    analyzer_mean_reversion_confidence: float = 0.0
    analyzer_order_flow_confidence: float = 0.0
    analyzer_volatility_confidence: float = 0.0
    analyzer_matrix_confidence: float = 0.0
    
    # Данные о рыночных условиях
    market_trend: str = "NEUTRAL"
    market_volatility: float = 0.0
    market_volume: float = 0.0
    
    # Стратегия и уверенность
    strategy_type: str = "unknown"
    confidence: float = 0.0
    risk_reward_ratio: float = 0.0
    
    # Флаг сохранения карточки
    _card_saved: bool = False

class ShadowDealer:
    """
    Симулирует исполнение сделок для сбора данных об эффективности стратегий.
    Использует реальные комиссии и проскальзывание из конфига с запасом для симуляции.
    """
    def __init__(self, config: Any):
        self.config = config
        
        # Конвертируем Pydantic модель в dict для совместимости
        if hasattr(config, 'dict'):
            cfg_dict = config.dict()
        elif hasattr(config, 'model_dump'):
            cfg_dict = config.model_dump()
        else:
            cfg_dict = config
            
        risk_cfg = cfg_dict.get('risk', {})
        
        # Параметры симуляции - берем из конфига с запасом для реалистичности
        self.slippage = risk_cfg.get('slippage_buffer', 0.0005)  # 0.05% проскальзывание
        self.commission = risk_cfg.get('commission_rate', 0.0002) + risk_cfg.get('commission_buffer', 0.0003)  # 0.05% комиссия с запасом
        
        self.symbol = cfg_dict.get('data', {}).get('symbols', ['DOGEUSDT'])[0]
        self.active_trades: Dict[str, ShadowTrade] = {}
        self.closed_trades: List[ShadowTrade] = []
        self._lock = asyncio.Lock()

    async def execute_scenario(self, scenario: Dict[str, Any]) -> ShadowTrade:
        """
        Открывает теневую сделку по сценарию.
        Теперь передает TP и SL из сценария в сделку.
        Добавляет дефолтное quantity если отсутствует.
        Сохраняет данные анализаторов для обучения AutoTuner.
        """
        async with self._lock:
            trade_id = f"shadow_{int(time.time() * 1000)}"
            
            # Симуляция входа с проскальзыванием
            entry_price = scenario.get('entry_price', 0)
            
            # Добавляем дефолтное quantity если отсутствует (для совместимости)
            quantity = scenario.get('quantity', 1.0)
            
            if scenario.get('direction') == 'BUY':
                entry_price *= (1 + self.slippage)
            else:
                entry_price *= (1 - self.slippage)
                
            trade = ShadowTrade(
                id=trade_id,
                symbol=self.symbol,
                direction=scenario.get('direction', 'BUY'),
                entry_price=entry_price,
                quantity=quantity,
                leverage=scenario.get('leverage', 1.0),
                timestamp_open=time.time(),
                scenario_id=scenario.get('id', 'unknown'),
                # Сохраняем данные анализаторов из сценария для обучения AutoTuner
                analyzer_trend_useful=scenario.get('analyzer_trend_useful', False),
                analyzer_mean_reversion_useful=scenario.get('analyzer_mean_reversion_useful', False),
                analyzer_order_flow_useful=scenario.get('analyzer_order_flow_useful', False),
                analyzer_volatility_useful=scenario.get('analyzer_volatility_useful', False),
                analyzer_matrix_useful=scenario.get('analyzer_matrix_useful', False),
                analyzer_trend_confidence=scenario.get('analyzer_trend_confidence', 0.0),
                analyzer_mean_reversion_confidence=scenario.get('analyzer_mean_reversion_confidence', 0.0),
                analyzer_order_flow_confidence=scenario.get('analyzer_order_flow_confidence', 0.0),
                analyzer_volatility_confidence=scenario.get('analyzer_volatility_confidence', 0.0),
                analyzer_matrix_confidence=scenario.get('analyzer_matrix_confidence', 0.0),
                market_trend=scenario.get('market_trend', 'NEUTRAL'),
                market_volatility=scenario.get('market_volatility', 0.0),
                market_volume=scenario.get('market_volume', 0.0),
                strategy_type=scenario.get('strategy_type', 'unknown'),
                confidence=scenario.get('confidence', 0.0),
                risk_reward_ratio=scenario.get('risk_reward_ratio', 0.0)
            )
            
            # Сохраняем TP и SL из сценария (теперь они будут использоваться)
            trade.take_profit = scenario.get('target_price', entry_price * 1.02)
            trade.stop_loss = scenario.get('stop_loss', entry_price * 0.99)
            
            self.active_trades[trade_id] = trade
            return trade

    async def update_prices(self, current_price: float):
        """
        Обновляет цены для активных теневых сделок, проверяет выход.
        """
        async with self._lock:
            trades_to_close = []
            
            for trade_id, trade in list(self.active_trades.items()):
                # Проверка Take Profit
                tp = getattr(trade, 'take_profit', None) or self._get_tp_from_scenario(trade)
                sl = getattr(trade, 'stop_loss', None) or self._get_sl_from_scenario(trade)
                
                should_close = False
                reason = None
                
                if trade.direction == 'BUY':
                    if current_price >= tp:
                        should_close = True
                        reason = 'take_profit'
                    elif current_price <= sl:
                        should_close = True
                        reason = 'stop_loss'
                else:  # SELL
                    if current_price <= tp:
                        should_close = True
                        reason = 'take_profit'
                    elif current_price >= sl:
                        should_close = True
                        reason = 'stop_loss'
                        
                # Обновление метрик
                if trade.direction == 'BUY':
                    unrealized_pnl = (current_price - trade.entry_price) / trade.entry_price
                else:
                    unrealized_pnl = (trade.entry_price - current_price) / trade.entry_price
                    
                unrealized_pnl *= trade.leverage
                
                if unrealized_pnl > trade.max_profit:
                    trade.max_profit = unrealized_pnl
                if unrealized_pnl < trade.max_drawdown:
                    trade.max_drawdown = unrealized_pnl
                    
                if should_close:
                    # Расчет PnL с учетом комиссии и спреда (реалистичный расчет)
                    # Комиссия берется дважды: при открытии и закрытии
                    total_commission = self.commission * 2
                    
                    if trade.direction == 'BUY':
                        raw_pnl_pct = (current_price - trade.entry_price) / trade.entry_price
                    else:
                        raw_pnl_pct = (trade.entry_price - current_price) / trade.entry_price
                    
                    # Вычитаем комиссию из процента прибыли
                    net_pnl_pct = raw_pnl_pct - total_commission
                    net_pnl_pct *= trade.leverage
                    
                    trade.exit_price = current_price
                    trade.pnl_percent = net_pnl_pct
                    trade.pnl = net_pnl_pct * trade.quantity * trade.entry_price
                    trade.timestamp_close = time.time()
                    trade.duration_sec = trade.timestamp_close - trade.timestamp_open
                    trade.reason = reason
                    trades_to_close.append(trade_id)
                    
            # Перенос завершенных сделок в историю
            for tid in trades_to_close:
                closed_trade = self.active_trades.pop(tid)
                self.closed_trades.append(closed_trade)

    def _get_tp_from_scenario(self, trade: ShadowTrade) -> float:
        """Получает TP из сценария (теперь берет из атрибутов сделки)."""
        # TP должен передаваться в execute_scenario и сохраняться в trade
        if hasattr(trade, 'take_profit') and trade.take_profit:
            return trade.take_profit
        # Дефолтное значение только если не задано (для совместимости)
        if trade.direction == 'BUY':
            return trade.entry_price * 1.02  # +2%
        else:
            return trade.entry_price * 0.98  # -2%

    def _get_sl_from_scenario(self, trade: ShadowTrade) -> float:
        """Получает SL из сценария (теперь берет из атрибутов сделки)."""
        if hasattr(trade, 'stop_loss') and trade.stop_loss:
            return trade.stop_loss
        # Дефолтное значение только если не задано
        if trade.direction == 'BUY':
            return trade.entry_price * 0.99  # -1%
        else:
            return trade.entry_price * 1.01  # +1%

    def get_closed_trades(self, limit: int = 10) -> List[ShadowTrade]:
        """Возвращает последние закрытые сделки для анализа."""
        return self.closed_trades[-limit:]

    def get_statistics(self) -> Dict[str, Any]:
        """Статистика по теневым сделкам."""
        if not self.closed_trades:
            return {'win_rate': 0, 'total_pnl': 0, 'count': 0}
            
        wins = sum(1 for t in self.closed_trades if t.pnl and t.pnl > 0)
        total_pnl = sum(t.pnl for t in self.closed_trades if t.pnl)
        
        return {
            'win_rate': wins / len(self.closed_trades),
            'total_pnl': total_pnl,
            'count': len(self.closed_trades),
            'avg_profit': sum(t.max_profit for t in self.closed_trades) / len(self.closed_trades),
            'avg_drawdown': sum(t.max_drawdown for t in self.closed_trades) / len(self.closed_trades)
        }
