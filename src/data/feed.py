"""
Data Feed Module: Binance Futures Connector
Использует python-binance (AsyncClient) для REST и aiohttp для WebSocket.
Заменяет ccxt и ThreadedWebsocketManager для избежания конфликтов циклов.
"""
import asyncio
import logging
import json
import aiohttp
from typing import Dict, List, Optional, Callable, Any
from binance import AsyncClient
from datetime import datetime

logger = logging.getLogger(__name__)


class BinanceFuturesFeed:
    """
    Асинхронный коннектор к Binance Futures.
    Предоставляет:
    - REST API для получения стакана и исторических данных
    - WebSocket для потоковых обновлений (стакан, сделки, тикеры)
    """
    
    def __init__(self, config: Any):
        # Конвертируем Pydantic модель в dict для совместимости
        if hasattr(config, 'model_dump'):
            self.config = config.model_dump()
        else:
            self.config = config
            
        # Обработка api_keys с учётом возможного объекта ApiKeysConfig
        if isinstance(self.config, dict):
            api_keys = self.config.get("api_keys", {})
            if hasattr(api_keys, 'model_dump'):
                api_keys = api_keys.model_dump()
            self.api_key = api_keys.get("binance_testnet_api_key", "") if isinstance(api_keys, dict) else ""
            self.api_secret = api_keys.get("binance_testnet_api_secret", "") if isinstance(api_keys, dict) else ""
            
            # Символы для отслеживания
            data_cfg = self.config.get("data", {})
            self.symbols = data_cfg.get("symbols", ["DOGEUSDT", "BTCUSDT"]) if isinstance(data_cfg, dict) else getattr(data_cfg, 'symbols', ["DOGEUSDT", "BTCUSDT"])
            self.primary_symbol = data_cfg.get("primary_symbol", "DOGEUSDT") if isinstance(data_cfg, dict) else getattr(data_cfg, 'primary_symbol', "DOGEUSDT")
        else:
            # Если config - это объект Config
            api_keys = getattr(self.config, 'api_keys', None)
            if hasattr(api_keys, 'model_dump'):
                api_keys_dict = api_keys.model_dump()
            elif isinstance(api_keys, dict):
                api_keys_dict = api_keys
            else:
                api_keys_dict = {}
            self.api_key = api_keys_dict.get("binance_testnet_api_key", "") if api_keys_dict else ""
            self.api_secret = api_keys_dict.get("binance_testnet_api_secret", "") if api_keys_dict else ""
            
            data_cfg = getattr(self.config, 'data', {})
            self.symbols = getattr(data_cfg, 'symbols', ["DOGEUSDT", "BTCUSDT"]) if hasattr(data_cfg, 'symbols') else ["DOGEUSDT", "BTCUSDT"]
            self.primary_symbol = getattr(data_cfg, 'primary_symbol', "DOGEUSDT") if hasattr(data_cfg, 'primary_symbol') else "DOGEUSDT"
        
        # Клиент инициализируется при старте
        self.client: Optional[AsyncClient] = None
        self.ws_session: Optional[aiohttp.ClientSession] = None
        
        # Кэши данных
        self.order_books: Dict[str, Dict] = {}
        self.last_trades: Dict[str, List] = {}
        self.tickers: Dict[str, Dict] = {}
        
        # Callbacks для WebSocket событий
        self._orderbook_callbacks: List[Callable] = []
        self._trade_callbacks: List[Callable] = []
        
        self._running = False
        self._ws_tasks: List[asyncio.Task] = []
    
    async def start(self):
        """Инициализация клиента и подключение."""
        logger.info("Initializing Binance Futures client...")
        
        # Определяем testnet режим - всегда True для тестнета
        testnet = True
        
        self.client = await AsyncClient.create(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=testnet,
            requests_params={"timeout": 10}
        )
        
        logger.info(f"Binance client initialized (testnet={testnet})")
        
        # Предзагрузка начальных данных
        await self._preload_data()
        
        self._running = True
        logger.info("Binance Futures feed started")
    
    async def stop(self):
        """Корректная остановка."""
        self._running = False
        if self.client:
            await self.client.close_connection()
        if self.ws_manager:
            self.ws_manager.stop()
        logger.info("Binance Futures feed stopped")
    
    async def _preload_data(self):
        """Предзагрузка начального состояния стакана и тикеров."""
        logger.info("Preloading market data...")
        
        for symbol in self.symbols:
            try:
                # Стакан
                orderbook = await self.client.get_order_book(symbol=symbol, limit=20)
                self.order_books[symbol] = {
                    'bids': [[float(b[0]), float(b[1])] for b in orderbook['bids']],
                    'asks': [[float(a[0]), float(a[1])] for a in orderbook['asks']],
                    'timestamp': datetime.utcnow()
                }
                
                # Тикер
                ticker = await self.client.get_symbol_ticker(symbol=symbol)
                self.tickers[symbol] = {
                    'price': float(ticker['price']),
                    'timestamp': datetime.utcnow()
                }
                
                # Последние сделки
                trades = await self.client.get_recent_trades(symbol=symbol, limit=50)
                self.last_trades[symbol] = [
                    {
                        'price': float(t['price']),
                        'qty': float(t['qty']),
                        'is_buyer_maker': t['isBuyerMaker'],
                        'time': t['time']
                    }
                    for t in trades
                ]
                
                logger.info(f"Loaded data for {symbol}: price={self.tickers[symbol]['price']}")
                
            except Exception as e:
                logger.error(f"Error preloading data for {symbol}: {e}")
    
    def subscribe_orderbook(self, callback: Callable):
        """Подписка на обновления стакана."""
        self._orderbook_callbacks.append(callback)
    
    def subscribe_trades(self, callback: Callable):
        """Подписка на обновления сделок."""
        self._trade_callbacks.append(callback)
    
    async def start_websocket_streams(self):
        """Запуск WebSocket потоков для реального времени через aiohttp."""
        if not self._running:
            return
        
        logger.info("Starting WebSocket streams (aiohttp)...")
        
        # Создаем сессию aiohttp если нет
        if not self.ws_session:
            self.ws_session = aiohttp.ClientSession()
        
        # Запускаем задачи для каждого символа
        for symbol in self.symbols:
            # Стакан
            task_ob = asyncio.create_task(self._run_orderbook_ws(symbol))
            self._ws_tasks.append(task_ob)
            
            # Сделки
            task_tr = asyncio.create_task(self._run_trades_ws(symbol))
            self._ws_tasks.append(task_tr)
            
        logger.info(f"WebSocket tasks created for {len(self.symbols)} symbols.")

    async def _run_orderbook_ws(self, symbol: str):
        """WS задача для стакана."""
        url = f"wss://fstream.binancefuture.com/ws/{symbol.lower()}@depth20@100ms"
        while self._running:
            try:
                async with self.ws_session.ws_connect(url) as ws:
                    logger.info(f"Connected to orderbook WS for {symbol}")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            self._handle_orderbook_update(data)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except Exception as e:
                logger.error(f"Orderbook WS error for {symbol}: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def _run_trades_ws(self, symbol: str):
        """WS задача для сделок."""
        url = f"wss://fstream.binancefuture.com/ws/{symbol.lower()}@aggTrade"
        while self._running:
            try:
                async with self.ws_session.ws_connect(url) as ws:
                    logger.info(f"Connected to trades WS for {symbol}")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            self._handle_trade_update(data)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except Exception as e:
                logger.error(f"Trades WS error for {symbol}: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
    
    def _handle_orderbook_update(self, msg: Dict):
        """Обработчик обновлений стакана."""
        try:
            symbol = msg.get('s', '').upper()
            if not symbol:
                return
            
            bids = [[float(b[0]), float(b[1])] for b in msg.get('bids', [])]
            asks = [[float(a[0]), float(a[1])] for a in msg.get('asks', [])]
            
            if bids or asks:
                self.order_books[symbol] = {
                    'bids': bids,
                    'asks': asks,
                    'timestamp': datetime.utcnow()
                }
                
                # Уведомление подписчиков
                for callback in self._orderbook_callbacks:
                    try:
                        callback(symbol, self.order_books[symbol])
                    except Exception as e:
                        logger.error(f"Error in orderbook callback: {e}")
                        
        except Exception as e:
            logger.error(f"Error processing orderbook update: {e}")
    
    def _handle_trade_update(self, msg: Dict):
        """Обработчик обновлений сделок."""
        try:
            symbol = msg.get('s', '').upper()
            if not symbol:
                return
            
            trade_data = {
                'price': float(msg.get('p', 0)),
                'qty': float(msg.get('q', 0)),
                'is_buyer_maker': msg.get('m', False),
                'time': msg.get('T', 0)
            }
            
            if symbol not in self.last_trades:
                self.last_trades[symbol] = []
            
            self.last_trades[symbol].append(trade_data)
            # Храним последние 100 сделок
            self.last_trades[symbol] = self.last_trades[symbol][-100:]
            
            # Обновляем тикер
            self.tickers[symbol] = {
                'price': trade_data['price'],
                'timestamp': datetime.utcnow()
            }
            
            # Уведомление подписчиков
            for callback in self._trade_callbacks:
                try:
                    callback(symbol, trade_data)
                except Exception as e:
                    logger.error(f"Error in trade callback: {e}")
                    
        except Exception as e:
            logger.error(f"Error processing trade update: {e}")
    
    def get_orderbook(self, symbol: str) -> Optional[Dict]:
        """Получение последнего стакана."""
        return self.order_books.get(symbol)
    
    def get_last_trades(self, symbol: str, limit: int = 50) -> List[Dict]:
        """Получение последних сделок."""
        trades = self.last_trades.get(symbol, [])
        return trades[-limit:]
    
    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """Получение последнего тикера."""
        return self.tickers.get(symbol)
    
    def get_mid_price(self, symbol: str) -> Optional[float]:
        """Расчет средней цены (mid price)."""
        ob = self.get_orderbook(symbol)
        if ob and ob['bids'] and ob['asks']:
            return (ob['bids'][0][0] + ob['asks'][0][0]) / 2
        return None
    
    async def get_initial_price(self, symbol: str) -> Optional[float]:
        """Получение начальной цены для символа (REST API)."""
        if not self.client:
            logger.error(f"Client not initialized for {symbol}")
            return None
            
        try:
            # Используем правильный метод для фьючерсов
            ticker = await self.client.futures_symbol_ticker(symbol=symbol)
            price = float(ticker.get('price', 0))
            logger.info(f"[DataFeed] Initial price for {symbol}: {price}")
            return price
        except Exception as e:
            logger.error(f"Error getting initial price for {symbol}: {e}")
            return None
    
    async def get_account_balance(self) -> Optional[Dict]:
        """Получение баланса фьючерсного аккаунта."""
        if not self.api_key or not self.api_secret:
            return None
        
        try:
            balance = await self.client.futures_account_balance()
            return {b['asset']: b for b in balance}
        except Exception as e:
            logger.error(f"Error getting account balance: {e}")
            return None
    
    async def get_futures_position(self, symbol: str) -> Optional[Dict]:
        """Получение позиции по символу."""
        if not self.api_key or not self.api_secret:
            return None
        
        try:
            positions = await self.client.futures_position_information(symbol=symbol)
            for pos in positions:
                if pos['symbol'] == symbol:
                    return {
                        'position_amt': float(pos['positionAmt']),
                        'entry_price': float(pos['entryPrice']) if pos['entryPrice'] else 0,
                        'leverage': int(pos['leverage']),
                        'unrealized_pnl': float(pos['unRealizedProfit'])
                    }
        except Exception as e:
            logger.error(f"Error getting position for {symbol}: {e}")
        
        return None


# Legacy compatibility class for old code
class DataFeed:
    """Обертка для обратной совместимости."""
    
    def __init__(self, config: Dict[str, Any], prob_field=None):
        self.feed = BinanceFuturesFeed(config)
        self.prob_field = prob_field
        self.config = config
        
        # Подписка на обновления и отправка в ProbabilityField
        self.feed.subscribe_orderbook(self._on_orderbook)
        self.feed.subscribe_trades(self._on_trade)
    
    async def start(self):
        await self.feed.start()
        await self.feed.start_websocket_streams()
    
    async def stop(self):
        await self.feed.stop()
    
    async def get_initial_price(self, symbol: str) -> Optional[float]:
        """Проксирование вызова к BinanceFuturesFeed."""
        return await self.feed.get_initial_price(symbol)
    
    def _on_orderbook(self, symbol: str, orderbook: Dict):
        """Отправка стакана в ProbabilityField."""
        if self.prob_field:
            # Формируем DataCard с данными стакана
            from src.core.models import DataCard
            card = DataCard(
                symbol=symbol,
                timestamp=orderbook['timestamp'],
                
            )
            self.prob_field.update(card)
    
    def _on_trade(self, symbol: str, trade: Dict):
        """Отправка сделки в ProbabilityField."""
        if self.prob_field:
            from src.core.models import DataCard
            card = DataCard(
                symbol=symbol,
                timestamp=datetime.utcnow(),
                price=trade['price'],
                volume_24h=trade['qty'], # Используем объем текущей сделки как заглушку
                recent_trades=[trade] # Передаем текущую сделку как список
            )
            # Используем актуальный метод update_market_data
            asyncio.create_task(self.prob_field.update_market_data(
                symbol=symbol,
                price=trade['price'],
                volume=trade['qty'],
                orderbook=self.get_orderbook(symbol) or {},
                trades=[trade]
            ))
    
    def get_orderbook(self, symbol: str) -> Optional[Dict]:
        return self.feed.get_orderbook(symbol)
    
    def get_order_book(self, symbol: str) -> Optional[Dict]:
        """Алиас для get_orderbook для совместимости."""
        return self.feed.get_orderbook(symbol)
    
    async def get_candles(self, symbol: str, timeframe: str = '1m', limit: int = 50) -> List[Dict]:
        """
        Получает свечи через REST API.
        Возвращает список словарей: [{'open': ..., 'high': ..., 'low': ..., 'close': ..., 'volume': ...}, ...]
        """
        # Маппинг таймфреймов для Binance
        tf_map = {
            '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m',
            '30m': '30m', '1h': '1h', '2h': '2h', '4h': '4h',
            '6h': '6h', '12h': '12h', '1d': '1d'
        }
        interval = tf_map.get(timeframe, '1m')
        
        try:
            # Используем асинхронный вызов через await внутри async функции
            # Убрано loop.run_until_complete() для работы внутри запущенного event loop
            klines = await self.feed.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
            
            candles = []
            for k in klines:
                candles.append({
                    'timestamp': datetime.fromtimestamp(k[0] / 1000),
                    'open': float(k[1]),
                    'high': float(k[2]),
                    'low': float(k[3]),
                    'close': float(k[4]),
                    'volume': float(k[5])
                })
            return candles
        except Exception as e:
            logger.error(f"Error getting candles for {symbol} {timeframe}: {e}")
            return []
    
    def get_last_trades(self, symbol: str, limit: int = 50) -> List[Dict]:
        return self.feed.get_last_trades(symbol, limit)
    
    def get_ticker(self, symbol: str) -> Optional[Dict]:
        return self.feed.get_ticker(symbol)
