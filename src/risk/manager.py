"""
RiskManager Module.
Calculates risk metrics, validates scenarios, and performs shadow calculation.
Acts as an analyzer writing to ProbabilityField and as a final gatekeeper.
"""
import asyncio
import time
from typing import Optional, Dict, Any
from src.core.models import DataCard, RiskMetrics
from src.core.field import ProbabilityField


class RiskManager:
    """
    Риск-менеджер.
    
    Функции:
    1. Анализ текущих рыночных условий (волатильность, ликвидность).
    2. Расчет ограничений (max_leverage, exposure_limit).
    3. Валидация сценариев от ScenarioWriter.
    4. Теневой расчет: сравнение идеального сценария с реальностью.
    5. Экстренное закрытие при критических рисках.
    """
    
    def __init__(self, config: Any, probability_field: ProbabilityField):
        # Конвертируем Pydantic модель в dict для совместимости
        if hasattr(config, 'model_dump'):
            self.config = config.model_dump()
        else:
            self.config = config
            
        self.field = probability_field
        
        # Настройки из конфига (с дефолтами для старта)
        risk_cfg = self.config.get('risk', {})
        self.min_leverage = risk_cfg.get('min_leverage', 1.0)
        self.max_leverage_default = risk_cfg.get('max_leverage', 5.0)
        self.max_exposure_pct = risk_cfg.get('max_exposure_pct', 0.05)  # 5% баланса
        self.volatility_threshold = risk_cfg.get('volatility_threshold', 0.05)  # 5% движение
        
        # Состояние
        self._active_positions: Dict[str, Dict] = {}  # symbol -> position info
        self._shadow_scenarios: Dict[str, Dict] = {}  # symbol -> last shadow calc
        
    async def analyze_and_update(self, symbol: str):
        """
        Основной цикл анализа.
        Считывает данные из ProbabilityField, считает метрики, записывает обратно.
        """
        card = await self.field.get_card(symbol)
        if not card:
            return
            
        # 1. Расчет волатильности (упрощенно по недавним сделкам)
        volatility = self._calculate_volatility(card.recent_trades)
        
        # 2. Оценка ликвидности (по стакану)
        liq_risk = self._calculate_liquidity_risk(card.orderbook_snapshot, card.price)
        
        # 3. Вероятность просадки (на основе волатильности и экспозиции)
        drawdown_prob = self._estimate_drawdown_prob(volatility, liq_risk)
        
        # 4. Динамическое ограничение плеча
        # Чем выше волатильность/риск ликвидности, тем ниже допустимое плечо
        dynamic_max_leverage = self._calc_dynamic_leverage(volatility, liq_risk)
        
        # 5. Лимит экспозиции
        exposure_limit = self.max_exposure_pct * (1.0 - liq_risk)  # Снижаем лимит при плохой ликвидности
        
        metrics = RiskMetrics(
            max_leverage=dynamic_max_leverage,
            liquidity_risk=liq_risk,
            drawdown_prob=drawdown_prob,
            volatility_index=volatility,
            exposure_limit=exposure_limit,
            is_emergency=(liq_risk > 0.9 or volatility > self.volatility_threshold * 2),
            reason="High volatility" if volatility > self.volatility_threshold * 2 else ""
        )
        
        # Запись метрик в матрицу
        await self.field.update_risk_metrics(symbol, metrics)
        
        # 6. Проверка активных сценариев (теневой расчет)
        await self._shadow_check(symbol, card, metrics)

    def _calculate_volatility(self, trades: list) -> float:
        """Расчет волатильности по последним сделкам."""
        if len(trades) < 2:
            return 0.0
        
        prices = [t.get('price', 0) for t in trades if t.get('price')]
        if not prices or min(prices) == 0:
            return 0.0
            
        avg_price = sum(prices) / len(prices)
        variance = sum((p - avg_price) ** 2 for p in prices) / len(prices)
        std_dev = variance ** 0.5
        
        # Нормализованная волатильность (коэф. вариации)
        return std_dev / avg_price if avg_price > 0 else 0.0

    def _calculate_liquidity_risk(self, orderbook: dict, current_price: float) -> float:
        """
        Оценка риска ликвидности.
        0.0 - отличный стакан, 1.0 - катастрофа.
        """
        if not orderbook or 'bids' not in orderbook or 'asks' not in orderbook:
            return 0.5  # Средний риск при отсутствии данных
        
        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])
        
        if not bids or not asks:
            return 0.8
        
        # Глубина стакана (сумма объемов вблизи цены)
        bid_depth = sum(float(b[1]) for b in bids[:10])  # Топ 10 уровней
        ask_depth = sum(float(a[1]) for a in asks[:10])
        
        # Спред
        best_bid = float(bids[0][0]) if bids else current_price
        best_ask = float(asks[0][0]) if asks else current_price
        spread = (best_ask - best_bid) / current_price if current_price > 0 else 0
        
        # Эвристика риска
        total_depth = bid_depth + ask_depth
        depth_risk = max(0, 1.0 - (total_depth / 100000))  # Нормализация (примерно)
        spread_risk = min(1.0, spread * 100)  # Штраф за широкий спред
        
        return (depth_risk + spread_risk) / 2

    def _estimate_drawdown_prob(self, volatility: float, liq_risk: float) -> float:
        """Оценка вероятности сильной просадки."""
        # Простая модель: высокая волатильность + низкая ликвидность = высокий риск
        base_prob = volatility * 5  # Масштабирование
        liq_factor = 1.0 + liq_risk
        return min(1.0, base_prob * liq_factor)

    def _calc_dynamic_leverage(self, volatility: float, liq_risk: float) -> float:
        """Динамический расчет максимального плеча."""
        # Базовое плечо
        base = self.max_leverage_default
        
        # Снижение при высокой волатильности
        vol_factor = max(0.1, 1.0 - (volatility * 10))
        
        # Снижение при плохой ликвидности
        liq_factor = max(0.1, 1.0 - liq_risk)
        
        dynamic = base * vol_factor * liq_factor
        return max(self.min_leverage, min(dynamic, self.max_leverage_default))

    async def validate_scenario(self, symbol: str, scenario: Dict[str, Any]) -> tuple[bool, str]:
        """
        Валидация сценария от ScenarioWriter.
        Возвращает (True/False, reason).
        """
        card = await self.field.get_card(symbol)
        if not card or not card.risk_metrics:
            return False, "No data or risk metrics available"
            
        metrics = card.risk_metrics
        
        # Проверка плеча
        req_leverage = scenario.get('leverage', 1.0)
        if req_leverage > metrics.max_leverage:
            return False, f"Leverage {req_leverage} exceeds limit {metrics.max_leverage:.2f}"
        
        # Проверка экспозиции
        qty = scenario.get('quantity', 0)
        price = scenario.get('price', card.price)
        notional = qty * price
        
        # Здесь нужна логика получения баланса (пока заглушка)
        # balance = ... 
        # if notional > balance * metrics.exposure_limit: ...
        
        # Проверка аварийных флагов
        if metrics.is_emergency:
            return False, f"Emergency mode: {metrics.reason}"
            
        return True, "OK"

    async def _shadow_check(self, symbol: str, card: DataCard, metrics: RiskMetrics):
        """
        Теневой расчет: сравнение текущего состояния с идеальным сценарием.
        Если расхождение велико -> сигнал на корректировку.
        """
        if symbol not in self._shadow_scenarios:
            return
            
        shadow = self._shadow_scenarios[symbol]
        ideal_sl = shadow.get('ideal_stop_loss')
        current_sl = shadow.get('current_stop_loss')
        
        if ideal_sl and current_sl:
            divergence = abs(ideal_sl - current_sl) / card.price
            if divergence > 0.005:  # Расхождение > 0.5%
                # Тут можно отправить команду Executor на обновление стопа
                print(f"[RiskManager] Shadow divergence detected for {symbol}: {divergence:.4f}")

    def register_scenario(self, symbol: str, scenario: Dict[str, Any]):
        """Регистрация сценария для теневого отслеживания."""
        self._shadow_scenarios[symbol] = {
            'ideal_stop_loss': scenario.get('stop_loss'),
            'current_stop_loss': scenario.get('stop_loss'),
            'entry_price': scenario.get('entry_price'),
            'timestamp': time.time()
        }

    def update_position(self, symbol: str, position_info: Dict):
        """Обновление информации о позиции."""
        self._active_positions[symbol] = position_info

    def remove_position(self, symbol: str):
        """Удаление позиции после закрытия."""
        self._active_positions.pop(symbol, None)
        self._shadow_scenarios.pop(symbol, None)
