"""
Scenario Writer Module.
Центральный узел принятия решений.
Формирует полный сценарий сделки на основе:
1. Кластеров от MatrixAnalyzer (кросс-валидация прогнозов)
2. Прямых точек из ProbabilityField (для альтернативных сценариев)
3. Данных о рисках и новостях
"""

import asyncio
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime

from src.core.models import DataCard, NewsVector, RiskMetrics
from src.core.field import ProbabilityField, PredictionPoint
from src.risk.manager import RiskManager
from src.logic.matrix_analyzer import MatrixAnalyzer

logger = logging.getLogger(__name__)

@dataclass
class TradeScenario:
    """Полный сценарий сделки."""
    scenario_id: str
    timestamp: float
    symbol: str
    
    # Направление
    direction: str  # 'LONG', 'SHORT', 'WAIT'
    
    # Параметры входа
    entry_price: float
    quantity: float
    leverage: int
    
    # Выходы
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    
    # Умный трейлинг
    trail_enabled: bool = True
    trail_step: Optional[float] = None
    
    # Тип сценария (горизонт)
    scenario_type: str = "scalp"  # "scalp", "trap", "trend", "sideways"
    
    # Обоснование (метрики)
    confidence_score: float = 0.0  # Итоговая уверенность (0-1)
    risk_score: float = 0.0        # Оценка риска (0-1)
    news_impact: float = 0.0       # Влияние новостей (-1 до 1)
    
    # Метаданные
    reasoning: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.scenario_id:
            self.scenario_id = f"{self.symbol}_{self.direction}_{int(self.timestamp)}"


class ScenarioWriter:
    """
    Генератор торговых сценариев.
    Анализирует ВСЕ доступные данные:
    - Кластеры от MatrixAnalyzer (основной источник)
    - Отдельные точки из ProbabilityField (альтернативные сценарии)
    - Новости и риски
    """
    
    def __init__(self, config: Any, probability_field: ProbabilityField, risk_manager: RiskManager, matrix_analyzer: MatrixAnalyzer):
        # Конвертируем Pydantic модель в dict для совместимости
        if hasattr(config, 'model_dump'):
            self.config = config.model_dump()
        else:
            self.config = config
            
        self.field = probability_field
        self.risk_manager = risk_manager
        self.matrix_analyzer = matrix_analyzer
        
        # Настройки из конфига
        scenario_cfg = self.config.get('scenario', {})
        self.min_confidence = scenario_cfg.get('min_confidence', 0.65)
        
        exec_cfg = self.config.get('executor', {})
        self.default_leverage = exec_cfg.get('default_leverage', 3)
        self.max_leverage = self.config.get('risk', {}).get('max_leverage', 10)
        
        # Состояние
        self._scenario_count = 0
        self.active_scenarios: Dict[str, TradeScenario] = {}
        
        logger.info(f"ScenarioWriter initialized. Min confidence: {self.min_confidence}")

    async def analyze_market(self, symbol: str) -> Optional[TradeScenario]:
        """
        Основной метод анализа рынка и генерации сценария.
        1. Получает кластеры от MatrixAnalyzer
        2. Проверяет альтернативные точки из ProbabilityField
        3. Формирует сценарий с лучшим соотношением риск/прибыль
        """
        logger.debug(f"[{symbol}] Начинаем анализ рынка...")
        
        # 1. Получаем все точки из ProbabilityField
        points = self.field.points  # Прямой доступ к списку точек
        current_price = self.field.current_price or 0.0
        
        if not points or current_price == 0:
            logger.warning(f"[{symbol}] Нет данных в ProbabilityField")
            return None
        
        # 2. Запрашиваем у MatrixAnalyzer лучший кластер (кросс-валидация)
        cluster_result = self.matrix_analyzer.analyze(points, current_price)
        
        # 3. Если кластер не найден, пробуем найти отдельные сильные точки
        if not cluster_result:
            logger.debug(f"[{symbol}] Кластеры не найдены, ищем отдельные сигналы...")
            best_point = self._find_best_single_point(points, current_price)
            if not best_point:
                return None
            # Создаём псевдо-результат из одной точки
            cluster_result = {
                "target_price": best_point.price,
                "target_time_sec": best_point.time_sec,
                "probability": best_point.probability,
                "pattern_type": "single_signal",
                "confidence": best_point.probability,
                "metadata": {"source": best_point.source}
            }
        
        # 4. Извлекаем метрики
        target_price = cluster_result["target_price"]
        target_time = cluster_result["target_time_sec"]
        base_probability = cluster_result["probability"]
        pattern_type = cluster_result["pattern_type"]
        
        # 5. Определяем направление и тип сценария
        direction = "LONG" if target_price > current_price else "SHORT"
        scenario_type = self._map_pattern_to_scenario(pattern_type)
        
        logger.info(f"[{symbol}] Паттерн: {pattern_type}, Направление: {direction}, Цель: {target_price}, Время: {target_time}с")
        
        # 6. Рассчитываем влияние новостей
        card = await self.field.get_card(symbol)
        news_vectors = card.news_vectors if card else []
        news_impact_score = self._calculate_news_impact(news_vectors)
        
        # 7. Комбинируем вероятность с новостями
        combined_confidence = self._combine_confidence(base_probability, news_impact_score)
        
        logger.info(f"[{symbol}] Confidence: Base={base_probability:.2f}, News={news_impact_score:.2f}, Combined={combined_confidence:.2f}")
        
        # 8. Проверка порога уверенности
        if combined_confidence < self.min_confidence:
            logger.debug(f"[{symbol}] Уверенность ({combined_confidence:.2f}) ниже порога ({self.min_confidence})")
            return None
        
        # 9. Расчет параметров сделки
        entry_price = current_price
        leverage = self._calculate_leverage(combined_confidence, target_time)
        quantity = self._calculate_quantity(entry_price, leverage)
        stop_loss, take_profit = self._calculate_levels(direction, entry_price, target_price, target_time)
        
        # 10. Формирование сценария
        self._scenario_count += 1
        scenario = TradeScenario(
            scenario_id=f"SCN_{self._scenario_count}_{int(datetime.now().timestamp())}",
            timestamp=datetime.now().timestamp(),
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            quantity=quantity,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trail_step=abs(entry_price - stop_loss) / 2 if stop_loss else None,
            scenario_type=scenario_type,
            confidence_score=combined_confidence,
            risk_score=0.1,  # Заглушка, будет от RiskManager
            news_impact=news_impact_score,
            reasoning=[
                f"PatternType={pattern_type}",
                f"TargetPrice={target_price}",
                f"TargetTime={target_time}s",
                f"TunerConfidence={base_probability:.2f}",
                f"NewsImpact={news_impact_score:.2f}"
            ],
            metadata={
                'cluster_result': cluster_result,
                'points_count': len(points)
            }
        )
        
        logger.info(f"[{symbol}] Сценарий сформирован: {scenario.scenario_id} | {direction} @{entry_price} | Type={scenario_type}")
        return scenario
    
    def _find_best_single_point(self, points: List[PredictionPoint], current_price: float) -> Optional[PredictionPoint]:
        """Находит лучшую отдельную точку, если кластеры не найдены."""
        significant = [p for p in points if p.probability >= self.min_confidence]
        if not significant:
            return None
        # Возвращаем точку с максимальной вероятностью
        return max(significant, key=lambda p: p.probability)
    
    def _map_pattern_to_scenario(self, pattern_type: str) -> str:
        """Преобразует тип паттерна в тип сценария."""
        if "scalp" in pattern_type:
            return "scalp"
        elif "trap" in pattern_type:
            return "trap"
        elif "trend" in pattern_type:
            return "trend"
        elif "consolidation" in pattern_type or "sideways" in pattern_type:
            return "sideways"
        else:
            return "swing"
    
    def _calculate_news_impact(self, news_vectors: List[NewsVector]) -> float:
        """
        Рассчитывает суммарное влияние новостей (-1.0 ... 1.0).
        Учитывает время жизни новости и её вес.
        """
        if not news_vectors:
            return 0.0
            
        total_impact = 0.0
        weight_sum = 0.0
        now = datetime.now().timestamp()
        
        for vec in news_vectors:
            # Учитываем только свежие новости
            age = now - vec.timestamp
            if age > vec.duration_sec:
                continue
                
            # Вес уменьшается со временем
            time_weight = 1.0 - (age / vec.duration_sec)
            impact_weight = vec.strength * vec.probability * time_weight
            
            total_impact += vec.direction * impact_weight
            weight_sum += impact_weight
            
        if weight_sum == 0:
            return 0.0
            
        # Нормализация к [-1, 1]
        return max(-1.0, min(1.0, total_impact / weight_sum))

    def _combine_confidence(self, base_prob: float, news_impact: float) -> float:
        """
        Комбинирует базовую вероятность от матрицы и влияние новостей.
        Формула: Base + NewsBonus (до +/- 15%)
        """
        news_bonus = news_impact * 0.15
        combined = base_prob + news_bonus
        return max(0.0, min(1.0, combined))

    def _calculate_leverage(self, confidence: float, time_horizon: int) -> int:
        """
        Динамический расчет плеча на основе уверенности и горизонта.
        Краткосрочные сценарии -> выше плечо, долгосрочные -> ниже.
        """
        # Базовое плечо
        base_lev = self.default_leverage
        
        # Корректировка на уверенность (0.5x ... 1.5x)
        conf_factor = 0.5 + confidence
        
        # Корректировка на время (краткосрок -> выше плечо)
        if time_horizon < 60:
            time_factor = 1.2
        elif time_horizon > 300:
            time_factor = 0.8
        else:
            time_factor = 1.0
        
        final_lev = base_lev * conf_factor * time_factor
        
        # Ограничения
        calculated = int(max(1, min(self.max_leverage, final_lev)))
        
        logger.debug(f"Расчет плеча: Base={base_lev}, Conf={conf_factor:.2f}, Time={time_factor:.2f} -> {calculated}")
        return calculated

    def _calculate_quantity(self, price: float, leverage: int) -> float:
        """
        Расчет количества монет для позиции.
        Использует лимиты экспозиции из конфига.
        """
        # Не рискуем более чем X% от доступной маржи
        risk_per_trade_pct = self.config.get('risk', {}).get('margin_per_trade', 0.1)  # 10%
        
        # Доступная маржа (заглушка, потом брать из Executor)
        available_margin = 1000.0  # USDT
        trade_margin = available_margin * risk_per_trade_pct
        
        if price <= 0:
            return 0.0
            
        # Объем = (Маржа * Плечо) / Цена
        quantity = (trade_margin * leverage) / price
        
        return round(quantity, 1)

    def _calculate_levels(self, direction: str, entry: float, target: float, time_sec: int) -> tuple:
        """
        Расчет уровней Stop-Loss и Take-Profit.
        StopLoss = середина между входом и целью (R:R минимум 1:1)
        TakeProfit = цель или чуть раньше для фиксации
        """
        # Расстояние до цели
        target_distance = abs(target - entry)
        
        if direction == 'LONG':
            # Стоп посередине или ближе к входу для безопасности
            stop_loss = entry - (target_distance * 0.5)
            # Тейк = цель или чуть раньше
            take_profit = target * 0.995  # Фиксируем чуть раньше цели
        else:
            stop_loss = entry + (target_distance * 0.5)
            take_profit = target * 1.005
        
        # Округление
        precision = 4 if entry < 1 else 2
        stop_loss = round(stop_loss, precision)
        take_profit = round(take_profit, precision)
        
        # Проверка: стоп не должен быть дальше цели
        if direction == 'LONG' and stop_loss >= entry:
            stop_loss = entry * 0.99
        if direction == 'SHORT' and stop_loss <= entry:
            stop_loss = entry * 1.01
        
        return stop_loss, take_profit

    async def validate_and_submit(self, scenario: TradeScenario, executor_callback) -> bool:
        """
        Отправляет сценарий на проверку в RiskManager и далее в Executor.
        """
        logger.info(f"[{scenario.symbol}] Отправка сценария {scenario.scenario_id} на валидацию...")
        
        # 1. Валидация в Риск-менеджере
        is_valid, reason = await self.risk_manager.validate_scenario(
            scenario.symbol, 
            {
                'leverage': scenario.leverage,
                'quantity': scenario.quantity,
                'price': scenario.entry_price,
                'stop_loss': scenario.stop_loss
            }
        )
        
        if not is_valid:
            logger.warning(f"[{scenario.symbol}] Сценарий отклонен RiskManager: {reason}")
            return False
            
        logger.info(f"[{scenario.symbol}] Сценарий одобрен. Отправка в Executor...")
        
        # 2. Сохраняем активный сценарий
        self.active_scenarios[scenario.scenario_id] = scenario
        
        # 3. Регистрация в RiskManager для отслеживания
        self.risk_manager.register_scenario(scenario.symbol, {
            'entry_price': scenario.entry_price,
            'stop_loss': scenario.stop_loss,
            'side': scenario.direction
        })
        
        # 4. Отправка в Executor
        if callable(executor_callback):
            command = {
                'action': 'OPEN',
                'side': scenario.direction,
                'quantity': scenario.quantity,
                'leverage': scenario.leverage,
                'stop_loss': scenario.stop_loss,
                'take_profit': scenario.take_profit
            }
            await executor_callback(scenario.symbol, command)
            
        return True

    async def update_trail(self, symbol: str, current_price: float, executor_callback):
        """
        Обновление траектории стоп-лосса для активной сделки.
        Вызывается циклически при изменении цены.
        """
        # Ищем активный сценарий по символу
        active = None
        for scn in self.active_scenarios.values():
            if scn.symbol == symbol and scn.trail_enabled:
                active = scn
                break
                
        if not active:
            return
            
        # Логика трейлинга
        new_stop = None
        if active.direction == 'LONG':
            potential_stop = current_price - (active.trail_step or 0)
            if potential_stop > (active.stop_loss or 0):
                new_stop = potential_stop
        else:
            potential_stop = current_price + (active.trail_step or 0)
            if active.stop_loss is None or potential_stop < active.stop_loss:
                new_stop = potential_stop
                
        if new_stop:
            old_stop = active.stop_loss
            active.stop_loss = round(new_stop, 4)
            logger.info(f"[{symbol}] Trailing Stop updated: {old_stop} -> {active.stop_loss}")
            
            # Отправка команды на обновление
            if callable(executor_callback):
                command = {
                    'action': 'UPDATE_STOP',
                    'stop_loss': active.stop_loss
                }
                await executor_callback(symbol, command)
