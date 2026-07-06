"""
Базовый класс для всех математических индикаторов.
Гарантирует единый интерфейс и изоляцию логики.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
import polars as pl


class BaseIndicator(ABC):
    """
    Абстрактный базовый класс для индикаторов.
    Все индикаторы должны наследовать этот класс и реализовать метод calculate.
    """

    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.config = config or {}
        # Внутреннее состояние индикатора (кэш, буферы)
        self._state: Dict[str, Any] = {}

    @abstractmethod
    def calculate(self, data: pl.DataFrame) -> Dict[str, Any]:
        """
        Основной метод расчета.
        
        Args:
            data: DataFrame с рыночными данными (ticks или свечи).
                  Обязательные колонки: ['timestamp', 'price', 'volume']
                  Опционально: ['bid', 'ask', 'oi', 'funding']
        
        Returns:
            Dict с результатами:
            - 'value': основное числовое значение
            - 'signal': направление (-1, 0, 1) или None
            - 'confidence': уверенность (0.0 - 1.0)
            - 'metadata': дополнительные данные (уровни, паттерны и т.д.)
            - 'tags': список тегов для маркировки в Матрице (напр. ['trend', 'btc_corr'])
        """
        pass

    def update_state(self, **kwargs):
        """Обновление внутреннего состояния (для stateful индикаторов)."""
        self._state.update(kwargs)

    def get_state(self) -> Dict[str, Any]:
        """Получение текущего состояния."""
        return self._state.copy()

    def validate_data(self, data: pl.DataFrame) -> bool:
        """Базовая валидация входных данных."""
        required_cols = {'timestamp', 'price', 'volume'}
        return required_cols.issubset(set(data.columns))
