"""
ProbabilityField: Thread-safe asynchronous data matrix.
Central hub where all analyzers write their calculations and ScenarioWriter reads the aggregated state.
"""
import asyncio
import threading
import time
from typing import Dict, Any, Optional, List, Callable
from collections import defaultdict
from src.core.models import DataCard, RiskMetrics, NewsVector
from src.matrix.probability_field import MatrixProbabilityField


class ProbabilityField:
    """
    Потокобезопасное хранилище данных (Матрица).
    
    Архитектура:
    - Ключи: символы (DOGEUSDT) или категории данных.
    - Значения: последние актуальные DataCard + история метрик.
    - Все операции асинхронные и потокобезопасные.
    
    Модули пишут в свои "слои":
    - math_core -> layer 'math'
    - news_aggregator -> layer 'news'
    - risk_manager -> layer 'risk'
    - auto_tuner -> layer 'tuner'
    
    Также содержит MatrixProbabilityField для агрегации прогнозов от всех анализаторов.
    """
    
    def __init__(self):
        self._lock = asyncio.Lock()
        self._data_store: Dict[str, DataCard] = {}
        self._history: Dict[str, List[Dict]] = defaultdict(list)
        self._subscribers: List[Callable] = []
        self._max_history_len = 100  # Храним последние N обновлений для анализа
        
        # Матрица вероятностей для каждого символа (Время × Цена × Вероятность)
        self._matrix_fields: Dict[str, MatrixProbabilityField] = {}
        
    async def initialize_symbol(self, symbol: str, initial_price: float):
        """Инициализация DataCard для нового символа."""
        async with self._lock:
            if symbol not in self._data_store:
                self._data_store[symbol] = DataCard(
                    symbol=symbol,
                    timestamp=time.time(),
                    price=initial_price,
                    volume_24h=0.0
                )
            
            # Инициализация матрицы вероятностей для символа
            if symbol not in self._matrix_fields:
                self._matrix_fields[symbol] = MatrixProbabilityField(
                    time_bins=10,
                    price_bins=20,
                    time_horizon_sec=600,  # 10 минут горизонт
                    current_price=initial_price
                )
    
    @property
    def current_price(self) -> float:
        """Возвращает цену первого доступного символа (для совместимости)."""
        if self._data_store:
            for card in self._data_store.values():
                return card.price
        return 0.0
    
    async def update_math_surface(self, symbol: str, key: str, value: Any):
        """MathCore записывает результаты расчетов (индикаторы, свечи)."""
        async with self._lock:
            if symbol not in self._data_store:
                return
            card = self._data_store[symbol]
            card.math_surfaces[key] = value
            card.timestamp = time.time()
            await self._notify_subscribers(symbol, card)

    async def update_news_vector(self, symbol: str, vector: NewsVector):
        """NewsAggregator добавляет вектор новости."""
        async with self._lock:
            if symbol not in self._data_store:
                return
            card = self._data_store[symbol]
            card.add_news_vector(vector)
            card.timestamp = time.time()
            await self._notify_subscribers(symbol, card)

    async def update_risk_metrics(self, symbol: str, metrics: RiskMetrics):
        """RiskManager записывает метрики риска."""
        async with self._lock:
            if symbol not in self._data_store:
                return
            card = self._data_store[symbol]
            card.update_risk(metrics)
            card.timestamp = time.time()
            await self._notify_subscribers(symbol, card)

    async def update_market_data(self, symbol: str, price: float, volume: float, orderbook: dict, trades: list):
        """DataFeed обновляет рыночные данные."""
        async with self._lock:
            if symbol not in self._data_store:
                await self.initialize_symbol(symbol, price)
            
            card = self._data_store[symbol]
            card.price = price
            card.volume_24h = volume
            card.orderbook_snapshot = orderbook
            card.recent_trades = trades[-50:]  # Храним последние 50 сделок
            card.timestamp = time.time()
            
            await self._notify_subscribers(symbol, card)

    async def update_tuner_confidence(self, symbol: str, confidence: float, strategy_id: str):
        """AutoTuner обновляет коэффициент доверия."""
        async with self._lock:
            if symbol not in self._data_store:
                return
            card = self._data_store[symbol]
            card.tuner_confidence = max(0.0, min(1.0, confidence))
            card.active_strategy_id = strategy_id
            card.timestamp = time.time()
            await self._notify_subscribers(symbol, card)

    async def update_shadow_result(self, symbol: str, result: Dict[str, Any]):
        """RiskManager записывает результаты теневого расчета для обучения AutoTuner."""
        async with self._lock:
            if symbol not in self._data_store:
                return
            card = self._data_store[symbol]
            # Добавляем результаты тени в специальное поле
            if not hasattr(card, 'shadow_results'):
                card.shadow_results = {}
            card.shadow_results = result
            card.timestamp = time.time()
            await self._notify_subscribers(symbol, card)

    async def get_card(self, symbol: str) -> Optional[DataCard]:
        """Получение актуальной DataCard для чтения (ScenarioWriter)."""
        async with self._lock:
            card = self._data_store.get(symbol)
            if card:
                # Возвращаем копию, чтобы избежать гонок данных при чтении
                return card
            return None

    async def get_historical_metrics(self, symbol: str, limit: int = 10) -> List[Dict]:
        """Получение истории метрик для анализа трендов риска."""
        async with self._lock:
            return self._history[symbol][-limit:]

    def subscribe(self, callback: Callable):
        """Подписка на обновления данных (для реактивных модулей)."""
        self._subscribers.append(callback)

    async def _notify_subscribers(self, symbol: str, card: DataCard):
        """Уведомление подписчиков об изменении данных."""
        # Сохраняем в историю
        self._history[symbol].append({
            "timestamp": card.timestamp,
            "price": card.price,
            "risk": card.risk_metrics,
            "sentiment": card.get_aggregated_sentiment()
        })
        if len(self._history[symbol]) > self._max_history_len:
            self._history[symbol].pop(0)

        # Асинхронный вызов подписчиков
        for sub in self._subscribers:
            try:
                if asyncio.iscoroutinefunction(sub):
                    await sub(symbol, card)
                else:
                    sub(symbol, card)
            except Exception as e:
                print(f"[ProbabilityField] Error in subscriber {sub}: {e}")

    def clear(self):
        """Очистка поля (для тестов)."""
        self._data_store.clear()
        self._history.clear()
        self._matrix_fields.clear()
    
    async def add_prediction_to_matrix(self, symbol: str, predicted_price: float, predicted_time_sec: int, probability: float, analyzer_type: str):
        """Добавляет прогноз от анализатора в матрицу вероятностей для указанного символа."""
        async with self._lock:
            if symbol not in self._matrix_fields:
                return  # Матрица еще не инициализирована
            matrix = self._matrix_fields[symbol]
            matrix.add_prediction(
                predicted_price=predicted_price,
                predicted_time_sec=predicted_time_sec,
                probability=probability,
                analyzer_type=analyzer_type
            )
    
    def get_matrix_snapshot(self, symbol: str):
        """Получает снимок матрицы вероятностей для анализа."""
        if symbol not in self._matrix_fields:
            return None
        return self._matrix_fields[symbol].get_snapshot()
