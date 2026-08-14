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
        # ВЕСА ЧИТАЕМ И ЗАПИСЫВАЕМ ВНУТРЬ TUNABLE_PARAMS['analyzers']
        analyzer_scores = snapshot.get('analyzer_scores', {})
        
        tunable_params = self.config.get('TUNABLE_PARAMS', {})
        analyzers_tunable = tunable_params.get('analyzers', {})
        
        updated_weights = {}
        
        for name, score in analyzer_scores.items():
            if score == 0:
                continue
            
            # Получаем текущий weight из TUNABLE_PARAMS['analyzers'][name]
            if name not in analyzers_tunable:
                analyzers_tunable[name] = {'weight': 0.5, 'confidence_factor': 0.1}
            
            current_weight = analyzers_tunable[name].get('weight', 0.5)
            
            # Расчет дельты: знак прибыли * сила сигнала * шаг
            delta = direction * abs(score) * self.max_step_pct
            
            new_weight = current_weight + delta
            
            # Ограничение диапазона [0.1, 1.0]
            new_weight = max(0.1, min(1.0, new_weight))
            
            analyzers_tunable[name]['weight'] = round(new_weight, 4)
            updated_weights[name] = analyzers_tunable[name]['weight']

        # Нормализация весов (сумма = 1.0) - теперь внутри TUNABLE_PARAMS
        total = sum(a.get('weight', 0) for a in analyzers_tunable.values())
        if total > 0:
            for name in analyzers_tunable:
                if 'weight' in analyzers_tunable[name]:
                    analyzers_tunable[name]['weight'] = round(analyzers_tunable[name]['weight'] / total, 4)
        
        tunable_params['analyzers'] = analyzers_tunable

        # 3. Коррекция ВСЕХ настраиваемых параметров из TUNABLE_PARAMS
        # Читаем и записываем изменения ВНУТРЬ секции TUNABLE_PARAMS
        tunable_params = self.config.get('TUNABLE_PARAMS', {})
        
        # --- Risk параметры ---
        risk_tunable = tunable_params.get('risk', {})
        risk_current = risk_tunable.copy()  # Работаем внутри TUNABLE_PARAMS['risk']
        
        # Параметры риска для обновления
        risk_param_map = {
            'risk_per_trade_pct': (0.1, 2.0),      # (min, max)
            'stop_loss_pct': (0.5, 3.0),           # Стоп-лосс % от цены входа
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
        
        tunable_params['risk'] = risk_current
        
        # --- Leverage параметры ---
        leverage_tunable = tunable_params.get('leverage', {})
        leverage_current = leverage_tunable.copy()  # Работаем внутри TUNABLE_PARAMS['leverage']
        
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
        
        tunable_params['leverage'] = leverage_current
        
        # --- Trailing Stop параметры ---
        trailing_tunable = tunable_params.get('trailing_stop', {})
        trailing_current = trailing_tunable.copy()  # Работаем внутри TUNABLE_PARAMS['trailing_stop']
        
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
        
        tunable_params['trailing_stop'] = trailing_current
        
        # --- Analyzer параметры (веса и confidence_factor) ---
        analyzers_tunable = tunable_params.get('analyzers', {})
        # Работаем напрямую с analyzers_tunable, без копии
        # Изменяем значения внутри словаря TUNABLE_PARAMS['analyzers']
        
        for analyzer_name, analyzer_cfg in list(analyzers_tunable.items()):
            if analyzer_name not in analyzers_tunable:
                analyzers_tunable[analyzer_name] = {}
            
            current_analyzer = analyzers_tunable[analyzer_name]
            
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
            
            # ВЕСА УЖЕ ОБНОВЛЕНЫ И НОРМАЛИЗОВАНЫ ВЫШЕ (строки 73-110)
            # Не перезаписываем их здесь, чтобы не нарушить нормализацию
        
        tunable_params['analyzers'] = analyzers_tunable
        
        # Сохраняем обновлённый TUNABLE_PARAMS обратно в конфиг
        self.config['TUNABLE_PARAMS'] = tunable_params

        # 4. Сохранение изменений в конфиг
        self._save_config()
        
        # 5. Запись решения в БД (.brain)
        # Вычисляем актуальный множитель риска из обновлённых параметров
        dynamic_risk_multiplier = tunable_params['risk'].get('risk_per_trade_pct', 0.5) / 0.5  # Относительно базового 0.5%
        
        decision_record = {
            'timestamp': datetime.now().isoformat(),
            'snapshot_id': snapshot.get('id'),
            'error_score': round(error_score, 4),
            'updated_weights': updated_weights,
            'new_risk_multiplier': round(dynamic_risk_multiplier, 4),
            'updated_params': {
                'risk': {k: v for k, v in tunable_params['risk'].items() if k != 'dynamic_risk_multiplier'},
                'leverage': tunable_params.get('leverage', {}),
                'trailing_stop': tunable_params.get('trailing_stop', {}),
                'analyzers': tunable_params.get('analyzers', {}),
            }
        }
        # Предполагаем, что у db_manager есть метод save_tuner_decision
        # Если нет - добавим заглушку или обработаем ошибку
        try:
            self.db.save_tuner_decision(decision_record)
        except AttributeError:
            print("[AutoTuner] Метод save_tuner_decision не найден в DB manager")
            
        print(f"[AutoTuner] Обработан снимок. Ошибка: {error_score:.4f}. Параметры обновлены.")
