"""
Logic Module: Matrix Analyzer
Читает список точек из ProbabilityField ("склад"), ищет паттерны (кластеры),
выдает результат с меткой "ЦЕЛЬ".

НЕ принимает торговых решений - только анализ данных из матрицы.
"""
from typing import Dict, List, Optional, Any
from datetime import datetime
from src.core.field import PredictionPoint


class MatrixAnalyzer:
    """
    Анализатор Матрицы Вероятностей.
    
    Задачи:
    1. Найти кластеры точек с высокой вероятностью в пространстве Цена×Время
    2. Определить направление тренда (лонг/шорт) на основе распределения
    3. Выдать цель: {цена, время, вероятность, тип_паттерна}
    
    Результат передается в Scenario Writer.
    """
    
    def __init__(self, min_cluster_size: int = 3, probability_threshold: float = 0.5):
        """
        :param min_cluster_size: Минимальное количество точек в кластере
        :param probability_threshold: Порог вероятности для рассмотрения точки
        """
        self.min_cluster_size = min_cluster_size
        self.probability_threshold = probability_threshold
    
    def analyze(self, points: List[PredictionPoint], current_price: float) -> Optional[Dict[str, Any]]:
        """
        Анализирует список точек и возвращает цель (если найдена).
        
        :param points: Список всех прогнозных точек из ProbabilityField
        :param current_price: Текущая цена актива
        :return: {
            "target_price": float,
            "target_time_sec": int,
            "probability": float,
            "pattern_type": str,  # "cluster", "trend", "breakout", "trap", "sideways"
            "confidence": float,  # Итоговая уверенность (сырая, без тюнера)
            "metadata": {...}
        } или None, если паттернов не найдено
        """
        if not points:
            return None
        
        # Шаг 1: Фильтрация точек по порогу вероятности
        significant_points = [p for p in points if p.probability >= self.probability_threshold]
        
        if len(significant_points) < self.min_cluster_size:
            return None
        
        # Шаг 2: Поиск кластеров (упрощённо: группируем близкие точки)
        clusters = self._find_clusters(significant_points, current_price)
        
        if not clusters:
            return None
        
        # Шаг 3: Выбор лучшего кластера (с максимальной суммарной вероятностью)
        best_cluster = max(clusters, key=lambda c: sum(p.probability for p in c))
        
        # Шаг 4: Расчет целевой цены и времени (взвешенное среднее)
        total_weight = sum(p.probability for p in best_cluster)
        if total_weight == 0:
            return None
        
        target_price = sum(p.price * p.probability for p in best_cluster) / total_weight
        target_time_sec = sum(p.time_sec * p.probability for p in best_cluster) / total_weight
        
        # Средняя вероятность кластера
        avg_cluster_probability = sum(p.probability for p in best_cluster) / len(best_cluster)
        
        # Определение типа паттерна
        pattern_type = self._detect_pattern_type(best_cluster, current_price)
        
        return {
            "target_price": target_price,
            "target_time_sec": int(target_time_sec),
            "probability": avg_cluster_probability,
            "pattern_type": pattern_type,
            "confidence": avg_cluster_probability,  # Сырая уверенность
            "metadata": {
                "cluster_size": len(best_cluster),
                "analyzers_contrib": self._aggregate_analyzers_contrib(best_cluster),
                "price_range": (min(p.price for p in best_cluster), max(p.price for p in best_cluster)),
                "time_range": (min(p.time_sec for p in best_cluster), max(p.time_sec for p in best_cluster))
            }
        }
    
    def _find_clusters(self, points: List[PredictionPoint], current_price: float, price_tolerance_pct: float = 0.02, time_tolerance_sec: int = 60) -> List[List[PredictionPoint]]:
        """
        Ищет кластеры точек в пространстве Цена×Время.
        Точки считаются близкими, если их цены отличаются менее чем на price_tolerance_pct
        и время отличается менее чем на time_tolerance_sec.
        """
        clusters = []
        used_indices = set()
        
        for i, point in enumerate(points):
            if i in used_indices:
                continue
            
            cluster = [point]
            used_indices.add(i)
            
            for j, other_point in enumerate(points):
                if j in used_indices or j == i:
                    continue
                
                price_diff_pct = abs(other_point.price - point.price) / current_price
                time_diff = abs(other_point.time_sec - point.time_sec)
                
                if price_diff_pct <= price_tolerance_pct and time_diff <= time_tolerance_sec:
                    cluster.append(other_point)
                    used_indices.add(j)
            
            if len(cluster) >= self.min_cluster_size:
                clusters.append(cluster)
        
        return clusters
    
    def _detect_pattern_type(self, cluster: List[PredictionPoint], current_price: float) -> str:
        """Определяет тип паттерна на основе распределения точек."""
        if len(cluster) < 2:
            return "single_point"
        
        prices = [p.price for p in cluster]
        times = [p.time_sec for p in cluster]
        
        price_range = max(prices) - min(prices)
        avg_price = sum(prices) / len(prices)
        time_range = max(times) - min(times)
        avg_time = sum(times) / len(times)
        
        # Направление
        direction = "bullish" if avg_price > current_price else "bearish"
        
        # Эвристика для определения типа
        if price_range < avg_price * 0.005:  # Очень узкий диапазон цен (<0.5%)
            if time_range < 120:  # Короткое время
                return f"{direction}_scalp"  # Скальпинг
            else:
                return "consolidation"  # Боковик/накопление
        elif time_range < 60:  # Очень быстрое движение
            return f"{direction}_breakout"  # Пробой
        elif avg_time > 300:  # Долгий горизонт (>5 мин)
            return f"{direction}_trend"  # Тренд
        elif price_range > avg_price * 0.03:  # Широкий разброс цен
            return "trap"  # Ловушка (неопределённость)
        else:
            return f"{direction}_swing"  # Свинг
    
    def _aggregate_analyzers_contrib(self, cluster: List[PredictionPoint]) -> Dict[str, float]:
        """Агрегирует вклад анализаторов в кластере."""
        contrib: Dict[str, float] = {}
        total_weight = 0
        
        for point in cluster:
            source = point.source
            weight = point.probability
            contrib[source] = contrib.get(source, 0) + weight
            total_weight += weight
        
        # Нормализация
        if total_weight > 0:
            contrib = {k: v / total_weight for k, v in contrib.items()}
        
        return contrib
