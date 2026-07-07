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
        """Открытие позиции."""
        side = command.get('side', 'LONG')  # LONG или SHORT
        quantity = command.get('quantity', 0)
        leverage = command.get('leverage', 1)
        price = command.get('price', None)  # Для лимитных ордеров
        
        if quantity <= 0:
            print(f"[Executor] Invalid quantity: {quantity}")
            return False
            
        # Установка плеча
        await self._client.futures_change_leverage(symbol=symbol, leverage=leverage)
        
        # Определение типа ордера и стороны
        binance_side = 'BUY' if side == 'LONG' else 'SELL'
        
        # Размещение ордера
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
            
        print(f"[Executor] OPEN {side} {symbol}: qty={quantity}, leverage={leverage}, order_id={order['orderId']}")
        
        # Регистрация позиции
        self._active_positions[symbol] = {
            'side': side,
            'quantity': float(order.get('executedQty', quantity)),
            'entry_price': float(order.get('avgEntryPrice', price or 0)),
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
