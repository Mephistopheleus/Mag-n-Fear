"""
Data Feed Module: Binance Futures Connector
Использует python-binance (AsyncClient) для получения данных через WebSocket и REST API.
Заменяет ccxt на легковесную библиотеку.
"""
import asyncio
import logging
from typing import Dict, List, Optional, Callable, Any
from binance import AsyncClient, ThreadedWebsocketManager
from datetime import datetime

logger = logging.getLogger(__name__)


class BinanceFuturesFeed:
    """
    Асинхронный коннектор к Binance Futures.
    Предоставляет:
    - REST API для получения стакана и исторических данных
    - WebSocket для потоковых обновлений (стакан, сделки, тикеры)
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.api_key = config.get("api_keys", {}).get("binance_testnet_api_key", "")
        self.api_secret = config.get("api_keys", {}).get("binance_testnet_api_secret", "")
        
        # Символы для отслеживания
        self.symbols = config.get("data", {}).get("symbols", ["DOGEUSDT", "BTCUSDT"])
        self.primary_symbol = config.get("data", {}).get("primary_symbol", "DOGEUSDT")
        
        # Клиент инициализируется при старте
        self.client: Optional[AsyncClient] = None
        self.ws_manager: Optional[ThreadedWebsocketManager] = None
        
        # Кэши данных
        self.order_books: Dict[str, Dict] = {}
        self.last_trades: Dict[str, List] = {}
        self.tickers: Dict[str, Dict] = {}
        
        # Callbacks для WebSocket событий
        self._orderbook_callbacks: List[Callable] = []
        self._trade_callbacks: List[Callable] = []
        
        self._running = False
    
    async def start(self):
        """Инициализация клиента и подключение."""
        logger.info("Initializing Binance Futures client...")
        
        # Определяем testnet режим
        testnet = self.config.get("bot", {}).get("mode", "shadow") == "shadow"
        
        self.client = await AsyncClient.create(
            api_key=self.api_key if self.api_key else None,
            api_secret=self.api_secret if self.api_secret else None,
            testnet=testnet,
            requests_params={"timeout": 10}
        )
        
        logger.info(f"Binance client initialized (testnet={testnet})")
        
        # Инициализация WebSocket менеджера
        self.ws_manager = ThreadedWebsocketManager(
            api_key=self.api_key if self.api_key else None,
            api_secret=self.api_secret if self.api_secret else None,
            testnet=testnet
        )
        self.ws_manager.start()
        
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
        """Запуск WebSocket потоков для реального времени."""
        if not self._running:
            return
        
        logger.info("Starting WebSocket streams...")
        
        # Глубина стакана (обновления) - используем futures stream
        for symbol in self.symbols:
            self.ws_manager.start_futures_depth_socket(
                callback=self._handle_orderbook_update,
                symbol=symbol.lower(),
                depth=20
            )
            logger.info(f"Started orderbook stream for {symbol}")
        
        # Агрегированные сделки
        for symbol in self.symbols:
            self.ws_manager.start_futures_aggtrade_socket(
                callback=self._handle_trade_update,
                symbol=symbol.lower()
            )
            logger.info(f"Started trade stream for {symbol}")
    
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
    
    def __init__(self, config: Dict[str, Any]):
        self.feed = BinanceFuturesFeed(config)
    
    async def start(self):
        await self.feed.start()
    
    async def stop(self):
        await self.feed.stop()
    
    def get_orderbook(self, symbol: str) -> Optional[Dict]:
        return self.feed.get_orderbook(symbol)
    
    def get_last_trades(self, symbol: str, limit: int = 50) -> List[Dict]:
        return self.feed.get_last_trades(symbol, limit)
    
    def get_ticker(self, symbol: str) -> Optional[Dict]:
        return self.feed.get_ticker(symbol)
