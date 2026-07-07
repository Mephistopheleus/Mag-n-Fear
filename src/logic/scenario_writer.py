"""
Scenario Writer Module.
Центральный узел принятия решений.
Формирует полный сценарий сделки на основе агрегированных данных из ProbabilityField.
"""

import asyncio
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime

from src.core.models import DataCard, NewsVector, RiskMetrics
from src.core.field import ProbabilityField
from src.risk.manager import RiskManager

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
    Анализирует ВСЕ доступные данные из ProbabilityField и формирует план действий.
    """
    
    def __init__(self, config: Any, probability_field: ProbabilityField, risk_manager: RiskManager):
        # Конвертируем Pydantic модель в dict для совместимости
        if hasattr(config, 'model_dump'):
            self.config = config.model_dump()
        else:
            self.config = config
            
        self.field = probability_field
        self.risk_manager = risk_manager
        
        # Настройки из конфига
        scenario_cfg = self.config.get('scenario', {})
        self.min_confidence = scenario_cfg.get('min_confidence', 0.65)
        # Лимит сценариев удален - используется только проверка маржи в RiskManager
        
        exec_cfg = self.config.get('executor', {})
        self.default_leverage = exec_cfg.get('default_leverage', 3)
        self.max_leverage = self.config.get('risk', {}).get('max_leverage', 10)
        
        # Состояние
        self._scenario_count = 0
        self._last_scenario_time = 0
        self.active_scenarios: Dict[str, TradeScenario] = {}
        
        logger.info(f"ScenarioWriter initialized. Min confidence: {self.min_confidence}")

    async def analyze_market(self, symbol: str) -> Optional[TradeScenario]:
        """
        Основной метод анализа рынка и генерации сценария.
        Читает данные из ProbabilityField и формирует сценарий.
        """
        logger.debug(f"[{symbol}] Начинаем анализ рынка...")
        
        # 1. Получаем свежие данные из поля вероятностей
        card = await self.field.get_card(symbol)
        if not card:
            logger.warning(f"[{symbol}] Нет данных в ProbabilityField")
            return None
            
        # 2. Извлекаем метрики
        tech_confidence = card.tuner_confidence  # Доверие от AutoTuner к стратегии
        risk_metrics = card.risk_metrics or RiskMetrics(
            max_leverage=self.max_leverage,
            liquidity_risk=0.5,
            drawdown_prob=0.1,
            volatility_index=0.02,
            exposure_limit=0.05,
            is_emergency=False
        )
        news_vectors = card.news_vectors or []
        
        # 3. Рассчитываем агрегированную уверенность
        news_impact_score = self._calculate_news_impact(news_vectors)
        combined_confidence = self._combine_confidence(tech_confidence, news_impact_score, risk_metrics)
        
        logger.info(f"[{symbol}] Confidence: Tech={tech_confidence:.2f}, News={news_impact_score:.2f}, Combined={combined_confidence:.2f}")
        
        # 4. Проверка порога уверенности
        if combined_confidence < self.min_confidence:
            logger.debug(f"[{symbol}] Уверенность ({combined_confidence:.2f}) ниже порога ({self.min_confidence})")
            return None
        
        # 5. Проверка лимита удалена - ограничение только по марже в RiskManager
            
        # 6. Определение направления и точки входа
        direction, entry_price = self._determine_direction_and_entry(card)
        if not direction or direction == 'WAIT':
            logger.debug(f"[{symbol}] Нет четкого направления для входа")
            return None
            
        # 7. Расчет параметров сделки (объем, плечо, стопы)
        leverage = self._calculate_leverage(risk_metrics, combined_confidence)
        quantity = self._calculate_quantity(entry_price, leverage, risk_metrics)
        stop_loss, take_profit = self._calculate_levels(direction, entry_price, card, risk_metrics)
        
        # 8. Формирование сценария
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
            confidence_score=combined_confidence,
            risk_score=risk_metrics.drawdown_prob,
            news_impact=news_impact_score,
            reasoning=[
                f"TunerConfidence={tech_confidence:.2f}",
                f"NewsImpact={news_impact_score:.2f}",
                f"Volatility={risk_metrics.volatility_index:.4f}",
                f"LiquidityRisk={risk_metrics.liquidity_risk:.2f}"
            ],
            metadata={
                'card_timestamp': card.timestamp,
                'risk_metrics': risk_metrics.__dict__ if risk_metrics else {}
            }
        )
        
        logger.info(f"[{symbol}] Сценарий сформирован: {scenario.scenario_id} | {direction} @{entry_price} | Qty={quantity} | Lev={leverage}")
        return scenario

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

    def _combine_confidence(self, tech_conf: float, news_impact: float, risk_metrics: RiskMetrics) -> float:
        """
        Комбинирует техническую уверенность, новости и риски в итоговый скор.
        Формула: Tech + NewsBonus - RiskPenalty
        """
        # Штраф за риск (до 20%)
        risk_penalty = risk_metrics.drawdown_prob * 0.2
        
        # Бонус/штраф от новостей (до +/- 15%)
        news_bonus = news_impact * 0.15
        
        combined = tech_conf + news_bonus - risk_penalty
        return max(0.0, min(1.0, combined))

    def _determine_direction_and_entry(self, card: DataCard) -> tuple:
        """
        Определяет направление сделки и цену входа.
        Использует данные из math_surfaces и агрегированный сентимент.
        """
        current_price = card.price
        if current_price <= 0:
            return None, None
            
        # Получаем агрегированный сентимент
        sentiment = card.get_aggregated_sentiment()
        
        # Проверяем технические индикаторы (если есть в math_surfaces)
        # Пример: если есть RSI или другие сигналы
        tech_signal = 0.0
        if 'rsi' in card.math_surfaces:
            rsi = card.math_surfaces['rsi']
            if rsi < 30:
                tech_signal = 0.5  # Перепроданность -> LONG
            elif rsi > 70:
                tech_signal = -0.5  # Перекупленность -> SHORT
        
        # Комбинируем сентимент и технику
        combined_signal = sentiment + tech_signal
        
        threshold = 0.2  # Минимальный перевес для входа
        
        if combined_signal > threshold:
            return 'LONG', current_price
        elif combined_signal < -threshold:
            return 'SHORT', current_price
            
        return 'WAIT', current_price

    def _calculate_leverage(self, risk_metrics: RiskMetrics, confidence: float) -> int:
        """
        Динамический расчет плеча на основе рисков и уверенности.
        """
        # Базовое плечо
        base_lev = self.default_leverage
        
        # Корректировка на риск
        risk_factor = 1.0 - risk_metrics.drawdown_prob
        adjusted_lev = base_lev * risk_factor
        
        # Корректировка на уверенность
        conf_factor = 0.5 + confidence  # От 0.5 до 1.5
        final_lev = adjusted_lev * conf_factor
        
        # Ограничения
        max_allowed = min(self.max_leverage, risk_metrics.max_leverage)
        calculated = int(max(1, min(max_allowed, final_lev)))
        
        logger.debug(f"Расчет плеча: Base={base_lev}, RiskFactor={risk_factor:.2f}, Conf={conf_factor:.2f} -> {calculated}")
        return calculated

    def _calculate_quantity(self, price: float, leverage: int, risk_metrics: RiskMetrics) -> float:
        """
        Расчет количества монет для позиции.
        Использует лимиты экспозиции от RiskManager.
        """
        # Не рискуем более чем X% от доступной маржи
        risk_per_trade_pct = self.config.get('risk', {}).get('margin_per_trade', 0.1)  # 10%
        
        # Доступная маржа (пока заглушка, потом брать из Executor/Binance)
        available_margin = 1000.0  # USDT (заглушка)
        trade_margin = available_margin * risk_per_trade_pct
        
        if price <= 0:
            return 0.0
            
        # Объем = (Маржа * Плечо) / Цена
        quantity = (trade_margin * leverage) / price
        
        # Округление
        return round(quantity, 1)

    def _calculate_levels(self, direction: str, entry: float, card: DataCard, risk_metrics: RiskMetrics) -> tuple:
        """
        Расчет уровней Stop-Loss и Take-Profit.
        Использует волатильность (ATR подход).
        """
        volatility = risk_metrics.volatility_index or 0.02
        
        # Стоп на основе волатильности (1.5 ATR)
        stop_distance = entry * (volatility * 1.5)
        
        if direction == 'LONG':
            stop_loss = entry - stop_distance
            take_profit = entry + (stop_distance * 2.5)  # R:R 1:2.5
        else:
            stop_loss = entry + stop_distance
            take_profit = entry - (stop_distance * 2.5)
            
        # Округление
        precision = 4 if entry < 1 else 2
        stop_loss = round(stop_loss, precision)
        take_profit = round(take_profit, precision)
        
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
        
        # 3. Регистрация в RiskManager для теневого отслеживания
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
