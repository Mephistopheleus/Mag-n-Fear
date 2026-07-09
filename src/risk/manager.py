"""
RiskManager Module.
Calculates risk metrics, validates scenarios, and performs shadow calculation.
Acts as an analyzer writing to ProbabilityField and as a final gatekeeper.
"""
import asyncio
import time
import logging
from typing import Optional, Dict, Any
from src.core.models import DataCard, RiskMetrics
from src.core.field import ProbabilityField

logger = logging.getLogger(__name__)


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
        if hasattr(config, 'dict'):
            self.config = config.dict()
        elif hasattr(config, 'model_dump'):
            self.config = config.model_dump()
        else:
            self.config = config
            
        self.field = probability_field
        
        # Настройки из конфига (с дефолтами для старта)
        if isinstance(self.config, dict):
            risk_cfg = self.config.get('risk', {})
        else:
            risk_cfg = getattr(self.config, 'risk', {})
            if hasattr(risk_cfg, 'dict'):
                risk_cfg = risk_cfg.dict()
            elif hasattr(risk_cfg, 'model_dump'):
                risk_cfg = risk_cfg.model_dump()
        
        self.min_leverage = risk_cfg.get('min_leverage', 1.0)
        self.max_leverage_default = risk_cfg.get('max_leverage', 5.0)
        self.max_exposure_pct = risk_cfg.get('max_exposure_pct', 0.05)  # 5% баланса
        self.volatility_threshold = risk_cfg.get('volatility_threshold', 0.05)  # 5% движение
        
        # Режим обучения (Learning Mode) - чтение из конфига
        self.learning_mode = risk_cfg.get('learning_mode', False)
        self.min_profit_threshold = risk_cfg.get('min_profit_threshold', 0.5)  # %
        
        # Состояние
        self._active_positions: Dict[str, Dict] = {}  # symbol -> position info
        self._shadow_scenarios: Dict[str, Dict] = {}  # symbol -> last shadow calc
        self.shadow_positions: Dict[str, list] = {}  # symbol -> list of shadow positions (для main.py)
        
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
        
        ВАЖНО: ВСЕ сценарии проходят проверку - и для реальных сделок, и для тени.
        Система адаптируется под рынок, а не требует "правильный" рынок.
        
        Логика:
        1. В режиме обучения (learning_mode=True) - пропускаем больше сценариев для сбора статистики
        2. На нейтральном рынке - снижаем требования к профиту, торгуем отскоки
        3. Динамический min_profit_threshold в зависимости от волатильности
        """
        card = await self.field.get_card(symbol)
        if not card or not card.risk_metrics:
            # В режиме обучения пропускаем даже без данных (для сбора статистики)
            if self.learning_mode:
                logger.info(f"[RiskManager] {symbol}: Scenario accepted for learning (no data yet)")
                return True, "Learning mode: collecting data"
            logger.warning(f"[RiskManager] {symbol}: No data or risk metrics available")
            return False, "No data or risk metrics available"
            
        metrics = card.risk_metrics
        
        # Расчет ожидаемого профита из R/R и входа
        entry_price = scenario.get('entry_price', 0)
        target_price = scenario.get('target_price', 0)
        direction = scenario.get('direction', 'LONG')
        
        if entry_price > 0 and target_price > 0:
            if direction == 'LONG':
                expected_profit_pct = ((target_price - entry_price) / entry_price) * 100
            else:
                expected_profit_pct = ((entry_price - target_price) / entry_price) * 100
        else:
            expected_profit_pct = 0.0
        
        scenario['expected_profit_pct'] = expected_profit_pct
        
        # === ДИНАМИЧЕСКИЙ ПОРОГ ПРИБЫЛИ ===
        # Адаптируем min_profit_threshold под текущую волатильность
        # Высокая волатильность = можно требовать больше прибыли
        # Низкая волатильность (флэт) = снижаем требования, чтобы торговать
        base_threshold = self.min_profit_threshold
        volatility_factor = max(0.5, 1.0 - (metrics.volatility_index * 10))  # 0.5-1.0
        dynamic_min_profit = base_threshold * volatility_factor
        
        # Если рынок нейтральный (волатильность низкая) - применяем настраиваемую скидку
        if metrics.volatility_index < 0.01:  # Очень низкая волатильность = флэт
            # Берем скидку из конфига (настраивается автотюнером), а не хардкод
            flat_discount = self.config.get('risk', {}).get('flat_market_discount', 0.7)
            dynamic_min_profit *= flat_discount
            logger.debug(f"[RiskManager] {symbol}: Flat market detected, reduced profit threshold by factor {flat_discount} to {dynamic_min_profit:.3f}%")
        
        # Проверка минимальной прибыли
        if expected_profit_pct < dynamic_min_profit:
            # В режиме обучения всё равно пропускаем для сбора статистики
            if self.learning_mode:
                logger.debug(f"[RiskManager] {symbol}: Shadow scenario low profit {expected_profit_pct:.2f}% < {dynamic_min_profit:.2f}% (allowed for learning)")
                pass  # Пропускаем для обучения
            else:
                logger.debug(f"[RiskManager] {symbol}: REJECTED - Profit {expected_profit_pct:.2f}% < dynamic threshold {dynamic_min_profit:.2f}%")
                return False, f"Profit {expected_profit_pct:.2f}% < threshold {dynamic_min_profit:.2f}%"
        
        # Проверка плеча
        req_leverage = scenario.get('leverage', 1.0)
        if req_leverage > metrics.max_leverage:
            # В режиме обучения пропускаем для сбора статистики
            if self.learning_mode:
                logger.debug(f"[RiskManager] {symbol}: Shadow leverage {req_leverage} exceeds limit {metrics.max_leverage:.2f} (allowed for learning)")
                pass
            else:
                logger.debug(f"[RiskManager] {symbol}: REJECTED - Leverage {req_leverage} exceeds limit {metrics.max_leverage:.2f}")
                return False, f"Leverage {req_leverage} exceeds limit {metrics.max_leverage:.2f}"
        
        # Проверка экспозиции
        qty = scenario.get('quantity', 0)
        price = scenario.get('price', card.price)
        notional = qty * price
        
        # Получение баланса через Executor (реальный вызов)
        balance = await self._get_balance_from_executor()
        # Если баланс 0 - значит торгуем на весь доступный, проверка экспозиции не нужна
        if balance > 0 and notional > balance * metrics.exposure_limit:
            logger.debug(f"[RiskManager] {symbol}: REJECTED - Exposure {notional:.2f} exceeds limit {balance * metrics.exposure_limit:.2f}")
            return False, f"Exposure {notional:.2f} exceeds limit {balance * metrics.exposure_limit:.2f}"
        
        # Проверка аварийных флагов (всегда строго, кроме режима обучения для тени)
        if metrics.is_emergency:
            if self.learning_mode:
                logger.debug(f"[RiskManager] {symbol}: Emergency mode but allowed for learning: {metrics.reason}")
                pass
            else:
                logger.warning(f"[RiskManager] {symbol}: REJECTED - Emergency mode: {metrics.reason}")
                return False, f"Emergency mode: {metrics.reason}"
            
        logger.info(f"[RiskManager] {symbol}: ACCEPTED - Profit {expected_profit_pct:.2f}%, RR={scenario.get('risk_reward_ratio', 0):.2f}, Vol={metrics.volatility_index:.4f}")
        return True, "OK"

    async def _shadow_check(self, symbol: str, card: DataCard, metrics: RiskMetrics):
        """
        Теневой расчет: сравнение текущего состояния с идеальным сценарием.
        1. Считает текущий PnL теневой позиции и MAE.
        2. Обновляет уровень стоп-лосса по логике адаптивного трейлинга.
        3. Фиксирует расхождения для обучения AutoTuner.
        """
        if symbol not in self._shadow_scenarios:
            return
            
        shadow = self._shadow_scenarios[symbol]
        
        # 1. Расчет текущего PnL тени
        current_pnl = self._calculate_shadow_pnl(shadow, card.price)
        shadow['current_pnl'] = current_pnl
        shadow['last_price'] = card.price
        
        # --- НОВОЕ: Расчет MAE (Max Adverse Excursion) ---
        entry_price = shadow.get('entry_price', 0)
        side = shadow.get('side', 'LONG')
        if entry_price > 0:
            if side == 'LONG':
                adverse_move = (entry_price - card.price) / entry_price
            else:
                adverse_move = (card.price - entry_price) / entry_price
            
            if adverse_move > 0: # Если движение против нас
                current_mae = adverse_move
                if 'max_adverse_excursion' not in shadow or current_mae > shadow['max_adverse_excursion']:
                    shadow['max_adverse_excursion'] = current_mae
            elif 'max_adverse_excursion' not in shadow:
                shadow['max_adverse_excursion'] = 0.0
        # -----------------------------------------------
        
        # 2. Адаптивный трейлинг-стоп
        new_stop = self._update_trailing_stop(shadow, card.price, metrics.volatility_index)
        if new_stop != shadow['current_stop_loss']:
            old_stop = shadow['current_stop_loss']
            shadow['current_stop_loss'] = new_stop
            print(f"[RiskManager] Shadow Trailing Update {symbol}: Stop {old_stop:.5f} -> {new_stop:.5f} (PnL: {current_pnl:.2f}, MAE: {shadow.get('max_adverse_excursion', 0):.4f})")
            
            # Здесь будет отправка команды Executor на обновление реального стопа, если позиция открыта
            # await self.executor.update_stop(symbol, new_stop) 
        
        # 3. Проверка расхождений (дивергенции)
        ideal_sl = shadow.get('ideal_stop_loss')
        current_sl = shadow['current_stop_loss']
        
        if ideal_sl and current_sl:
            divergence = abs(ideal_sl - current_sl) / card.price if card.price > 0 else 0
            if divergence > 0.005:  # Расхождение > 0.5%
                print(f"[RiskManager] Shadow divergence detected for {symbol}: {divergence:.4f}")
                
        # 4. Проверка на срабатывание стопа в тени
        if self._check_stop_hit(shadow, card.price):
            mae = shadow.get('max_adverse_excursion', 0)
            print(f"[RiskManager] Shadow Stop Hit for {symbol} at {card.price}. PnL: {current_pnl:.2f}, MAE: {mae:.4f}")
            # Логика закрытия тени и отправки статистики в AutoTuner
            self._close_shadow_position(symbol, card.price)

    def _calculate_shadow_pnl(self, shadow: Dict, current_price: float) -> float:
        """
        Расчет PnL для теневой позиции с учетом комиссий и проскальзывания.
        Возвращает прибыль/убыток в валюте котировки (USDT).
        Использует реальные комиссии из конфига + запас для симуляции.
        """
        entry_price = shadow.get('entry_price', 0)
        quantity = shadow.get('quantity', 0)
        leverage = shadow.get('leverage', 1)
        side = shadow.get('side', 'LONG')
        
        if entry_price == 0 or quantity == 0:
            return 0.0
            
        # Расчет сырого PnL
        if side == 'LONG':
            raw_pnl = (current_price - entry_price) * quantity * leverage
        else:
            raw_pnl = (entry_price - current_price) * quantity * leverage
            
        # Учет комиссий (вход + выход) - берем из конфига с запасом
        risk_cfg = self.config.get('risk', {})
        commission_rate = risk_cfg.get('commission_rate', 0.0002)  # 0.02% базовая
        commission_buffer = risk_cfg.get('commission_buffer', 0.0003)  # 0.03% запас
        total_fee_rate = (commission_rate + commission_buffer) * 2  # Вход + выход
        
        notional = entry_price * quantity * leverage
        fees = notional * total_fee_rate
        
        # Проскальзывание - берем из конфига (с запасом для симуляции)
        slippage_buffer = risk_cfg.get('slippage_buffer', 0.0005)  # 0.05%
        slippage = notional * slippage_buffer
        
        return raw_pnl - fees - slippage

    def _update_trailing_stop(self, shadow: Dict, current_price: float, volatility: float) -> float:
        """
        Логика адаптивного трейлинг-стопа.
        Двигает стоп только в направлении прибыли.
        Расстояние до стопа зависит от волатильности (ATR аналог).
        """
        side = shadow.get('side', 'LONG')
        current_stop = shadow.get('current_stop_loss', 0)
        entry_price = shadow.get('entry_price', current_price)
        
        # Динамическое расстояние стопа: база + волатильность * коэффициент
        # Коэффициент можно крутить через AutoTuner (пока хардкод 2.0 для примера)
        trail_multiplier = self.config.get('risk', {}).get('trail_multiplier', 2.0)
        base_distance = entry_price * 0.005 # Базовый отступ 0.5%
        vol_distance = current_price * volatility * trail_multiplier
        
        dynamic_distance = max(base_distance, vol_distance)
        
        new_stop = current_stop
        
        if side == 'LONG':
            # Для лонга стоп двигается только вверх
            potential_stop = current_price - dynamic_distance
            if potential_stop > current_stop:
                new_stop = potential_stop
            # Не ниже цены входа (безубыток), если настроено
            if self.config.get('risk', {}).get('break_even', False):
                if potential_stop > entry_price and current_stop < entry_price:
                    new_stop = entry_price
                    
        elif side == 'SHORT':
            # Для шорта стоп двигается только вниз
            potential_stop = current_price + dynamic_distance
            if potential_stop < current_stop:
                new_stop = potential_stop
            # Безубыток
            if self.config.get('risk', {}).get('break_even', False):
                if potential_stop < entry_price and current_stop > entry_price:
                    new_stop = entry_price
                    
        return new_stop

    def _check_stop_hit(self, shadow: Dict, current_price: float) -> bool:
        """Проверка, достигла ли цена стоп-лосса."""
        side = shadow.get('side', 'LONG')
        stop_loss = shadow.get('current_stop_loss', 0)
        
        if side == 'LONG' and current_price <= stop_loss:
            return True
        if side == 'SHORT' and current_price >= stop_loss:
            return True
        return False

    def _close_shadow_position(self, symbol: str, close_price: float):
        """Закрытие теневой позиции и подготовка данных для AutoTuner."""
        if symbol not in self._shadow_scenarios:
            return
            
        shadow = self._shadow_scenarios[symbol]
        pnl = shadow.get('current_pnl', 0)
        
        # Формируем результат для обучения
        result_data = {
            'symbol': symbol,
            'pnl': pnl,
            'entry_time': shadow.get('timestamp'),
            'exit_time': time.time(),
            'duration': time.time() - shadow.get('timestamp', 0),
            'max_adverse_excursion': shadow.get('max_drawdown', 0), # TODO: считать в цикле
            'strategy_type': shadow.get('strategy_type', 'unknown')
        }
        
        print(f"[RiskManager] Shadow Closed {symbol}. Result: {pnl:.2f} USDT")
        
        # Отправка в ProbabilityField или напрямую в AutoTuner
        # await self.field.add_trade_result(result_data) 
        
        # Удаление из активных теней
        del self._shadow_scenarios[symbol]

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

    async def add_to_shadow_learning(self, symbol: str, scenario: Dict[str, Any], reject_reason: str):
        """
        Добавление отклоненного сценария в систему обучения.
        Сценарий сохраняется для анализа AutoTuner'ом, даже если не прошел фильтры.
        """
        if symbol not in self.shadow_positions:
            self.shadow_positions[symbol] = []
        
        shadow_entry = {
            'scenario': scenario,
            'reject_reason': reject_reason,
            'timestamp': time.time(),
            'entry_price': scenario.get('entry_price', 0),
            'direction': scenario.get('direction', 'LONG'),
            'strategy_type': scenario.get('strategy_type', 'unknown'),
            'confidence': scenario.get('confidence', 0),
            'risk_reward_ratio': scenario.get('risk_reward_ratio', 0),
            'expected_profit_pct': scenario.get('expected_profit_pct', 0),
            'stop_loss': scenario.get('stop_loss', 0),
            'target_price': scenario.get('target_price', 0),
            'max_adverse_excursion': 0.0,
            'current_pnl': 0.0,
            'is_closed': False
        }
        
        self.shadow_positions[symbol].append(shadow_entry)
        logger.debug(f"[RiskManager] Added to shadow learning: {symbol} - {scenario.get('strategy_type')} {scenario.get('direction')} (reason: {reject_reason})")
        
        # Ограничиваем количество теневых записей (последние 100 на символ)
        if len(self.shadow_positions[symbol]) > 100:
            self.shadow_positions[symbol] = self.shadow_positions[symbol][-100:]

    async def _get_balance_from_executor(self) -> float:
        """
        Получение баланса из Executor или конфига.
        Приоритет: Executor > config.risk.trading_balance_usd > 0 (торгуем на весь доступный)
        """
        try:
            # В реальной системе здесь будет вызов к Executor для получения реального баланса
            # executor = self.config.get('executor_instance')
            # if executor:
            #     balance = await executor.get_balance()
            #     return balance if balance > 0 else 0
            
            # Пока берем из конфига risk.trading_balance_usd
            # Если указано 0 - торгуем на весь доступный баланс (будет запрос к API)
            risk_cfg = self.config.get('risk', {})
            trading_balance = risk_cfg.get('trading_balance_usd', 0)
            
            if trading_balance > 0:
                logger.debug(f"[RiskManager] Using configured trading balance: ${trading_balance}")
                return trading_balance
            
            # Если 0 - пытаемся получить реальный баланс (пока заглушка, потом API)
            logger.info("[RiskManager] trading_balance_usd=0, will use full exchange balance (API call needed)")
            # TODO: Вызов API Binance для получения реального баланса
            return 0  # 0 означает "использовать весь доступный"
            
        except Exception as e:
            logger.warning(f"Failed to get balance: {e}, using 0 (full balance mode)")
            return 0  # 0 означает "использовать весь доступный"
