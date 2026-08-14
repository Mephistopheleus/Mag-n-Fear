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
        2. Корректирует веса и параметры.
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
        
        # 2. Коррекция весов анализаторов
        analyzer_scores = snapshot.get('analyzer_scores', {})
        direction = 1 if pnl > 0 else -1
        
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

        # 3. Коррекция общих настроек (Risk Multiplier)
        if 'risk' not in self.config:
            self.config['risk'] = {}
            
        current_mult = self.config['risk'].get('dynamic_risk_multiplier', 1.0)
        if pnl < 0:
            new_mult = current_mult * (1 - self.max_step_pct)
        else:
            new_mult = current_mult * (1 + self.max_step_pct)
            
        # Ограничение множителя [0.5, 2.0]
        new_mult = max(0.5, min(2.0, new_mult))
        self.config['risk']['dynamic_risk_multiplier'] = round(new_mult, 4)

        # 4. Сохранение изменений в конфиг
        self._save_config()
        
        # 5. Запись решения в БД (.brain)
        decision_record = {
            'timestamp': datetime.now().isoformat(),
            'snapshot_id': snapshot.get('id'),
            'error_score': round(error_score, 4),
            'updated_weights': updated_weights,
            'new_risk_multiplier': new_mult
        }
        # Предполагаем, что у db_manager есть метод save_tuner_decision
        # Если нет - добавим заглушку или обработаем ошибку
        try:
            self.db.save_tuner_decision(decision_record)
        except AttributeError:
            print("[AutoTuner] Метод save_tuner_decision не найден в DB manager")
            
        print(f"[AutoTuner] Обработан снимок. Ошибка: {error_score:.4f}. Весы обновлены.")
