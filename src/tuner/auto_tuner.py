"""
Tuner Module: Auto Tuner (Momentum Instant Update)
Мгновенная обработка снимков сделок. Без накопления.
Обновляет веса анализаторов и настройки конфига с плавностью 0.5%.
Записывает изменения в config.yaml и .brain.
"""
import yaml
from pathlib import Path
from typing import Dict, Any, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from src.storage.sqlite_manager import DatabaseManager

class AutoTuner:
    """
    Мгновенный адаптивный тюнер.
    Обрабатывает каждый снимок сделки сразу после закрытия.
    """
    def __init__(self, config_path: str, db_manager: 'DatabaseManager'):
        self.config_path = Path(config_path)
        self.db = db_manager
        self.config = self._load_config()
        
        # Настройки плавности (максимальное изменение за шаг)
        self.max_step_pct = 0.005  # 0.5%
        
        # Идеальные метрики для сравнения
        self.target_pnl = 1.0  # 100%
        self.target_wr = 1.0   # 100%
        self.target_dd = 0.0   # 0%

    def _load_config(self) -> dict:
        """Загрузка актуального конфига."""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"[AutoTuner] Ошибка загрузки конфига: {e}")
            return {}

    def _save_config(self):
        """Сохранение конфига на диск."""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(self.config, f, default_flow_style=False, allow_unicode=True)
            print("[AutoTuner] Конфиг обновлен на диске.")
        except Exception as e:
            print(f"[AutoTuner] Ошибка сохранения конфига: {e}")

    def process_snapshot(self, snapshot: dict):
        """
        ГЛАВНЫЙ МЕТОД: Мгновенная обработка снимка.
        1. Сравнивает результат с идеалом.
        2. Корректирует веса и ВСЕ настраиваемые параметры из конфига.
        3. Применяет ограничение шага (0.5%).
        4. Сохраняет в конфиг и БД.
        """
        if not snapshot:
            return

        # 1. Анализ отклонения от идеала
        pnl = snapshot.get('pnl_percent', 0) / 100.0  # нормализация
        is_win = 1 if pnl > 0 else 0
        dd = snapshot.get('drawdown_max', 0) / 100.0
        
        # Вычисляем "ошибку" (чем дальше от идеала, тем сильнее коррекция)
        error_score = (self.target_pnl - pnl) + (self.target_wr - is_win) + (dd - self.target_dd)
        
        # Направление корректировки: прибыль -> усиливаем, убыток -> ослабляем
        direction = 1 if pnl > 0 else -1
        
        # 2. Коррекция весов анализаторов
        analyzer_scores = snapshot.get('analyzer_scores', {})
        
        updated_weights = {}
        # Путь к весам в конфиге
        if 'analysis' not in self.config:
            self.config['analysis'] = {}
        if 'weights' not in self.config['analysis']:
            self.config['analysis']['weights'] = {}
            
        current_weights = self.config['analysis']['weights']
        
        for name, score in analyzer_scores.items():
            if score == 0:
                continue
                
            current_weight = current_weights.get(name, 0.5)
            
            # Расчет дельты: знак прибыли * сила сигнала * шаг
            delta = direction * abs(score) * self.max_step_pct
            
            new_weight = current_weight + delta
            
            # Ограничение диапазона [0.1, 1.0]
            new_weight = max(0.1, min(1.0, new_weight))
            
            updated_weights[name] = new_weight

        # Нормализация весов (сумма = 1.0)
        total = sum(updated_weights.values())
        if total > 0:
            for name in updated_weights:
                updated_weights[name] /= total
                self.config['analysis']['weights'][name] = round(updated_weights[name], 4)

        # 3. Коррекция ВСЕХ настраиваемых параметров из TUNABLE_PARAMS
        # Используем секцию TUNABLE_PARAMS из конфига как шаблон
        tunable_params = self.config.get('TUNABLE_PARAMS', {})
        
        # --- Risk параметры ---
        if 'risk' in tunable_params and 'risk' not in self.config:
            self.config['risk'] = {}
        
        risk_tunable = tunable_params.get('risk', {})
        risk_current = self.config.get('risk', {})
        
        # Параметры риска для обновления
        risk_param_map = {
            'risk_per_trade_pct': (0.1, 2.0),      # (min, max)
            'max_position_size_pct': (0.33, 0.75),
            'high_confidence_threshold': (0.70, 0.95),
            'high_confidence_max_pct': (0.60, 0.80),
            'max_daily_loss_pct': (1.0, 5.0),
            'min_reward_ratio': (1.0, 3.0),
            'min_profit_threshold': (0.01, 0.2),
            'commission_buffer': (0.0001, 0.001),
            'slippage_buffer': (0.0001, 0.002),
        }
        
        for param_name, (min_val, max_val) in risk_param_map.items():
            if param_name in risk_tunable:
                current_val = risk_current.get(param_name, risk_tunable[param_name])
                
                # Адаптивная корректировка на основе PnL
                if pnl > 0:
                    # Увеличиваем агрессивность при прибыли
                    if param_name in ['risk_per_trade_pct', 'max_position_size_pct', 'high_confidence_max_pct']:
                        delta = current_val * self.max_step_pct
                    elif param_name in ['min_reward_ratio', 'min_profit_threshold']:
                        # Уменьшаем требования при прибыли
                        delta = -current_val * self.max_step_pct
                    else:
                        delta = 0
                else:
                    # Уменьшаем агрессивность при убытке
                    if param_name in ['risk_per_trade_pct', 'max_position_size_pct', 'high_confidence_max_pct']:
                        delta = -current_val * self.max_step_pct
                    elif param_name in ['min_reward_ratio', 'min_profit_threshold']:
                        # Повышаем требования при убытке
                        delta = current_val * self.max_step_pct
                    else:
                        delta = 0
                
                new_val = current_val + delta
                new_val = max(min_val, min(max_val, new_val))
                risk_current[param_name] = round(new_val, 6)
        
        self.config['risk'] = risk_current
        
        # --- Leverage параметры ---
        if 'leverage' in tunable_params and 'leverage' not in self.config:
            self.config['leverage'] = {}
        
        leverage_tunable = tunable_params.get('leverage', {})
        leverage_current = self.config.get('leverage', {})
        
        leverage_param_map = {
            'base_leverage': (1, 3),
            'max_dynamic_leverage': (5, 20),
            'volatility_adjustment_factor': (0.2, 0.8),
            'confidence_leverage_multiplier': (0.1, 0.5),
        }
        
        for param_name, (min_val, max_val) in leverage_param_map.items():
            if param_name in leverage_tunable:
                current_val = leverage_current.get(param_name, leverage_tunable[param_name])
                
                if pnl > 0:
                    # Увеличиваем плечо при прибыли
                    if param_name in ['base_leverage', 'max_dynamic_leverage', 'confidence_leverage_multiplier']:
                        delta = current_val * self.max_step_pct
                    else:
                        delta = 0
                else:
                    # Уменьшаем плечо при убытке
                    if param_name in ['base_leverage', 'max_dynamic_leverage', 'confidence_leverage_multiplier']:
                        delta = -current_val * self.max_step_pct
                    else:
                        delta = 0
                
                new_val = current_val + delta
                new_val = max(min_val, min(max_val, new_val))
                leverage_current[param_name] = round(new_val, 4)
        
        self.config['leverage'] = leverage_current
        
        # --- Trailing Stop параметры ---
        if 'trailing_stop' in tunable_params and 'trailing_stop' not in self.config:
            self.config['trailing_stop'] = {}
        
        trailing_tunable = tunable_params.get('trailing_stop', {})
        trailing_current = self.config.get('trailing_stop', {})
        
        trailing_param_map = {
            'atr_multiplier_base': (1.0, 3.0),
            'activation_pct': (0.2, 1.0),
            'breakeven_trigger_pct': (0.1, 0.5),
            'trail_step_pct': (0.1, 0.5),
            'trail_distance_atr_mult': (1.0, 2.5),
            'profit_lock_trigger_pct': (1.0, 5.0),
            'tight_stop_atr_mult': (0.5, 2.0),
        }
        
        for param_name, (min_val, max_val) in trailing_param_map.items():
            if param_name in trailing_tunable:
                current_val = trailing_current.get(param_name, trailing_tunable[param_name])
                
                # Корректировка на основе PnL
                if pnl > 0:
                    # Более агрессивный трейлинг при прибыли
                    if param_name in ['activation_pct', 'breakeven_trigger_pct']:
                        delta = -current_val * self.max_step_pct  # Раньше активируем
                    elif param_name in ['trail_step_pct']:
                        delta = current_val * self.max_step_pct  # Больше шаг
                    else:
                        delta = 0
                else:
                    # Более консервативный трейлинг при убытке
                    if param_name in ['activation_pct', 'breakeven_trigger_pct']:
                        delta = current_val * self.max_step_pct  # Позже активируем
                    elif param_name in ['trail_step_pct']:
                        delta = -current_val * self.max_step_pct  # Меньше шаг
                    else:
                        delta = 0
                
                new_val = current_val + delta
                new_val = max(min_val, min(max_val, new_val))
                trailing_current[param_name] = round(new_val, 4)
        
        self.config['trailing_stop'] = trailing_current
        
        # --- Analyzer параметры (веса и confidence_factor) ---
        if 'analyzers' in tunable_params:
            analyzers_tunable = tunable_params.get('analyzers', {})
            analyzers_current = self.config.get('analyzers', {})
            
            for analyzer_name, analyzer_cfg in analyzers_tunable.items():
                if analyzer_name not in analyzers_current:
                    analyzers_current[analyzer_name] = {}
                
                current_analyzer = analyzers_current[analyzer_name]
                
                # Обновление confidence_factor
                if 'confidence_factor' in analyzer_cfg:
                    current_cf = current_analyzer.get('confidence_factor', analyzer_cfg['confidence_factor'])
                    
                    if pnl > 0:
                        delta = current_cf * self.max_step_pct
                    else:
                        delta = -current_cf * self.max_step_pct
                    
                    new_cf = current_cf + delta
                    new_cf = max(0.05, min(0.5, new_cf))
                    current_analyzer['confidence_factor'] = round(new_cf, 4)
                
                # Обновление weight (если есть)
                if 'weight' in analyzer_cfg:
                    current_w = current_analyzer.get('weight', analyzer_cfg.get('weight', 1.0))
                    
                    if pnl > 0:
                        delta = current_w * self.max_step_pct
                    else:
                        delta = -current_w * self.max_step_pct
                    
                    new_w = current_w + delta
                    new_w = max(0.1, min(2.0, new_w))
                    current_analyzer['weight'] = round(new_w, 4)
            
            self.config['analyzers'] = analyzers_current

        # 4. Сохранение изменений в конфиг
        self._save_config()
        
        # 5. Запись решения в БД (.brain)
        decision_record = {
            'timestamp': datetime.now().isoformat(),
            'snapshot_id': snapshot.get('id'),
            'error_score': round(error_score, 4),
            'updated_weights': updated_weights,
            'new_risk_multiplier': self.config['risk'].get('dynamic_risk_multiplier', 1.0),
            'updated_params': {
                'risk': {k: v for k, v in self.config['risk'].items() if k != 'dynamic_risk_multiplier'},
                'leverage': self.config.get('leverage', {}),
                'trailing_stop': self.config.get('trailing_stop', {}),
            }
        }
        # Предполагаем, что у db_manager есть метод save_tuner_decision
        # Если нет - добавим заглушку или обработаем ошибку
        try:
            self.db.save_tuner_decision(decision_record)
        except AttributeError:
            print("[AutoTuner] Метод save_tuner_decision не найден в DB manager")
            
        print(f"[AutoTuner] Обработан снимок. Ошибка: {error_score:.4f}. Параметры обновлены.")
