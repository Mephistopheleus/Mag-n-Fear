"""
ProbabilityField: Thread-safe asynchronous data storage ("Dumb Warehouse").
Central hub where all analyzers write their predictions as points {price, time, probability, source}.
NO formulas, NO grids, NO analysis - just storage and retrieval.
"""
import asyncio
import threading
import time
from typing import Dict, Any, Optional, List, Callable
from collections import defaultdict
from src.core.models import DataCard, RiskMetrics, NewsVector


class PredictionPoint:
    """Одна точка прогноза в поле вероятностей."""
    def __init__(self, price: float, time_sec: int, probability: float, source: str, timestamp: float = None):
        self.price = price  # Прогнозируемая цена
        self.time_sec = time_sec  # Время достижения (секунды от текущего момента)
        self.probability = probability  # Вероятность (0.0 - 1.0)
        self.source = source  # Тип анализатора (например, "trend", "news", "matrix")
        self.timestamp = timestamp or time.time()  # Время добавления точки
    
    def to_dict(self) -> Dict:
        return {
            "price": self.price,
            "time_sec": self.time_sec,
            "probability": self.probability,
            "source": self.source,
            "timestamp": self.timestamp
        }


class ProbabilityField:
    """
    Потокобезопасное хранилище данных (Матрица).
    
    Архитектура:
    - Ключи: символы (DOGEUSDT)
    - Значения: список точек прогнозов + последние актуальные DataCard
    - Все операции асинхронные и потокобезопасные.
    
    Модули пишут в свои "слои":
    - Анализаторы -> добавляют точки в _prediction_points
    - MathCore -> записывает индикаторы в math_surfaces
    - NewsAggregator -> добавляет векторы новостей
    - RiskManager -> записывает метрики риска
    
    ВАЖНО: Этот класс НЕ анализирует, НЕ считает формулы.
    Только хранение и предоставление данных.
    """
    
    def __init__(self):
        self._lock = asyncio.Lock()
        self._data_store: Dict[str, DataCard] = {}
        self._history: Dict[str, List[Dict]] = defaultdict(list)
        self._subscribers: List[Callable] = []
        self._max_history_len = 100  # Храним последние N обновлений для анализа
        
        # Поле вероятностей: символ -> список точек прогнозов
        self._prediction_points: Dict[str, List[PredictionPoint]] = defaultdict(list)
        self._max_points_per_symbol = 1000  # Максимум точек на символ (старые удаляются)
        
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
    
    @property
    def current_price(self) -> float:
        """Возвращает цену первого доступного символа (для совместимости)."""
        if self._data_store:
            for card in self._data_store.values():
                return card.price
        return 0.0
    
    def add_point(self, symbol: str, price: float, time_sec: int, probability: float, source: str):
        """
        Добавляет точку прогноза в поле вероятностей.
        ВЫЗЫВАЕТСЯ АНАЛИЗАТОРАМИ напрямую (синхронно).
        
        :param symbol: Символ (DOGEUSDT)
        :param price: Прогнозируемая цена
        :param time_sec: Время достижения в секундах от текущего момента
        :param probability: Вероятность прогноза (0.0 - 1.0)
        :param source: Тип анализатора (например, "trend", "news", "fractal")
        """
        point = PredictionPoint(
            price=price,
            time_sec=time_sec,
            probability=probability,
            source=source
        )
        
        self._prediction_points[symbol].append(point)
        
        # Удаляем старые точки, если превышен лимит
        if len(self._prediction_points[symbol]) > self._max_points_per_symbol:
            self._prediction_points[symbol] = self._prediction_points[symbol][-self._max_points_per_symbol:]
    
    async def add_prediction_point(self, symbol: str, price: float, time_sec: int, probability: float, source: str):
        """
        Устаревший метод для совместимости. Вызывает add_point().
        """
        self.add_point(symbol, price, time_sec, probability, source)
    
    async def get_prediction_points(self, symbol: str, time_range: Optional[tuple] = None) -> List[PredictionPoint]:
        """
        Получает список точек прогнозов для символа.
        
        :param symbol: Символ
        :param time_range: Опциональный фильтр по времени (min_sec, max_sec)
        :return: Список точек
        """
        async with self._lock:
            points = self._prediction_points.get(symbol, []).copy()
            
            if time_range:
                min_sec, max_sec = time_range
                points = [p for p in points if min_sec <= p.time_sec <= max_sec]
            
            return points
    
    async def clear_predictions(self, symbol: str):
        """Очищает все точки прогнозов для символа (для нового цикла)."""
        async with self._lock:
            self._prediction_points[symbol] = []
    
    @property
    def points(self) -> Dict[str, List[PredictionPoint]]:
        """Возвращает словарь всех точек прогнозов по символам (для совместимости)."""
        return dict(self._prediction_points)
    
    def get_points_sync(self, symbol: str) -> List[PredictionPoint]:
        """Синхронный метод получения точек для символа."""
        return self._prediction_points.get(symbol, []).copy()
    
    async def update_math_surface(self, symbol: str, key: str, value: Any):
        """MathCore записывает результаты расчетов (индикаторы, свечи)."""
        async with self._lock:
            if symbol not in self._data_store:
                return
            card = self._data_store[symbol]
            card.math_surfaces[key] = value
            card.timestamp = time.time()
            await self._notify_subscribers(symbol, card)

    def update_news_vector(self, symbol: str, vector: NewsVector):
        """NewsAggregator добавляет вектор новости (синхронно)."""
        if symbol not in self._data_store:
            return
        card = self._data_store[symbol]
        card.add_news_vector(vector)
        card.timestamp = time.time()
        # Уведомление подписчиков (асинхронно, но без ожидания)
        asyncio.create_task(self._notify_subscribers(symbol, card))

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
        self._prediction_points.clear()

    def get_matrix_snapshot(self, symbol: str = None) -> List[PredictionPoint]:
        """
        Вернуть снимок всех текущих точек (прогнозов) для символа.
        Для совместимости с main.py.
        """
        if symbol:
            return self._prediction_points.get(symbol, []).copy()
        # Если символ не указан, возвращаем все точки со всех символов
        all_points = []
        for points_list in self._prediction_points.values():
            all_points.extend(points_list)
        return all_points
