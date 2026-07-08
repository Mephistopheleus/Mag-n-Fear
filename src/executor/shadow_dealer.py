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

class ShadowDealer:
    """
    Симулирует исполнение сделок для сбора данных об эффективности стратегий.
    """
    def __init__(self, config: Any):
        self.config = config
        self.symbol = config.trading.symbol
        self.active_trades: Dict[str, ShadowTrade] = {}
        self.closed_trades: List[ShadowTrade] = []
        self._lock = asyncio.Lock()
        
        # Параметры симуляции
        self.slippage = 0.0005  # 0.05% проскальзывание
        self.commission = 0.001  # 0.1% комиссия (как на Binance)

    async def execute_scenario(self, scenario: Dict[str, Any]) -> ShadowTrade:
        """
        Открывает теневую сделку по сценарию.
        """
        async with self._lock:
            trade_id = f"shadow_{int(time.time() * 1000)}"
            
            # Симуляция входа с проскальзыванием
            entry_price = scenario['entry_price']
            if scenario['direction'] == 'BUY':
                entry_price *= (1 + self.slippage)
            else:
                entry_price *= (1 - self.slippage)
                
            trade = ShadowTrade(
                id=trade_id,
                symbol=self.symbol,
                direction=scenario['direction'],
                entry_price=entry_price,
                quantity=scenario['quantity'],
                leverage=scenario.get('leverage', 1.0),
                timestamp_open=time.time(),
                scenario_id=scenario.get('id', 'unknown')
            )
            
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
                    trade.exit_price = current_price
                    trade.pnl = unrealized_pnl * trade.quantity * trade.entry_price
                    trade.pnl_percent = unrealized_pnl
                    trade.timestamp_close = time.time()
                    trade.duration_sec = trade.timestamp_close - trade.timestamp_open
                    trade.reason = reason
                    trades_to_close.append(trade_id)
                    
            # Перенос завершенных сделок в историю
            for tid in trades_to_close:
                closed_trade = self.active_trades.pop(tid)
                self.closed_trades.append(closed_trade)

    def _get_tp_from_scenario(self, trade: ShadowTrade) -> float:
        """Получает TP из сценария (заглушка, нужно передавать в execute_scenario)."""
        # В реальной реализации брать из сценария
        if trade.direction == 'BUY':
            return trade.entry_price * 1.02  # +2%
        else:
            return trade.entry_price * 0.98  # -2%

    def _get_sl_from_scenario(self, trade: ShadowTrade) -> float:
        """Получает SL из сценария (заглушка)."""
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
