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
        
        # Чтение настроек риска ИЗ TUNABLE_PARAMS (где AutoTuner хранит обновления)
        # Fallback на старый формат если TUNABLE_PARAMS отсутствует
        if isinstance(self.config, dict):
            tunable_params = self.config.get('TUNABLE_PARAMS', {})
            risk_cfg = tunable_params.get('risk', {})
            # Если в TUNABLE_PARAMS нет risk, пробуем старый путь
            if not risk_cfg:
                risk_cfg = self.config.get('risk', {})
        else:
            # Pydantic модель
            tunable_params = getattr(self.config, 'TUNABLE_PARAMS', None)
            if tunable_params:
                risk_cfg = getattr(tunable_params, 'risk', {})
                if hasattr(risk_cfg, 'model_dump'):
                    risk_cfg = risk_cfg.model_dump()
                elif hasattr(risk_cfg, 'dict'):
                    risk_cfg = risk_cfg.dict()
            else:
                risk_cfg = getattr(self.config, 'risk', {})
                if hasattr(risk_cfg, 'model_dump'):
                    risk_cfg = risk_cfg.model_dump()
                elif hasattr(risk_cfg, 'dict'):
                    risk_cfg = risk_cfg.dict()
        
        self.min_leverage = risk_cfg.get('min_leverage', 1.0)
        self.max_leverage_default = risk_cfg.get('max_leverage', 5.0)
        
        # Динамический лимит экспозиции: max_position_size_pct от trading_balance_usd
        # По умолчанию 66% (2/3) от выделенного баланса
        self.max_position_size_pct = risk_cfg.get('max_position_size_pct', 0.66)
        self.trading_balance_usd = risk_cfg.get('trading_balance_usd', 50.0)
        
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
        
        # 5. Лимит экспозиции: max_position_size_pct ОТ trading_balance_usd (выделенного баланса)
        # Не от полного баланса биржи! Снижаем лимит при плохой ликвидности для защиты капитала
        exposure_limit = self.trading_balance_usd * self.max_position_size_pct * (1.0 - liq_risk)
        
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

    async def validate_scenario(self, symbol: str, scenario: Dict[str, Any], current_usage: float, is_shadow: bool = False) -> Dict[str, Any]:
        """
        Валидация сценария на соответствие риск-лимитам баланса.
        
        Логика:
        1. Базовый лимит занятости депозита: 66%.
        2. Исключение: Если уверенность (confidence) > 90%, лимит повышается до 75%.
        3. Если занятость >= лимит -> Отклонить для Реала, отправить в Тень.
        
        Args:
            symbol: Символ пары.
            scenario: Словарь с данными сценария (entry_price, confidence и т.д.).
            current_usage: Текущая занятость баланса в долях (0.0 - 1.0).
            is_shadow: Флаг, является ли сделка теневой (для логирования).
            
        Returns:
            Dict с ключами: 'approved' (bool), 'is_shadow' (bool), 'reason' (str).
        """
        confidence = scenario.get('confidence', 0.0)
        
        # Чтение лимитов из конфига (с дефолтами)
        # Приоритет: TUNABLE_PARAMS['risk'] (где AutoTuner хранит обновления) -> старый формат risk
        if isinstance(self.config, dict):
            tunable_params = self.config.get('TUNABLE_PARAMS', {})
            risk_cfg = tunable_params.get('risk', {})
            if not risk_cfg:
                risk_cfg = self.config.get('risk', {})
        else:
            tunable_params = getattr(self.config, 'TUNABLE_PARAMS', None)
            if tunable_params:
                risk_cfg = getattr(tunable_params, 'risk', {})
                if hasattr(risk_cfg, 'model_dump'):
                    risk_cfg = risk_cfg.model_dump()
                elif hasattr(risk_cfg, 'dict'):
                    risk_cfg = risk_cfg.dict()
            else:
                risk_cfg = getattr(self.config, 'risk', {})
                if hasattr(risk_cfg, 'model_dump'):
                    risk_cfg = risk_cfg.model_dump()
                elif hasattr(risk_cfg, 'dict'):
                    risk_cfg = risk_cfg.dict()

        base_limit = risk_cfg.get('max_position_size_pct', 0.66)
        high_limit = risk_cfg.get('high_confidence_max_pct', 0.75)
        conf_threshold = risk_cfg.get('high_confidence_threshold', 0.90)
        
        # Определение актуального лимита
        if confidence > conf_threshold:
            limit = high_limit
            limit_type = "HIGH_CONF"
        else:
            limit = base_limit
            limit_type = "BASE"
            
        # Логика маршрутизации
        if current_usage >= limit:
            # Превышен лимит -> Отправляем в ТЕНЬ
            reason = f'Balance usage {current_usage:.2f} >= limit {limit:.2f} ({limit_type}, Conf: {confidence:.2f})'
            logger.warning(f"[RiskManager] {symbol}: Scenario rejected for REAL. Reason: {reason}. Sending to SHADOW.")
            return {
                'approved': False,
                'is_shadow': True,
                'reason': reason
            }
            
        logger.debug(f"[RiskManager] {symbol}: Scenario approved for REAL. Usage {current_usage:.2f} < limit {limit:.2f} ({limit_type}).")
        return {'approved': True, 'is_shadow': False}
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
        Трёхуровневый адаптивный трейлинг-стоп.
        
        УРОВЕНЬ 1: Активация (Безубыток) - при достижении activation_pct
        УРОВЕНЬ 2: Подтягивание (Trailing Step) - динамическое расстояние на основе волатильности
        УРОВЕНЬ 3: Замок прибыли (Profit Lock) - уплотнение стопа при сильном движении
        
        :param shadow: Данные теневой позиции
        :param current_price: Текущая цена
        :param volatility: Индекс волатильности
        :return: Новый уровень стоп-лосса
        """
        side = shadow.get('side', 'LONG')
        current_stop = shadow.get('current_stop_loss', 0)
        entry_price = shadow.get('entry_price', current_price)
        
        # Расчет текущего PnL в процентах
        if side == 'LONG':
            pnl_pct = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
        else:
            pnl_pct = (entry_price - current_price) / entry_price * 100 if entry_price > 0 else 0
        
        # Получаем настройки из конфига (с дефолтами)
        trailing_cfg = self.config.get('TUNABLE_PARAMS', {}).get('trailing_stop', {})
        if not trailing_cfg:
            # Fallback на старый формат конфига
            trailing_cfg = self.config.get('risk', {}).get('trailing_stop', {})
        
        # === НАСТРОЙКИ УРОВНЯ 1: АКТИВАЦИЯ ===
        activation_pct = trailing_cfg.get('activation_pct', 0.5)  # % движения для активации
        breakeven_trigger_pct = trailing_cfg.get('breakeven_trigger_pct', 0.3)  # % от входа для безубытка
        
        # === НАСТРОЙКИ УРОВНЯ 2: ПОДТЯГИВАНИЕ ===
        trail_step_pct = trailing_cfg.get('trail_step_pct', 0.2)  # Шаг подтягивания в %
        trail_distance_atr_mult = trailing_cfg.get('trail_distance_atr_mult', 1.5)  # Множитель волатильности
        
        # === НАСТРОЙКИ УРОВНЯ 3: ЗАМОК ПРИБЫЛИ ===
        profit_lock_trigger_pct = trailing_cfg.get('profit_lock_trigger_pct', 2.0)  # % для замка прибыли
        tight_stop_atr_mult = trailing_cfg.get('tight_stop_atr_mult', 1.0)  # Уплотненный множитель
        
        new_stop = current_stop
        
        # =========================================================================
        # === УРОВЕНЬ 1: АКТИВАЦИЯ (Безубыток) ===
        # =========================================================================
        if pnl_pct >= activation_pct:
            # Активируем трейлинг - двигаем стоп в безубыток
            if side == 'LONG':
                breakeven_stop = entry_price * (1 + breakeven_trigger_pct / 100)
                if current_stop < breakeven_stop:
                    new_stop = breakeven_stop
                    logger.debug(f"[Trail L1] {shadow.get('symbol', 'UNK')}: Activated breakeven at {breakeven_stop:.5f} (PnL={pnl_pct:.2f}%)")
            else:  # SHORT
                breakeven_stop = entry_price * (1 - breakeven_trigger_pct / 100)
                if current_stop == 0 or current_stop > breakeven_stop:
                    new_stop = breakeven_stop
                    logger.debug(f"[Trail L1] {shadow.get('symbol', 'UNK')}: Activated breakeven at {breakeven_stop:.5f} (PnL={pnl_pct:.2f}%)")
        
        # =========================================================================
        # === УРОВЕНЬ 3: ЗАМОК ПРИБЫЛИ (Проверяем ДО уровня 2) ===
        # =========================================================================
        if pnl_pct >= profit_lock_trigger_pct:
            # Сильное движение в нашу пользу - уплотняем стоп для защиты прибыли
            tight_distance = current_price * volatility * tight_stop_atr_mult
            
            if side == 'LONG':
                potential_stop = current_price - tight_distance
                # Двигаем только вверх
                if potential_stop > new_stop:
                    new_stop = potential_stop
                    logger.debug(f"[Trail L3] {shadow.get('symbol', 'UNK')}: Profit lock activated at {new_stop:.5f} (PnL={pnl_pct:.2f}%, tight_dist={tight_distance:.5f})")
            else:  # SHORT
                potential_stop = current_price + tight_distance
                if new_stop == 0 or potential_stop < new_stop:
                    new_stop = potential_stop
                    logger.debug(f"[Trail L3] {shadow.get('symbol', 'UNK')}: Profit lock activated at {new_stop:.5f} (PnL={pnl_pct:.2f}%)")
            
            return new_stop  # Возвращаем сразу, не применяя уровень 2
        
        # =========================================================================
        # === УРОВЕНЬ 2: ПОДТЯГИВАНИЕ (Trailing Step) ===
        # =========================================================================
        # Динамическое расстояние на основе волатильности
        base_distance = entry_price * 0.005  # Базовый отступ 0.5%
        vol_distance = current_price * volatility * trail_distance_atr_mult
        dynamic_distance = max(base_distance, vol_distance)
        
        if side == 'LONG':
            potential_stop = current_price - dynamic_distance
            # Двигаем стоп только вверх
            if potential_stop > new_stop:
                # Округляем до шага трейлинга
                step_size = entry_price * trail_step_pct / 100
                if step_size > 0:
                    stepped_stop = round(potential_stop / step_size) * step_size
                    new_stop = max(stepped_stop, new_stop)
                else:
                    new_stop = potential_stop
                    
                logger.debug(f"[Trail L2] {shadow.get('symbol', 'UNK')}: Trailing up to {new_stop:.5f} (dist={dynamic_distance:.5f}, step={step_size:.5f})")
                
        elif side == 'SHORT':
            potential_stop = current_price + dynamic_distance
            # Двигаем стоп только вниз
            if new_stop == 0 or potential_stop < new_stop:
                step_size = entry_price * trail_step_pct / 100
                if step_size > 0:
                    stepped_stop = round(potential_stop / step_size) * step_size
                    new_stop = min(stepped_stop, new_stop) if new_stop > 0 else stepped_stop
                else:
                    new_stop = potential_stop
                    
                logger.debug(f"[Trail L2] {shadow.get('symbol', 'UNK')}: Trailing down to {new_stop:.5f}")
        
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
            'max_adverse_excursion': shadow.get('max_adverse_excursion', 0.0),
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

    async def add_to_shadow_learning(self, symbol: str, scenario: Dict[str, Any], reject_reason: str, shadow_trade=None):
        """
        Добавление отклоненного сценария в систему обучения.
        Сценарий сохраняется для анализа AutoTuner, даже если не прошел фильтры.
        ВАЖНО: В режиме обучения все сценарии идут в тень для сбора полной статистики.
        
        Args:
            symbol: Торговая пара
            scenario: Параметры сценария
            reject_reason: Причина отклонения или "accepted_real_trade"
            shadow_trade: Объект ShadowTrade из ShadowDealer (опционально)
        """
        if symbol not in self.shadow_positions:
            self.shadow_positions[symbol] = []
        
        # В режиме обучения добавляем ВСЕ сценарии в тень, даже принятые
        # Это нужно для сбора полной статистики по всем возможным исходам
        shadow_entry = {
            'scenario': scenario,
            'reject_reason': reject_reason,  # Причина отклонения или "learning_mode"
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
            'is_closed': False,
            'is_learning_mode': self.learning_mode,
            'shadow_trade_id': getattr(shadow_trade, 'id', None) if shadow_trade else None
        }
        
        self.shadow_positions[symbol].append(shadow_entry)
        logger.debug(f"[RiskManager] Added to shadow learning: {symbol} - {scenario.get('strategy_type')} {scenario.get('direction')} (reason: {reject_reason}, learning={self.learning_mode})")
        
        # Ограничиваем количество теневых записей (последние 100 на символ)
        if len(self.shadow_positions[symbol]) > 100:
            self.shadow_positions[symbol] = self.shadow_positions[symbol][-100:]

    async def _get_balance_from_executor(self) -> float:
        """
        Получение баланса для торговли.
        
        ВАЖНО: Для расчёта размера позиций используется ТОЛЬКО trading_balance_usd из конфига.
        Реальный баланс с Binance используется только для валидации (контроль, что не ушли в минус).
        
        Приоритет: config.risk.trading_balance_usd > fallback на весь доступный (если = 0)
        """
        try:
            # Берем из конфига risk.trading_balance_usd - это ОСНОВНОЙ баланс для торговли
            risk_cfg = self.config.get('risk', {})
            trading_balance = risk_cfg.get('trading_balance_usd', 0)
            
            if trading_balance > 0:
                logger.debug(f"[RiskManager] Using configured trading balance: ${trading_balance}")
                
                # Валидация: проверяем реальный баланс на Binance, чтобы убедиться что хватает средств
                executor = self.config.get('executor_instance')
                if executor and hasattr(executor, 'get_balance'):
                    real_balance = await executor.get_balance()
                    if real_balance < trading_balance:
                        logger.warning(
                            f"[RiskManager] Configured trading balance (${trading_balance}) "
                            f"exceeds real balance (${real_balance}). Using real balance."
                        )
                        return real_balance
                
                return trading_balance
            
            # Если trading_balance_usd = 0 - торгуем на весь доступный баланс (режим full balance)
            logger.info("[RiskManager] trading_balance_usd=0, fetching real balance from Binance API")
            executor = self.config.get('executor_instance')
            if executor and hasattr(executor, 'get_balance'):
                balance = await executor.get_balance()
                if balance > 0:
                    logger.debug(f"[RiskManager] Using real balance from Executor: ${balance}")
                    return balance
            
            # Fallback через feed
            feed_instance = self.config.get('feed_instance')
            if feed_instance and hasattr(feed_instance, 'get_account_balance'):
                balance_data = await feed_instance.get_account_balance()
                if balance_data and 'USDT' in balance_data:
                    usdt_balance = float(balance_data['USDT'].get('availableBalance', 0))
                    logger.debug(f"[RiskManager] Real USDT balance from API: ${usdt_balance}")
                    return usdt_balance
            
            logger.warning("[RiskManager] Could not get balance, using 0 (full balance mode)")
            return 0  # 0 означает "использовать весь доступный"
            
        except Exception as e:
            logger.warning(f"Failed to get balance: {e}, using 0 (full balance mode)")
            return 0  # 0 означает "использовать весь доступный"
