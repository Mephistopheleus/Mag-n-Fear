"""
Historical Data Loader Module.
Загрузка исторических данных (OHLCV, OrderBook) при старте системы для "разогрева".
"""
import logging
import asyncio
from typing import List, Dict, Optional
from binance.async_client import AsyncClient
from src.core.field import ProbabilityField

logger = logging.getLogger(__name__)


class HistoricalLoader:
    """Загрузчик исторических данных для инициализации системы."""
    
    def __init__(self, client: AsyncClient, field: ProbabilityField, config: Dict):
        self.client = client
        self.field = field
        self.config = config
    
    async def warmup_symbol(self, symbol: str, days: int = 1, interval: str = '5m'):
        """
        Загрузка исторических данных для конкретного символа.
        
        :param symbol: Торговая пара (например, 'BTCUSDT')
        :param days: Количество дней истории
        :param interval: Таймфрейм ('1m', '5m', '15m', '1h')
        """
        logger.info(f"🔥 Warming up {symbol}: loading {days} day(s) of {interval} data...")
        
        try:
            # Расчет количества свечей
            candles_per_day = {
                '1m': 1440,
                '3m': 480,
                '5m': 288,
                '15m': 96,
                '30m': 48,
                '1h': 24,
                '2h': 12,
                '4h': 6,
                '6h': 4,
                '12h': 2,
                '1d': 1
            }
            
            limit = min(candles_per_day.get(interval, 288) * days, 1000)  # Максимум 1000 за запрос
            
            # 1. Загрузка OHLCV данных
            klines = await self.client.futures_klines(
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            
            if not klines:
                logger.warning(f"No historical data found for {symbol}")
                return
            
            logger.info(f"Loaded {len(klines)} candles for {symbol}")
            
            # 2. Обработка и запись в ProbabilityField
            candles_processed = 0
            for candle in klines:
                candle_data = {
                    'timestamp': candle[0],
                    'open': float(candle[1]),
                    'high': float(candle[2]),
                    'low': float(candle[3]),
                    'close': float(candle[4]),
                    'volume': float(candle[5]),
                    'quote_volume': float(candle[7]),
                    'trades_count': int(candle[8])
                }
                
                # Добавляем свечу в поле вероятностей
                await self.field.add_candle(symbol, candle_data)
                candles_processed += 1
                
                # Небольшая пауза чтобы не блокировать event loop
                if candles_processed % 100 == 0:
                    await asyncio.sleep(0.01)
            
            logger.info(f"✅ {symbol} warmup complete: {candles_processed} candles added to ProbabilityField")
            
            # 3. (Опционально) Загрузка снимков стакана
            if self.config.get('warmup_orderbook', False):
                await self._warmup_orderbook(symbol)
            
        except Exception as e:
            logger.error(f"Error warming up {symbol}: {e}")
            raise
    
    async def _warmup_orderbook(self, symbol: str, depth: int = 20):
        """Загрузка текущего состояния стакана для инициализации уровней."""
        try:
            orderbook = await self.client.futures_order_book(symbol=symbol, limit=depth)
            
            bids = [(float(p), float(q)) for p, q in orderbook.get('bids', [])]
            asks = [(float(p), float(q)) for p, q in orderbook.get('asks', [])]
            
            # Запись в поле как "текущий срез ликвидности"
            await self.field.add_liquidity_snapshot(symbol, {
                'timestamp': asyncio.get_event_loop().time() * 1000,
                'bids': bids,
                'asks': asks,
                'spread': asks[0][0] - bids[0][0] if asks and bids else 0
            })
            
            logger.debug(f"Orderbook snapshot loaded for {symbol}: spread={orderbook.get('asks', [['0']])[0][0]}-{orderbook.get('bids', [['0']])[0][0]}")
            
        except Exception as e:
            logger.warning(f"Could not load orderbook for {symbol}: {e}")
    
    async def warmup_all_symbols(self, symbols: List[str], days: int = 1, interval: str = '5m'):
        """Пакетная загрузка данных для нескольких символов."""
        logger.info(f"Starting global warmup for {len(symbols)} symbols...")
        
        tasks = [self.warmup_symbol(symbol, days, interval) for symbol in symbols]
        
        # Выполняем последовательно, чтобы не превысить rate limits
        for i, task in enumerate(tasks, 1):
            logger.info(f"Warming up symbol {i}/{len(symbols)}: {symbols[i-1]}")
            try:
                await task
                # Пауза между символами для соблюдения лимитов Binance API
                await asyncio.sleep(0.5) 
            except Exception as e:
                logger.error(f"Failed to warmup {symbols[i-1]}: {e}")
                continue
        
        logger.info("🌡️ Global warmup process finished.")
