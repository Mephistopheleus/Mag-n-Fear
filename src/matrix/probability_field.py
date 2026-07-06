"""
Matrix Module: Probability Field
Раскладывает прогнозы от анализаторов по сетке Время × Цена × Вероятность.
НЕ анализирует, НЕ принимает решений - только агрегация данных.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
import numpy as np


@dataclass
class ProbabilityCell:
    """Одна ячейка в матрице вероятностей."""
    time_bin: int  # Индекс временного интервала
    price_bin: int  # Индекс ценовой корзины
    probability_sum: float = 0.0  # Сумма вероятностей всех прогнозов, попавших в эту ячейку
    count: int = 0  # Количество прогнозов в ячейке
    analyzers_contrib: Dict[str, float] = field(default_factory=dict)  # Вклад по типам анализаторов
    
    @property
    def avg_probability(self) -> float:
        """Средняя вероятность в ячейке."""
        if self.count == 0:
            return 0.0
        return self.probability_sum / self.count


@dataclass
class MatrixSnapshot:
    """Снимок Матрицы на момент времени."""
    timestamp: datetime
    current_price: float
    grid: Dict[Tuple[int, int], ProbabilityCell]  # (time_bin, price_bin) -> Cell
    time_bins: int
    price_bins: int
    time_horizon_sec: int
    price_range: Tuple[float, float]  # (min_price, max_price)
    
    def to_dict(self) -> Dict:
        """Сериализация для сохранения."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "current_price": self.current_price,
            "grid": {
                f"{k[0]}_{k[1]}": {
                    "time_bin": v.time_bin,
                    "price_bin": v.price_bin,
                    "probability_sum": v.probability_sum,
                    "count": v.count,
                    "avg_probability": v.avg_probability,
                    "analyzers_contrib": v.analyzers_contrib
                }
                for k, v in self.grid.items()
            },
            "time_bins": self.time_bins,
            "price_bins": self.price_bins,
            "time_horizon_sec": self.time_horizon_sec,
            "price_range": self.price_range
        }


class ProbabilityField:
    """
    Матрица Полей Вероятности.
    
    Принцип работы:
    1. Создается сетка: время (N интервалов) × цена (M корзин)
    2. Каждый анализатор отправляет прогноз: {цена, время, вероятность, тип_анализатора}
    3. Прогноз попадает в соответствующую ячейку сетки
    4. Ячейка накапливает сумму вероятностей и счетчик
    5. Matrix Analyzer позже читает эту сетку и ищет паттерны
    
    ВАЖНО: Этот класс НЕ принимает решений, НЕ анализирует.
    Только хранение и агрегация.
    """
    
    def __init__(self, time_bins: int, price_bins: int, time_horizon_sec: int, current_price: float):
        """
        :param time_bins: Количество временных интервалов
        :param price_bins: Количество ценовых корзин
        :param time_horizon_sec: Общий горизонт прогноза в секундах
        :param current_price: Текущая цена (центр ценового диапазона)
        """
        self.time_bins = time_bins
        self.price_bins = price_bins
        self.time_horizon_sec = time_horizon_sec
        self.current_price = current_price
        
        # Динамический ценовой диапазон: ±5% от текущей цены (можно сделать настраиваемым)
        price_range_pct = 0.05
        self.price_min = current_price * (1 - price_range_pct)
        self.price_max = current_price * (1 + price_range_pct)
        self.price_step = (self.price_max - self.price_min) / price_bins
        
        # Временной шаг
        self.time_step_sec = time_horizon_sec / time_bins
        
        # Сетка: (time_bin, price_bin) -> ProbabilityCell
        self.grid: Dict[Tuple[int, int], ProbabilityCell] = {}
        
        # Метаданные
        self.created_at = datetime.utcnow()
        self.last_updated = self.created_at
        self.total_predictions = 0
    
    def _get_time_bin(self, seconds_from_now: int) -> int:
        """Определяет индекс временной корзины."""
        if seconds_from_now < 0:
            return 0
        if seconds_from_now >= self.time_horizon_sec:
            return self.time_bins - 1
        return int(seconds_from_now / self.time_step_sec)
    
    def _get_price_bin(self, price: float) -> int:
        """Определяет индекс ценовой корзины."""
        if price <= self.price_min:
            return 0
        if price >= self.price_max:
            return self.price_bins - 1
        bin_idx = int((price - self.price_min) / self.price_step)
        return min(bin_idx, self.price_bins - 1)
    
    def add_prediction(
        self,
        predicted_price: float,
        predicted_time_sec: int,
        probability: float,
        analyzer_type: str
    ):
        """
        Добавляет прогноз от анализатора в матрицу.
        
        :param predicted_price: Прогнозируемая цена
        :param predicted_time_sec: Время прогноза в секундах от текущего момента
        :param probability: Вероятность прогноза (0.0 - 1.0)
        :param analyzer_type: Тип анализатора ("trend", "mean_reversion", etc.)
        """
        time_bin = self._get_time_bin(predicted_time_sec)
        price_bin = self._get_price_bin(predicted_price)
        
        key = (time_bin, price_bin)
        
        if key not in self.grid:
            self.grid[key] = ProbabilityCell(
                time_bin=time_bin,
                price_bin=price_bin
            )
        
        cell = self.grid[key]
        cell.probability_sum += probability
        cell.count += 1
        cell.analyzers_contrib[analyzer_type] = cell.analyzers_contrib.get(analyzer_type, 0) + probability
        
        self.total_predictions += 1
        self.last_updated = datetime.utcnow()
    
    def get_cell(self, time_bin: int, price_bin: int) -> Optional[ProbabilityCell]:
        """Получает ячейку по индексу."""
        return self.grid.get((time_bin, price_bin))
    
    def get_snapshot(self) -> MatrixSnapshot:
        """Создает снимок текущей матрицы."""
        return MatrixSnapshot(
            timestamp=datetime.utcnow(),
            current_price=self.current_price,
            grid=self.grid.copy(),
            time_bins=self.time_bins,
            price_bins=self.price_bins,
            time_horizon_sec=self.time_horizon_sec,
            price_range=(self.price_min, self.price_max)
        )
    
    def clear(self):
        """Очищает матрицу (для нового цикла)."""
        self.grid.clear()
        self.total_predictions = 0
        self.last_updated = datetime.utcnow()
    
    def get_heatmap_data(self) -> List[Dict]:
        """
        Возвращает данные для визуализации heatmap.
        Используется Matrix Analyzer для поиска паттернов.
        """
        heatmap = []
        for (time_bin, price_bin), cell in self.grid.items():
            heatmap.append({
                "time_bin": time_bin,
                "price_bin": price_bin,
                "avg_probability": cell.avg_probability,
                "count": cell.count,
                "time_sec": time_bin * self.time_step_sec,
                "price": self.price_min + price_bin * self.price_step
            })
        return sorted(heatmap, key=lambda x: (x["time_bin"], x["price_bin"]))
