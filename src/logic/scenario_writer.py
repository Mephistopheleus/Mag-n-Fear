"""
ScenarioWriter v2.0 - Генератор сценариев на основе единой модели рынка.

Принципы работы:
1. Получает от MarketSynthesizer полную модель рынка (тренд, уровни, настроение).
2. Генерирует сценарии для разных горизонтов (Scalp, Trap, Swing), согласованные с моделью.
3. Не принимает решений о входе, только формирует гипотезы "Что если?".
4. Каждый сценарий содержит: тип, направление, цену входа, цели, стоп, обоснование.
"""

import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class TradeScenario:
    """Структура торгового сценария."""
    scenario_id: str
    timestamp: float
    symbol: str
    strategy_type: str  # 'scalp', 'trap', 'swing'
    direction: str      # 'LONG', 'SHORT'
    entry_price: float
    target_price: float
    stop_loss: float
    confidence: float   # 0.0 - 1.0 (насколько сценарий соответствует модели рынка)
    time_horizon_sec: int
    reasoning: str      # Текстовое обоснование
    risk_reward_ratio: float
    
    # Данные анализаторов для обучения AutoTuner
    analyzer_trend_useful: bool = False
    analyzer_mean_reversion_useful: bool = False
    analyzer_order_flow_useful: bool = False
    analyzer_volatility_useful: bool = False
    analyzer_matrix_useful: bool = False
    
    analyzer_trend_confidence: float = 0.0
    analyzer_mean_reversion_confidence: float = 0.0
    analyzer_order_flow_confidence: float = 0.0
    analyzer_volatility_confidence: float = 0.0
    analyzer_matrix_confidence: float = 0.0
    
    # Данные о рыночных условиях
    market_trend: str = "NEUTRAL"
    market_volatility: float = 0.0
    market_volume: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class ScenarioWriter:
    def __init__(self, config: Dict[str, Any], prob_field, risk_manager, matrix_analyzer):
        self.config = config
        self.prob_field = prob_field
        self.risk_manager = risk_manager
        self.matrix_analyzer = matrix_analyzer
        self.symbol = config.get('symbol', 'DOGEUSDT')
        self.min_confidence = config.get('min_scenario_confidence', 0.6)
        
        # Параметры для разных стратегий
        self.strategies = {
            'scalp': {
                'min_rr': 1.5,
                'time_horizon': (60, 300),  # 1-5 минут
                'target_mult': (0.005, 0.015) # 0.5% - 1.5% движения
            },
            'trap': {
                'min_rr': 2.0,
                'time_horizon': (300, 900), # 5-15 минут
                'target_mult': (0.01, 0.03) # 1% - 3% движения (отскок)
            },
            'swing': {
                'min_rr': 2.5,
                'time_horizon': (900, 3600), # 15 мин - 1 час
                'target_mult': (0.02, 0.05) # 2% - 5% движения
            }
        }

    def generate_scenarios(self, market_model: Dict[str, Any], current_price: float) -> List[TradeScenario]:
        """
        Генерирует список сценариев на основе модели рынка от Synthesizer.
        
        Args:
            market_model: Словарь с данными от MarketSynthesizer:
                - trend: 'BULLISH', 'BEARISH', 'SIDEWAYS'
                - strength: 0.0 - 1.0
                - key_levels: {'support': [...], 'resistance': [...]}
                - sentiment: 0.0 - 1.0 (общее настроение)
                - volatility: текущая волатильность
            current_price: Текущая цена актива.
            
        Returns:
            Список объектов TradeScenario.
        """
        scenarios = []
        timestamp = datetime.now().timestamp()
        
        trend = market_model.get('trend', 'SIDEWAYS')
        strength = market_model.get('strength', 0.5)
        key_levels = market_model.get('key_levels', {'support': [], 'resistance': []})
        sentiment = market_model.get('sentiment', 0.5)
        volatility = market_model.get('volatility', 0.01)
        
        logger.info(f"Генерация сценариев для {self.symbol}. Тренд: {trend}, Сила: {strength:.2f}")

        # 1. Сценарии по тренду (Scalp и Swing)
        if trend != 'SIDEWAYS' and strength > 0.4:
            direction = 'LONG' if trend == 'BULLISH' else 'SHORT'
            
            # Скальпинг по тренду
            scalp_scen = self._create_trend_scenario(
                'scalp', direction, current_price, key_levels, volatility, strength, timestamp
            )
            if scalp_scen:
                scenarios.append(scalp_scen)
            
            # Свинг по тренду (если сила тренда высокая)
            if strength > 0.7:
                swing_scen = self._create_trend_scenario(
                    'swing', direction, current_price, key_levels, volatility, strength, timestamp
                )
                if swing_scen:
                    scenarios.append(swing_scen)

        # 2. Сценарии "Ловушка" (против текущего движения у уровней)
        trap_scenarios = self._create_trap_scenarios(current_price, key_levels, trend, strength, timestamp)
        scenarios.extend(trap_scenarios)

        # 3. Сценарии для боковика (если тренд слабый)
        if trend == 'SIDEWAYS' or strength < 0.3:
            range_scenarios = self._create_range_scenarios(current_price, key_levels, volatility, timestamp)
            scenarios.extend(range_scenarios)

        # ВАЖНО: Возвращаем ВСЕ сценарии, фильтрация будет в RiskManager
        # Это нужно для обучения AutoTuner на полных данных
        for scen in scenarios:
            min_rr_req = self.strategies[scen.strategy_type]['min_rr']
            if scen.confidence >= self.min_confidence and scen.risk_reward_ratio >= min_rr_req:
                logger.info(f"Сценарий принят: {scen.scenario_id} ({scen.strategy_type}) RR={scen.risk_reward_ratio:.2f}, confidence={scen.confidence:.2f}")
            else:
                logger.debug(f"Сценарий отклонен на уровне Writer: {scen.scenario_id} (низкая уверенность или плохой RR). confidence={scen.confidence:.2f}, RR={scen.risk_reward_ratio:.2f}, required_RR={min_rr_req:.2f}")

        return scenarios  # Возвращаем все сценарии для дальнейшей проверки в RiskManager

    def _create_trend_scenario(self, strat_type: str, direction: str, price: float, 
                               levels: Dict, vol: float, strength: float, ts: float) -> Optional[TradeScenario]:
        """Создает сценарий движения по тренду."""
        params = self.strategies[strat_type]
        
        if direction == 'LONG':
            nearest_res = self._find_nearest_level(levels.get('resistance', []), price, above=True)
            target_pct = self._calc_target_pct(params['target_mult'], strength)
            target = max(nearest_res, price * (1 + target_pct)) if nearest_res else price * (1 + target_pct)
            
            nearest_sup = self._find_nearest_level(levels.get('support', []), price, above=False)
            stop_pct = vol * 1.5
            stop = min(nearest_sup, price * (1 - stop_pct)) if nearest_sup else price * (1 - stop_pct)
            
            reasoning = f"Тренд {direction}. Сила {strength:.2f}. Цель на уровне {nearest_res or 'процентном'}."
        else:
            nearest_sup = self._find_nearest_level(levels.get('support', []), price, above=False)
            target_pct = self._calc_target_pct(params['target_mult'], strength)
            target = min(nearest_sup, price * (1 - target_pct)) if nearest_sup else price * (1 - target_pct)
            
            nearest_res = self._find_nearest_level(levels.get('resistance', []), price, above=True)
            stop_pct = vol * 1.5
            stop = max(nearest_res, price * (1 + stop_pct)) if nearest_res else price * (1 + stop_pct)
            
            reasoning = f"Тренд {direction}. Сила {strength:.2f}. Цель на уровне {nearest_sup or 'процентном'}."

        if stop == 0 or target == 0 or stop == price or target == price:
            return None

        rr = abs(target - price) / abs(price - stop)
        confidence = strength * 0.8 + 0.2
        
        # Заполняем данные анализаторов для обучения AutoTuner
        # Trend analyzer активен когда есть явный тренд
        trend_useful = strength > 0.6
        trend_confidence = strength
        
        # Mean reversion полезен в боковике или против сильного тренда
        mean_rev_useful = strength < 0.4 or (direction == 'SHORT' and strength > 0.8) or (direction == 'LONG' and strength > 0.8)
        mean_rev_confidence = 1.0 - strength if strength < 0.4 else 0.3
        
        # Order flow важен при пробоях уровней
        order_flow_useful = strength > 0.5
        order_flow_confidence = strength * 0.7
        
        # Volatility analyzer всегда полезен для расчета стопов
        vol_useful = True
        vol_confidence = 0.8
        
        # Matrix analyzer полезен при наличии кластеров
        matrix_useful = True
        matrix_confidence = 0.6
        
        return TradeScenario(
            scenario_id=f"{strat_type}_{direction}_{int(ts)}",
            timestamp=ts,
            symbol=self.symbol,
            strategy_type=strat_type,
            direction=direction,
            entry_price=price,
            target_price=target,
            stop_loss=stop,
            confidence=confidence,
            time_horizon_sec=int((params['time_horizon'][0] + params['time_horizon'][1]) / 2),
            reasoning=reasoning,
            risk_reward_ratio=rr,
            # Данные анализаторов
            analyzer_trend_useful=trend_useful,
            analyzer_mean_reversion_useful=mean_rev_useful,
            analyzer_order_flow_useful=order_flow_useful,
            analyzer_volatility_useful=vol_useful,
            analyzer_matrix_useful=matrix_useful,
            analyzer_trend_confidence=trend_confidence,
            analyzer_mean_reversion_confidence=mean_rev_confidence,
            analyzer_order_flow_confidence=order_flow_confidence,
            analyzer_volatility_confidence=vol_confidence,
            analyzer_matrix_confidence=matrix_confidence,
            # Рыночные условия
            market_trend=self._get_trend_from_direction(direction),
            market_volatility=vol,
            market_volume=1.0  # Заглушка, будет заменено на реальные данные
        )
    
    def _get_trend_from_direction(self, direction: str) -> str:
        """Преобразует направление сделки в тренд."""
        return "BULLISH" if direction == "LONG" else "BEARISH"

    def _create_trap_scenarios(self, price: float, levels: Dict, trend: str, strength: float, ts: float) -> List[TradeScenario]:
        """Создает сценарии ловушек у уровней."""
        scenarios = []
        params = self.strategies['trap']
        
        for level in levels.get('resistance', []):
            if 0 < (level - price) / price < 0.01:
                target = price * (1 - params['target_mult'][1])
                stop = level * 1.005
                rr = abs(target - price) / abs(stop - price)
                if rr >= params['min_rr']:
                    scenarios.append(TradeScenario(
                        scenario_id=f"trap_SHORT_{int(ts)}",
                        timestamp=ts, symbol=self.symbol, strategy_type='trap',
                        direction='SHORT', entry_price=price, target_price=target,
                        stop_loss=stop, confidence=0.75,
                        time_horizon_sec=600,
                        reasoning=f"Ловушка у сопротивления {level:.4f}.",
                        risk_reward_ratio=rr,
                        # Данные анализаторов для trap-сценариев
                        analyzer_trend_useful=False,  # Trap играет против тренда
                        analyzer_mean_reversion_useful=True,  # Mean reversion ключевой для trap
                        analyzer_order_flow_useful=True,  # Важен поток для подтверждения ложного пробоя
                        analyzer_volatility_useful=True,
                        analyzer_matrix_useful=True,
                        analyzer_trend_confidence=0.2,
                        analyzer_mean_reversion_confidence=0.85,
                        analyzer_order_flow_confidence=0.75,
                        analyzer_volatility_confidence=0.8,
                        analyzer_matrix_confidence=0.65,
                        market_trend="BEARISH" if trend == "BULLISH" else "BULLISH",  # Контр-тренд
                        market_volatility=params['target_mult'][1],
                        market_volume=1.2  # Повышенный объем у уровней
                    ))

        for level in levels.get('support', []):
            if 0 < (price - level) / price < 0.01:
                target = price * (1 + params['target_mult'][1])
                stop = level * 0.995
                rr = abs(target - price) / abs(price - stop)
                if rr >= params['min_rr']:
                    scenarios.append(TradeScenario(
                        scenario_id=f"trap_LONG_{int(ts)}",
                        timestamp=ts, symbol=self.symbol, strategy_type='trap',
                        direction='LONG', entry_price=price, target_price=target,
                        stop_loss=stop, confidence=0.75,
                        time_horizon_sec=600,
                        reasoning=f"Ловушка у поддержки {level:.4f}.",
                        risk_reward_ratio=rr,
                        # Данные анализаторов для trap-сценариев
                        analyzer_trend_useful=False,  # Trap играет против тренда
                        analyzer_mean_reversion_useful=True,  # Mean reversion ключевой для trap
                        analyzer_order_flow_useful=True,  # Важен поток для подтверждения ложного пробоя
                        analyzer_volatility_useful=True,
                        analyzer_matrix_useful=True,
                        analyzer_trend_confidence=0.2,
                        analyzer_mean_reversion_confidence=0.85,
                        analyzer_order_flow_confidence=0.75,
                        analyzer_volatility_confidence=0.8,
                        analyzer_matrix_confidence=0.65,
                        market_trend="BULLISH" if trend == "BEARISH" else "BEARISH",  # Контр-тренд
                        market_volatility=params['target_mult'][1],
                        market_volume=1.2  # Повышенный объем у уровней
                    ))
        return scenarios

    def _create_range_scenarios(self, price: float, levels: Dict, vol: float, ts: float) -> List[TradeScenario]:
        """Сценарии для бокового движения."""
        scenarios = []
        
        sup = self._find_nearest_level(levels.get('support', []), price, above=False)
        res = self._find_nearest_level(levels.get('resistance', []), price, above=True)
        
        if sup and (price - sup) / price < 0.02:
            target = (sup + res) / 2 if res else price * 1.01
            stop = sup * 0.995
            rr = abs(target - price) / abs(price - stop)
            if rr >= 1.5:
                scenarios.append(TradeScenario(
                    scenario_id=f"range_LONG_{int(ts)}",
                    timestamp=ts, symbol=self.symbol, strategy_type='scalp',
                    direction='LONG', entry_price=price, target_price=target,
                    stop_loss=stop, confidence=0.65,
                    time_horizon_sec=180,
                    reasoning=f"Боковик. Покупка у поддержки {sup:.4f}.",
                    risk_reward_ratio=rr,
                    # Данные анализаторов для range-сценариев
                    analyzer_trend_useful=False,  # Тренд не важен в боковике
                    analyzer_mean_reversion_useful=True,  # Mean reversion ключевой
                    analyzer_order_flow_useful=False,
                    analyzer_volatility_useful=True,
                    analyzer_matrix_useful=True,
                    analyzer_trend_confidence=0.1,
                    analyzer_mean_reversion_confidence=0.8,
                    analyzer_order_flow_confidence=0.3,
                    analyzer_volatility_confidence=0.7,
                    analyzer_matrix_confidence=0.6,
                    market_trend="SIDEWAYS",
                    market_volatility=vol * 0.8,  # В боковике волатильность ниже
                    market_volume=0.8  # Объем ниже в боковике
                ))
                
        if res and (res - price) / price < 0.02:
            target = (sup + res) / 2 if sup else price * 0.99
            stop = res * 1.005
            rr = abs(target - price) / abs(stop - price)
            if rr >= 1.5:
                scenarios.append(TradeScenario(
                    scenario_id=f"range_SHORT_{int(ts)}",
                    timestamp=ts, symbol=self.symbol, strategy_type='scalp',
                    direction='SHORT', entry_price=price, target_price=target,
                    stop_loss=stop, confidence=0.65,
                    time_horizon_sec=180,
                    reasoning=f"Боковик. Продажа у сопротивления {res:.4f}.",
                    risk_reward_ratio=rr,
                    # Данные анализаторов для range-сценариев
                    analyzer_trend_useful=False,  # Тренд не важен в боковике
                    analyzer_mean_reversion_useful=True,  # Mean reversion ключевой
                    analyzer_order_flow_useful=False,
                    analyzer_volatility_useful=True,
                    analyzer_matrix_useful=True,
                    analyzer_trend_confidence=0.1,
                    analyzer_mean_reversion_confidence=0.8,
                    analyzer_order_flow_confidence=0.3,
                    analyzer_volatility_confidence=0.7,
                    analyzer_matrix_confidence=0.6,
                    market_trend="SIDEWAYS",
                    market_volatility=vol * 0.8,  # В боковике волатильность ниже
                    market_volume=0.8  # Объем ниже в боковике
                ))
        return scenarios

    def _find_nearest_level(self, levels_list: List[float], price: float, above: bool) -> Optional[float]:
        """Находит ближайший уровень выше или ниже цены."""
        if not levels_list:
            return None
        filtered = [l for l in levels_list if (l > price if above else l < price)]
        if not filtered:
            return None
        return min(filtered) if above else max(filtered)

    def _calc_target_pct(self, mult_range: tuple, strength: float) -> float:
        """Расчет процента движения цели."""
        min_p, max_p = mult_range
        return min_p + (max_p - min_p) * strength
