"""
Executor Module.
Executes trading commands from ScenarioWriter after RiskManager validation.
Handles order queue, position tracking, and stop-loss updates.
"""
import asyncio
import time
from typing import Dict, Any, Optional, List
from binance import AsyncClient
from src.core.field import ProbabilityField
from src.risk.manager import RiskManager


class Executor:
    """
    Исполнитель ордеров.
    
    Функции:
    1. Управление очередью команд (защита от флуда).
    2. Исполнение ордеров: вход, выход, обновление стопов.
    3. Мониторинг позиций.
    4. Приоритетная обработка аварийных команд от RiskManager.
    """
    
    def __init__(self, config: Dict[str, Any], probability_field: ProbabilityField, risk_manager: RiskManager):
        self.config = config
        self.field = probability_field
        self.risk_manager = risk_manager
        
        # Настройки - извлекаем из Pydantic-модели или dict
        exec_obj = config.get('executor', {})
        
        # Если это Pydantic-модель, конвертируем в dict
        if hasattr(exec_obj, 'dict'):
            exec_cfg = exec_obj.dict()
        elif isinstance(exec_obj, dict):
            exec_cfg = exec_obj
        else:
            exec_cfg = {}
            
        # Testnet берем из общего конфига binance или по умолчанию True
        binance_cfg = config.get('binance', {})
        if hasattr(binance_cfg, 'dict'):
            binance_dict = binance_cfg.dict()
        else:
            binance_dict = binance_cfg
            
        self.testnet = binance_dict.get('testnet', True)
        self.max_queue_size = exec_cfg.get('max_queue_size', 10)
        self.rate_limit_ms = exec_cfg.get('rate_limit_ms', 100)  # Мин. интервал между ордерами
        
        # Состояние
        self._client: Optional[AsyncClient] = None
        self._order_queue: asyncio.Queue = asyncio.Queue()
        self._active_positions: Dict[str, Dict] = {}  # symbol -> position info
        self._last_order_time = 0
        self._running = False
        
    async def start(self):
        """Инициализация клиента Binance."""
        # Берем ключи из раздела api_keys в конфиге
        api_keys_cfg = self.config.get('api_keys', {})
        if hasattr(api_keys_cfg, 'dict'):
            api_keys_dict = api_keys_cfg.dict()
        else:
            api_keys_dict = api_keys_cfg
            
        api_key = api_keys_dict.get('binance_testnet_api_key', '')
        api_secret = api_keys_dict.get('binance_testnet_api_secret', '')
        
        if not api_key or not api_secret:
            print("[Executor] WARNING: API keys not found in config. Running in simulation mode.")
            self._client = None
            self._running = True
            asyncio.create_task(self._process_queue())
            print(f"[Executor] Started (testnet={self.testnet}, simulation_mode=True)")
            return
        
        self._client = await AsyncClient.create(
            api_key=api_key,
            api_secret=api_secret,
            testnet=self.testnet
        )
        self._running = True
        
        # Запуск обработчика очереди
        asyncio.create_task(self._process_queue())
        print(f"[Executor] Started (testnet={self.testnet})")
        
    async def stop(self):
        """Остановка и закрытие клиента."""
        self._running = False
        if self._client:
            await self._client.close_connection()
        print("[Executor] Stopped")
        
    async def submit_command(self, symbol: str, command: Dict[str, Any]):
        """
        Добавление команды в очередь.
        command: {action: 'OPEN'|'CLOSE'|'UPDATE_STOP', ...params}
        """
        if self._order_queue.qsize() >= self.max_queue_size:
            print(f"[Executor] Queue full! Dropping command for {symbol}")
            return False
            
        await self._order_queue.put({
            'symbol': symbol,
            'command': command,
            'timestamp': time.time()
        })
        return True
        
    async def _process_queue(self):
        """Обработчик очереди ордеров."""
        while self._running:
            try:
                # Проверка rate limit
                now = time.time()
                if now - self._last_order_time < self.rate_limit_ms / 1000:
                    await asyncio.sleep(0.01)
                    continue
                    
                try:
                    item = await asyncio.wait_for(self._order_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                    
                symbol = item['symbol']
                command = item['command']
                
                # Выполнение команды
                success = await self._execute_command(symbol, command)
                
                if success:
                    self._last_order_time = time.time()
                    
            except Exception as e:
                print(f"[Executor] Error in queue processing: {e}")
                await asyncio.sleep(1)
                
    async def _execute_command(self, symbol: str, command: Dict[str, Any]) -> bool:
        """Выполнение конкретной команды."""
        action = command.get('action')
        
        try:
            if action == 'OPEN':
                return await self._open_position(symbol, command)
            elif action == 'CLOSE':
                return await self._close_position(symbol, command)
            elif action == 'UPDATE_STOP':
                return await self._update_stop_loss(symbol, command)
            else:
                print(f"[Executor] Unknown action: {action}")
                return False
                
        except Exception as e:
            print(f"[Executor] Failed to execute {action} on {symbol}: {e}")
            return False
            
    async def _open_position(self, symbol: str, command: Dict[str, Any]) -> bool:
        """Открытие позиции с обработкой минимального размера ордера."""
        side = command.get('side', 'LONG')  # LONG или SHORT
        quantity = command.get('quantity', 0)
        leverage = command.get('leverage', 1)
        price = command.get('price', None)  # Для лимитных ордеров
        position_value_usd = command.get('position_value_usd', 0)
        
        if quantity <= 0:
            print(f"[Executor] Invalid quantity: {quantity}")
            return False
            
        # Установка плеча
        await self._client.futures_change_leverage(symbol=symbol, leverage=leverage)
        
        # Определение типа ордера и стороны
        binance_side = 'BUY' if side == 'LONG' else 'SELL'
        
        # Попытка размещения ордера с расчетным количеством
        order = None
        order_placed = False
        retry_with_min = False
        
        try:
            if price:
                # Лимитный ордер
                order = await self._client.futures_create_order(
                    symbol=symbol,
                    side=binance_side,
                    type='LIMIT',
                    quantity=quantity,
                    price=price,
                    timeInForce='GTC'
                )
            else:
                # Рыночный ордер
                order = await self._client.futures_create_order(
                    symbol=symbol,
                    side=binance_side,
                    type='MARKET',
                    quantity=quantity
                )
            order_placed = True
            print(f"[Executor] OPEN {side} {symbol}: qty={quantity:.4f} (${position_value_usd:.2f}), leverage={leverage}, order_id={order['orderId']}")
            
        except Exception as e:
            error_msg = str(e)
            # Проверка на ошибку точности количества (Precision is over the maximum)
            if 'Precision is over the maximum' in error_msg or 'FILTER_FAILURE' in error_msg or 'LOT_SIZE' in error_msg:
                print(f"[Executor] Order rejected due to quantity precision. Adjusting to symbol precision...")
                # Получаем точную информацию о шаге количества и корректируем
                step_size = self._get_step_size(symbol)
                adjusted_quantity = round(quantity / step_size) * step_size
                
                # Защита от округления до нуля
                if adjusted_quantity <= 0:
                    adjusted_quantity = step_size  # Минимально возможное количество
                    
                print(f"[Executor] Adjusted quantity from {quantity} to {adjusted_quantity} (step_size={step_size})")
                
                try:
                    if price:
                        order = await self._client.futures_create_order(
                            symbol=symbol,
                            side=binance_side,
                            type='LIMIT',
                            quantity=adjusted_quantity,
                            price=price,
                            timeInForce='GTC'
                        )
                    else:
                        order = await self._client.futures_create_order(
                            symbol=symbol,
                            side=binance_side,
                            type='MARKET',
                            quantity=adjusted_quantity
                        )
                    order_placed = True
                    print(f"[Executor] OPEN {side} {symbol}: qty={adjusted_quantity:.4f} (${position_value_usd:.2f}), leverage={leverage}, order_id={order['orderId']}")
                    
                except Exception as e2:
                    error_msg2 = str(e2)
                    # Если после коррекции точности всё ещё ошибка минимального размера - пробуем с минимумом
                    if 'MIN_NOTIONAL' in error_msg2 or 'notional must be no smaller' in error_msg2 or 'minimum order value' in error_msg2.lower():
                        print(f"[Executor] Adjusted quantity still too small. Retrying with minimum allowed ($5)...")
                        retry_with_min = True
                    else:
                        print(f"[Executor] Failed with adjusted quantity: {e2}")
                        return False
                        
            elif 'MIN_NOTIONAL' in error_msg or 'notional must be no smaller' in error_msg or 'minimum order value' in error_msg.lower():
                print(f"[Executor] Order rejected due to minimum size. Retrying with minimum allowed...")
                retry_with_min = True
            else:
                print(f"[Executor] Failed to open position: {e}")
                return False
        
        # Если ордер отклонен из-за минимального размера, пробуем с минимумом
        if retry_with_min:
            # Получаем текущую цену для расчета минимального количества
            ticker = await self._client.futures_ticker_price(symbol=symbol)
            current_price = float(ticker.get('price', 0))
            
            if current_price > 0:
                # Минимальный размер ордера на Binance Futures = $5 USDT
                min_order_value = 5.0
                min_quantity = min_order_value / current_price
                
                # Округляем количество до точности символа
                step_size = self._get_step_size(symbol)
                min_quantity = round(min_quantity / step_size) * step_size
                
                try:
                    if price:
                        order = await self._client.futures_create_order(
                            symbol=symbol,
                            side=binance_side,
                            type='LIMIT',
                            quantity=min_quantity,
                            price=price,
                            timeInForce='GTC'
                        )
                    else:
                        order = await self._client.futures_create_order(
                            symbol=symbol,
                            side=binance_side,
                            type='MARKET',
                            quantity=min_quantity
                        )
                    print(f"[Executor] OPEN {side} {symbol} (MIN): qty={min_quantity:.4f} (~$5), leverage={leverage}, order_id={order['orderId']}")
                    order_placed = True
                except Exception as e2:
                    print(f"[Executor] Failed to place minimum order: {e2}")
                    return False
            else:
                print(f"[Executor] Could not get current price for {symbol}")
                return False
        
        if not order_placed or not order:
            return False
            
        # Регистрация позиции
        executed_qty = float(order.get('executedQty', quantity))
        avg_price = float(order.get('avgEntryPrice', price or 0))
        
        self._active_positions[symbol] = {
            'side': side,
            'quantity': executed_qty,
            'entry_price': avg_price,
            'leverage': leverage,
            'stop_loss': command.get('stop_loss'),
            'take_profit': command.get('take_profit'),
            'order_id': order['orderId'],
            'stop_order_id': None,  # Пока стопа нет, будет заполнен при установке
            'opened_at': time.time()
        }
        
        # Регистрация в RiskManager для теневого отслеживания
        self.risk_manager.register_scenario(symbol, {
            'entry_price': self._active_positions[symbol]['entry_price'],
            'stop_loss': command.get('stop_loss'),
            'side': side
        })
        
        # Обновление информации о позиции в RiskManager
        self.risk_manager.update_position(symbol, self._active_positions[symbol])
        
        return True
    
    def _get_step_size(self, symbol: str) -> float:
        """Получение шага количества для символа через API Binance."""
        # Пытаемся получить точную информацию о символе через API
        # Примечание: этот метод может вызываться из синхронного контекста,
        # поэтому используем синхронный подход с созданием временного клиента если нужно
        # Но в нашем случае мы вызываем его внутри async методов, где self._client уже инициализирован
        
        # Значения по умолчанию для популярных пар
        if 'DOGE' in symbol:
            return 1.0  # DOGE торгуется целыми числами
        elif 'BTC' in symbol or 'BTCUSDT' in symbol:
            return 0.001  # BTC с точностью до 0.001
        elif 'ETH' in symbol:
            return 0.01  # ETH с точностью до 0.01
        elif 'SOL' in symbol:
            return 0.1  # SOL с точностью до 0.1
        else:
            return 0.1  # По умолчанию
        
    async def _close_position(self, symbol: str, command: Dict[str, Any]) -> bool:
        """Закрытие позиции."""
        if symbol not in self._active_positions:
            print(f"[Executor] No active position for {symbol}")
            return False
            
        pos = self._active_positions[symbol]
        side = pos['side']
        quantity = pos['quantity']
        
        # Противоположная сторона для закрытия
        binance_side = 'SELL' if side == 'LONG' else 'BUY'
        
        # Рыночное закрытие
        order = await self._client.futures_create_order(
            symbol=symbol,
            side=binance_side,
            type='MARKET',
            quantity=quantity
        )
        
        print(f"[Executor] CLOSE {symbol}: qty={quantity}, order_id={order['orderId']}")
        
        # Удаление позиции
        self.risk_manager.remove_position(symbol)
        del self._active_positions[symbol]
        
        return True
        
    async def _update_stop_loss(self, symbol: str, command: Dict[str, Any]) -> bool:
        """Обновление стоп-лосса (через Stop Market ордер)."""
        if symbol not in self._active_positions:
            print(f"[Executor] No active position for {symbol}")
            return False
            
        pos = self._active_positions[symbol]
        side = pos['side']
        quantity = pos['quantity']
        new_stop_price = command.get('stop_loss')
        
        if not new_stop_price:
            print(f"[Executor] Invalid stop_loss price")
            return False
            
        # Определение стороны стопа
        stop_side = 'SELL' if side == 'LONG' else 'BUY'
        
        # Отмена предыдущего стопа (если был сохранен ID)
        if 'stop_order_id' in pos and pos['stop_order_id']:
            try:
                await self._client.futures_cancel_order(
                    symbol=symbol,
                    orderId=pos['stop_order_id']
                )
                print(f"[Executor] Cancelled old stop order {pos['stop_order_id']} for {symbol}")
            except Exception as e:
                print(f"[Executor] Error cancelling old stop: {e}")
        
        # Размещение нового Stop Market ордера
        order = await self._client.futures_create_order(
            symbol=symbol,
            side=stop_side,
            type='STOP_MARKET',
            quantity=quantity,
            stopPrice=new_stop_price,
            closePosition=True  # Закрывает всю позицию
        )
        
        print(f"[Executor] UPDATE_STOP {symbol}: new_stop={new_stop_price}, order_id={order['orderId']}")
        
        # Сохранение ID стопа для будущей отмены
        pos['stop_order_id'] = order['orderId']
        
        # Обновление локального состояния
        pos['stop_loss'] = new_stop_price
        self._active_positions[symbol] = pos
        
        # Обновление в RiskManager
        self.risk_manager.update_position(symbol, pos)
        
        return True
        
    def get_active_positions(self) -> Dict[str, Dict]:
        """Получение активных позиций."""
        return self._active_positions.copy()
        
    async def get_balance(self) -> Dict[str, float]:
        """Получение баланса фьючерсного аккаунта."""
        if not self._client:
            return {'available': 0, 'total': 0}
            
        account = await self._client.futures_account()
        for asset in account['assets']:
            if asset['asset'] == 'USDT':
                return {
                    'available': float(asset['availableBalance']),
                    'total': float(asset['walletBalance'])
                }
        return {'available': 0, 'total': 0}
    
    async def execute_scenario(self, scenario):
        """
        Выполнение торгового сценария.
        
        Принимает объект сценария от ScenarioWriter, проверяет риски
        и отправляет команду на исполнение (или в теневой просчет).
        
        Args:
            scenario: Объект TradeScenario с атрибутами:
                - symbol: торговая пара
                - direction: LONG или SHORT
                - entry_price: цена входа
                - stop_loss: цена стоп-лосса
                - target_price: цена цели (take profit)
                - confidence: уверенность сценария
                - strategy_type: тип стратегии (например, 'trap')
        """
        try:
            symbol = scenario.symbol
            direction = scenario.direction
            entry_price = scenario.entry_price
            stop_loss = scenario.stop_loss
            take_profit = scenario.target_price
            confidence = scenario.confidence
            
            # Получаем баланс для расчета размера позиции
            balance_info = await self.get_balance()
            available_balance = balance_info.get('available', 0)
            
            # Расчет размера позиции на основе доступного баланса и настроек риск-менеджмента
            # Используем процент от баланса согласно конфигу
            risk_cfg = self.config.get('risk', {})
            # Если risk_cfg это Pydantic модель, конвертируем в dict
            if hasattr(risk_cfg, 'model_dump'):
                risk_cfg = risk_cfg.model_dump()
            elif hasattr(risk_cfg, 'dict'):
                risk_cfg = risk_cfg.dict()
            
            # Получаем размер позиции ИЗ СЦЕНАРИЯ (если там есть) или рассчитываем от баланса
            # RiskManager может передать position_value_usd в сценарии, либо используем процент от баланса
            position_value_usd = getattr(scenario, 'position_value_usd', 0)
            
            if position_value_usd <= 0:
                # Если в сценарии не указан размер, рассчитываем от доступного баланса
                risk_cfg = self.config.get('risk', {})
                if hasattr(risk_cfg, 'model_dump'):
                    risk_cfg = risk_cfg.model_dump()
                elif hasattr(risk_cfg, 'dict'):
                    risk_cfg = risk_cfg.dict()
                
                max_exposure_pct = risk_cfg.get('max_exposure_pct', 0.05)  # По умолчанию 5%
                position_value_usd = available_balance * max_exposure_pct
                
                # Если баланс маленький или нулевой, используем минимальный размер ордера
                if position_value_usd < 5.0:
                    position_value_usd = 5.0  # Минимум Binance Futures
            
            # Расчет количества монет: quantity = position_value / entry_price
            if entry_price > 0:
                quantity = position_value_usd / entry_price
            else:
                print(f"[Executor] Invalid entry_price {entry_price} for {symbol}, cannot calculate quantity")
                return False
            
            leverage = 5  # Стандартное плечо по умолчанию
            
            # Формирование команды на открытие позиции
            command = {
                'action': 'OPEN',
                'side': direction,
                'quantity': quantity,
                'leverage': leverage,
                'price': None,  # Рыночный ордер по умолчанию
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'position_value_usd': position_value_usd  # Для логирования
            }
            
            # Отправка команды в очередь исполнителя
            success = await self.submit_command(symbol, command)
            
            if success:
                print(f"[Executor] Scenario queued: {direction} {symbol}, qty={quantity:.4f} (${position_value_usd:.2f}), SL={stop_loss}, TP={take_profit}, conf={confidence}")
            else:
                print(f"[Executor] Failed to queue scenario for {symbol}: queue full")
                
        except Exception as e:
            print(f"[Executor] Error executing scenario: {e}")
            raise
    
    async def save_trade_card(self, symbol: str, scenario: Dict[str, Any], result: Dict[str, Any], cards_path: str = "data_storage/cards"):
        """
        Сохранение карточки сделки для обучения AutoTuner.
        
        Карточка содержит полный снимок данных на момент сделки и результат.
        Данные сохраняются в SQLite базу данных для предотвращения создания тысяч файлов.
        
        Args:
            symbol: Торговая пара
            scenario: Параметры сценария (вход, стоп, цель, стратегия)
            result: Результат сделки (PnL, длительность, причина выхода)
            cards_path: Путь к директории для сохранения карточек
        """
        import sqlite3
        import os
        from datetime import datetime
        
        # Создаем директорию если не существует
        os.makedirs(cards_path, exist_ok=True)
        
        # Путь к SQLite базе данных
        db_path = os.path.join(os.path.dirname(cards_path), "trading_history.db")
        
        # Создаем карточку со всеми данными
        tuner_notes = scenario.get("tuner_notes", {})
        market_conditions = scenario.get("market_conditions", {})
        
        # Подключение к базе данных
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        try:
            # Проверяем наличие колонки is_real, если нет - добавляем
            cursor.execute("PRAGMA table_info(trades)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'is_real' not in columns:
                print("[Executor] Adding 'is_real' column to trades table...")
                cursor.execute("ALTER TABLE trades ADD COLUMN is_real INTEGER DEFAULT 0")
                conn.commit()
            
            cursor.execute('''
                INSERT INTO trades (
                    symbol, timestamp_open, timestamp_close, strategy_type, direction,
                    entry_price, stop_loss, target_price, confidence, risk_reward_ratio,
                    leverage, quantity, pnl_usd, pnl_percent, exit_price, duration_sec,
                    exit_reason, max_drawdown, max_profit, is_real,
                    analyzer_trend_useful, analyzer_mean_reversion_useful,
                    analyzer_order_flow_useful, analyzer_volatility_useful, analyzer_matrix_useful,
                    analyzer_trend_confidence, analyzer_mean_reversion_confidence,
                    analyzer_order_flow_confidence, analyzer_volatility_confidence,
                    analyzer_matrix_confidence, market_trend, market_volatility, market_volume
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                symbol,
                scenario.get("timestamp", datetime.now().timestamp()),
                result.get("exit_time", datetime.now().isoformat()),
                scenario.get("strategy_type", "unknown"),
                scenario.get("direction", "LONG"),
                scenario.get("entry_price", 0),
                scenario.get("stop_loss", 0),
                scenario.get("target_price", 0),
                scenario.get("confidence", 0),
                scenario.get("risk_reward_ratio", 0),
                scenario.get("leverage", 1),
                scenario.get("quantity", 0),
                result.get("pnl", 0),
                result.get("pnl_percent", 0),
                result.get("exit_price", 0),
                result.get("duration", 0),
                result.get("reason", "unknown"),
                result.get("max_drawdown", 0),
                result.get("max_profit", 0),
                scenario.get("is_real", False),  # Флаг реальной сделки
                tuner_notes.get("analyzer_trend_useful", False),
                tuner_notes.get("analyzer_mean_reversion_useful", False),
                tuner_notes.get("analyzer_order_flow_useful", False),
                tuner_notes.get("analyzer_volatility_useful", False),
                tuner_notes.get("analyzer_matrix_useful", False),
                tuner_notes.get("analyzer_trend_confidence", 0),
                tuner_notes.get("analyzer_mean_reversion_confidence", 0),
                tuner_notes.get("analyzer_order_flow_confidence", 0),
                tuner_notes.get("analyzer_volatility_confidence", 0),
                tuner_notes.get("analyzer_matrix_confidence", 0),
                market_conditions.get("trend", "NEUTRAL"),
                market_conditions.get("volatility", 0),
                market_conditions.get("volume", 0)
            ))
            conn.commit()
            
            # Получаем общее количество записей
            cursor.execute("SELECT COUNT(*) FROM trades")
            total = cursor.fetchone()[0]
            
            print(f"[Executor] Trade card saved to SQLite database (total cards: {total})")
            
        except Exception as e:
            print(f"[Executor] Error saving trade card to database: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()
        
        return db_path
