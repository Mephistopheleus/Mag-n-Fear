"""
Logic Module: Matrix Analyzer
Читает Матрицу Вероятностей, ищет паттерны (кластеры высокой вероятности),
выдает результат с меткой "ЦЕЛЬ".

НЕ принимает торговых решений - только анализ данных из матрицы.
"""
from typing import Dict, List, Optional, Any
from datetime import datetime
from src.matrix.probability_field import MatrixSnapshot, ProbabilityCell, MatrixProbabilityField


class MatrixAnalyzer:
    """
    Анализатор Матрицы Вероятностей.
    
    Задачи:
    1. Найти кластеры ячеек с высокой средней вероятностью
    2. Определить направление тренда (лонг/шорт) на основе распределения
    3. Выдать цель: {цена, время, вероятность, тип_паттерна}
    
    Результат передается в Scenario Writer.
    """
    
    def __init__(self, min_cluster_size: int = 3, probability_threshold: float = 0.5):
        """
        :param min_cluster_size: Минимальное количество ячеек в кластере
        :param probability_threshold: Порог вероятности дляconsidering ячейки
        """
        self.min_cluster_size = min_cluster_size
        self.probability_threshold = probability_threshold
    
    def analyze(self, snapshot: MatrixSnapshot) -> Optional[Dict[str, Any]]:
        """
        Анализирует снимок матрицы и возвращает цель (если найдена).
        
        :return: {
            "target_price": float,
            "target_time_sec": int,
            "probability": float,
            "pattern_type": str,  # "cluster", "trend", "breakout"
            "confidence": float,  # Итоговая уверенность (сырая, без тюнера)
            "metadata": {...}
        } или None, если паттернов не найдено
        """
        if not snapshot.grid:
            return None
        
        # Шаг 1: Фильтрация ячеек по порогу вероятности
        significant_cells = [
            cell for cell in snapshot.grid.values()
            if cell.avg_probability >= self.probability_threshold
        ]
        
        if len(significant_cells) < self.min_cluster_size:
            return None
        
        # Шаг 2: Поиск кластера с максимальной суммарной вероятностью
        # (Простая эвристика: берем топ-N ячеек по avg_probability)
        sorted_cells = sorted(
            significant_cells,
            key=lambda c: c.avg_probability * c.count,  # Вес: вероятность × количество прогнозов
            reverse=True
        )
        
        # Берем топ-кластер (первые N ячеек)
        cluster = sorted_cells[:max(self.min_cluster_size, len(sorted_cells) // 2)]
        
        # Шаг 3: Расчет целевой цены и времени (взвешенное среднее)
        total_weight = sum(cell.count * cell.avg_probability for cell in cluster)
        if total_weight == 0:
            return None
        
        target_price = sum(
            (snapshot.price_min + cell.price_bin * (snapshot.price_range[1] - snapshot.price_range[0]) / snapshot.price_bins)
            * cell.count * cell.avg_probability
            for cell in cluster
        ) / total_weight
        
        target_time_sec = sum(
            cell.time_bin * (snapshot.time_horizon_sec / snapshot.time_bins)
            * cell.count * cell.avg_probability
            for cell in cluster
        ) / total_weight
        
        # Средняя вероятность кластера
        avg_cluster_probability = sum(cell.avg_probability for cell in cluster) / len(cluster)
        
        # Определение типа паттерна (упрощенно)
        pattern_type = self._detect_pattern_type(cluster, snapshot)
        
        return {
            "target_price": target_price,
            "target_time_sec": int(target_time_sec),
            "probability": avg_cluster_probability,
            "pattern_type": pattern_type,
            "confidence": avg_cluster_probability,  # Сырая уверенность
            "metadata": {
                "cluster_size": len(cluster),
                "total_predictions_in_cluster": sum(c.count for c in cluster),
                "analyzers_contrib": self._aggregate_analyzers_contrib(cluster),
                "price_range": (
                    min(snapshot.price_min + c.price_bin * (snapshot.price_range[1] - snapshot.price_range[0]) / snapshot.price_bins for c in cluster),
                    max(snapshot.price_min + c.price_bin * (snapshot.price_range[1] - snapshot.price_range[0]) / snapshot.price_bins for c in cluster)
                )
            }
        }
    
    def _detect_pattern_type(self, cluster: List[ProbabilityCell], snapshot: MatrixSnapshot) -> str:
        """Определяет тип паттерна на основе распределения ячеек."""
        if len(cluster) < 2:
            return "single_point"
        
        # Проверяем разброс цен
        prices = [
            snapshot.price_min + cell.price_bin * (snapshot.price_range[1] - snapshot.price_range[0]) / snapshot.price_bins
            for cell in cluster
        ]
        price_range = max(prices) - min(prices)
        avg_price = sum(prices) / len(prices)
        
        # Проверяем разброс времени
        times = [cell.time_bin * (snapshot.time_horizon_sec / snapshot.time_bins) for cell in cluster]
        time_range = max(times) - min(times)
        
        # Эвристика для определения типа
        if price_range < avg_price * 0.01:  # Узкий диапазон цен (<1%)
            return "consolidation"
        elif time_range < snapshot.time_horizon_sec * 0.2:  # Короткий временной диапазон
            return "breakout"
        else:
            return "trend"
    
    def _aggregate_analyzers_contrib(self, cluster: List[ProbabilityCell]) -> Dict[str, float]:
        """Агрегирует вклад анализаторов в кластере."""
        contrib: Dict[str, float] = {}
        total_count = 0
        
        for cell in cluster:
            for analyzer_type, value in cell.analyzers_contrib.items():
                contrib[analyzer_type] = contrib.get(analyzer_type, 0) + value
            total_count += cell.count
        
        # Нормализация
        if total_count > 0:
            contrib = {k: v / total_count for k, v in contrib.items()}
        
        return contrib
