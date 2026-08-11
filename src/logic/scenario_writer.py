"""
ScenarioWriter v3.0 - Конвейер генерации торговых идей.

Принципы работы:
1. Получает данные от ВСЕХ анализаторов (не через MarketSynthesizer, а напрямую из MatrixProbabilityField).
2. Генерирует ТОРГОВЫЕ ИДЕИ (TradeIdea) для всех возможных сценариев без фильтрации.
3. Не принимает решений о входе, только формирует гипотезы "Что если?" с полной геометрией.
4. Каждый сценарий содержит: тип (ScenarioType), направление, цену входа, цели, стоп, обоснование, рекомендуемое плечо.
5. Помечает каждый сценарий тегом scenario_type для обучения AutoTuner.
6. Передает ВСЕ идеи в RiskManager для фильтрации и выбора лучших.
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from src.core.trade_idea import TradeIdea, ScenarioType
from src.matrix.probability_field import MatrixProbabilityField

logger = logging.getLogger(__name__)


class ScenarioWriter:
    """
    Конвейер генерации торговых идей.
    Работает напрямую с MatrixProbabilityField и данными анализаторов.
    """
    def __init__(self, config: Dict[str, Any], prob_field: MatrixProbabilityField):
        self.config = config
        self.prob_field = prob_field
        self.symbol = config.get('symbol', 'BTCUSDT')
        
        # Параметры для разных типов сценариев (настраиваемые через TUNABLE_PARAMS)
        self.scenario_params = {
            ScenarioType.SCALPING_LONG: {'min_rr': 1.5, 'time_horizon': (60, 300), 'target_move_pct': (0.005, 0.015)},
            ScenarioType.SCALPING_SHORT: {'min_rr': 1.5, 'time_horizon': (60, 300), 'target_move_pct': (0.005, 0.015)},
            ScenarioType.BREAKOUT_LONG: {'min_rr': 2.0, 'time_horizon': (300, 900), 'target_move_pct': (0.01, 0.03)},
            ScenarioType.BREAKOUT_SHORT: {'min_rr': 2.0, 'time_horizon': (300, 900), 'target_move_pct': (0.01, 0.03)},
            ScenarioType.PULLBACK_LONG: {'min_rr': 2.0, 'time_horizon': (180, 600), 'target_move_pct': (0.008, 0.02)},
            ScenarioType.PULLBACK_SHORT: {'min_rr': 2.0, 'time_horizon': (180, 600), 'target_move_pct': (0.008, 0.02)},
            ScenarioType.REVERSAL_LONG: {'min_rr': 2.5, 'time_horizon': (600, 1800), 'target_move_pct': (0.02, 0.05)},
            ScenarioType.REVERSAL_SHORT: {'min_rr': 2.5, 'time_horizon': (600, 1800), 'target_move_pct': (0.02, 0.05)},
            ScenarioType.LIQUIDITY_RUN: {'min_rr': 3.0, 'time_horizon': (900, 2700), 'target_move_pct': (0.03, 0.08)},
            ScenarioType.MEAN_REVERSION: {'min_rr': 2.0, 'time_horizon': (300, 1200), 'target_move_pct': (0.01, 0.025)},
            ScenarioType.ARBITRAGE_CORRELATION: {'min_rr': 1.8, 'time_horizon': (120, 600), 'target_move_pct': (0.005, 0.012)},
        }
        
        logger.info("ScenarioWriter v3.0 инициализирован. Режим: КОНВЕЙЕР ИДЕЙ")

    def generate_all_ideas(self, current_price: float, market_context: Dict[str, Any]) -> List[TradeIdea]:
        """
        Генерирует ВСЕ возможные торговые идеи на основе данных из MatrixProbabilityField.
        Не фильтрует идеи - это задача RiskManager.
        """
        ideas = []
        timestamp = datetime.now().timestamp()
        
        logger.info(f"Генерация идей для {self.symbol}. Цена: {current_price:.4f}")
        
        # Получаем данные из MatrixProbabilityField напрямую
        field_data = self.prob_field.get_latest_snapshot()
        
        # 1. Генерируем идеи на основе паттернов
        pattern_ideas = self._generate_pattern_ideas(field_data, current_price, timestamp)
        ideas.extend(pattern_ideas)
        
        # 2. Генерируем идеи на основе ликвидности
        liquidity_ideas = self._generate_liquidity_ideas(field_data, current_price, timestamp)
        ideas.extend(liquidity_ideas)
        
        # 3. Генерируем идеи на основе тренда
        trend_ideas = self._generate_trend_ideas(field_data, current_price, timestamp)
        ideas.extend(trend_ideas)
        
        # 4. Генерируем идеи на основе возврата к среднему
        mean_rev_ideas = self._generate_mean_reversion_ideas(field_data, current_price, timestamp)
        ideas.extend(mean_rev_ideas)
        
        # 5. Генерируем идеи на основе корреляций
        arb_ideas = self._generate_arbitrage_ideas(field_data, current_price, timestamp)
        ideas.extend(arb_ideas)
        
        logger.info(f"Сгенерировано {len(ideas)} торговых идей. Передача в RiskManager.")
        return ideas

    def _generate_pattern_ideas(self, field_data: Dict, price: float, ts: float) -> List[TradeIdea]:
        """Генерирует идеи на основе графических паттернов."""
        ideas = []
        patterns = field_data.get('graph_patterns', [])
        
        for pattern in patterns:
            pattern_type = pattern.get('type', '')
            confidence = pattern.get('confidence', 0.5)
            breakout_direction = pattern.get('breakout_direction', 0)
            target_price = pattern.get('target_price', price)
            neckline = pattern.get('neckline', price)
            
            if breakout_direction > 0:
                scenario_type = ScenarioType.BREAKOUT_LONG if confidence > 0.7 else ScenarioType.PULLBACK_LONG
                idea = self._create_idea(scenario_type, 1, price, neckline * 0.995, target_price, 
                                        target_price, 600, confidence, f"Паттерн {pattern_type}: пробой вверх", 
                                        [f"pattern_{pattern_type}", "breakout"], ts)
                if idea: ideas.append(idea)
            elif breakout_direction < 0:
                scenario_type = ScenarioType.BREAKOUT_SHORT if confidence > 0.7 else ScenarioType.PULLBACK_SHORT
                idea = self._create_idea(scenario_type, -1, price, neckline * 1.005, target_price,
                                        target_price, 600, confidence, f"Паттерн {pattern_type}: пробой вниз",
                                        [f"pattern_{pattern_type}", "breakdown"], ts)
                if idea: ideas.append(idea)
        return ideas

    def _generate_liquidity_ideas(self, field_data: Dict, price: float, ts: float) -> List[TradeIdea]:
        """Генерирует идеи на основе карты ликвидности."""
        ideas = []
        liq_data = field_data.get('liquidity_map', {})
        poc_price = liq_data.get('poc_price')
        zones = liq_data.get('zones', [])
        
        if not poc_price:
            return ideas
        
        dist_to_poc = abs(price - poc_price) / price
        if dist_to_poc > 0.002:
            direction = 1 if price < poc_price else -1
            scenario_type = ScenarioType.LIQUIDITY_RUN
            stop_mult = 0.99 if direction == 1 else 1.01
            idea = self._create_idea(scenario_type, direction, price, price * stop_mult, poc_price,
                                    poc_price, 900, 0.65, f"Движение к POC ({poc_price:.4f})",
                                    ["liquidity_run", "poc_magnet"], ts)
            if idea: ideas.append(idea)
        
        for zone in zones:
            if zone.get('type') == 'void' and zone.get('strength', 0) > 0.5:
                void_low, void_high = zone.get('low', price), zone.get('high', price)
                if price > void_high:
                    idea = self._create_idea(ScenarioType.BREAKOUT_SHORT, -1, price, void_high * 1.01, void_low,
                                            void_low, 300, 0.6, f"Пробой вакуума вниз до {void_low:.4f}",
                                            ["void_breakdown"], ts)
                    if idea: ideas.append(idea)
                elif price < void_low:
                    idea = self._create_idea(ScenarioType.BREAKOUT_LONG, 1, price, void_low * 0.99, void_high,
                                            void_high, 300, 0.6, f"Пробой вакуума вверх до {void_high:.4f}",
                                            ["void_breakout"], ts)
                    if idea: ideas.append(idea)
        return ideas

    def _generate_trend_ideas(self, field_data: Dict, price: float, ts: float) -> List[TradeIdea]:
        """Генерирует идеи на основе трендовых анализаторов."""
        ideas = []
        trend_data = field_data.get('g_trend', {})
        trend_direction = trend_data.get('direction', 'NEUTRAL')
        trend_strength = trend_data.get('strength', 0.5)
        channel_lower = trend_data.get('lower_bound', price * 0.99)
        channel_upper = trend_data.get('upper_bound', price * 1.01)
        
        if trend_direction == 'UP' and trend_strength > 0.6:
            scenario_type = ScenarioType.PULLBACK_LONG if price > channel_lower * 1.005 else ScenarioType.SCALPING_LONG
            idea = self._create_idea(scenario_type, 1, price, channel_lower * 0.995, channel_upper,
                                    channel_upper, 400, trend_strength, f"Тренд вверх (сила {trend_strength:.2f})",
                                    ["trend_following", "channel_bounce"], ts)
            if idea: ideas.append(idea)
        elif trend_direction == 'DOWN' and trend_strength > 0.6:
            scenario_type = ScenarioType.PULLBACK_SHORT if price < channel_upper * 0.995 else ScenarioType.SCALPING_SHORT
            idea = self._create_idea(scenario_type, -1, price, channel_upper * 1.005, channel_lower,
                                    channel_lower, 400, trend_strength, f"Тренд вниз (сила {trend_strength:.2f})",
                                    ["trend_following", "channel_rejection"], ts)
            if idea: ideas.append(idea)
        return ideas

    def _generate_mean_reversion_ideas(self, field_data: Dict, price: float, ts: float) -> List[TradeIdea]:
        """Генерирует идеи на основе возврата к среднему."""
        ideas = []
        mr_data = field_data.get('mean_reversion', {})
        rsi = mr_data.get('rsi', 50)
        bb_upper = mr_data.get('bb_upper', price * 1.02)
        bb_lower = mr_data.get('bb_lower', price * 0.98)
        bb_middle = mr_data.get('bb_middle', price)
        
        if rsi < 30:
            idea = self._create_idea(ScenarioType.MEAN_REVERSION, 1, price, bb_lower * 0.99, bb_middle,
                                    bb_middle, 600, 0.7, f"Перепроданность (RSI={rsi:.1f})",
                                    ["mean_reversion", "oversold"], ts)
            if idea: ideas.append(idea)
        elif rsi > 70:
            idea = self._create_idea(ScenarioType.MEAN_REVERSION, -1, price, bb_upper * 1.01, bb_middle,
                                    bb_middle, 600, 0.7, f"Перекупленность (RSI={rsi:.1f})",
                                    ["mean_reversion", "overbought"], ts)
            if idea: ideas.append(idea)
        return ideas

    def _generate_arbitrage_ideas(self, field_data: Dict, price: float, ts: float) -> List[TradeIdea]:
        """Генерирует идеи на основе корреляций."""
        ideas = []
        corr_data = field_data.get('correlations', {})
        btc_corr = corr_data.get('BTCUSDT', {})
        coeff = btc_corr.get('coefficient', 0.8)
        divergence = btc_corr.get('divergence_pct', 0.0)
        
        if abs(divergence) > 0.01 and coeff > 0.7:
            direction = 1 if divergence > 0 else -1
            tp_mult = 1.015 if direction > 0 else 0.985
            stop_mult = 0.99 if direction > 0 else 1.01
            idea = self._create_idea(ScenarioType.ARBITRAGE_CORRELATION, direction, price, price * stop_mult,
                                    price * tp_mult, price * tp_mult, 300, 0.65,
                                    f"Корреляционный арбитраж с BTC ({divergence*100:.2f}%)",
                                    ["correlation_arb", "btc_pair"], ts)
            if idea: ideas.append(idea)
        return ideas

    def _create_idea(self, scenario_type: ScenarioType, direction: int, entry: float,
                     stop: float, tp: float, target_price: float, target_time_sec: int,
                     confidence: float, reason: str, logic_tags: List[str], timestamp: float) -> Optional[TradeIdea]:
        """Создает объект TradeIdea с расчетом рекомендуемого плеча."""
        params = self.scenario_params.get(scenario_type, {'min_rr': 1.5})
        min_rr = params['min_rr']
        
        risk = (entry - stop) if direction == 1 else (stop - entry)
        reward = (tp - entry) if direction == 1 else (entry - tp)
        
        if risk <= 0 or reward <= 0:
            return None
        
        rr = reward / risk
        if rr < min_rr:
            return None
        
        dist_to_sl_pct = abs(risk / entry * 100)
        recommended_leverage = min(10.0, 2.0 / dist_to_sl_pct) if dist_to_sl_pct > 0 else 1.0
        
        return TradeIdea(
            symbol=self.symbol,
            scenario_type=scenario_type,
            direction=direction,
            entry_price=entry,
            stop_loss_price=stop,
            take_profit_price=tp,
            target_price=target_price,
            target_time_sec=target_time_sec,
            expected_volatility=0.02,
            confidence=confidence,
            logic_tags=logic_tags,
            reason=reason,
            recommended_leverage=recommended_leverage,
            matrix_snapshot={}
        )
