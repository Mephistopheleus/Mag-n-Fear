"""
Tuner Module: Auto Tuner
Анализирует карточки сделок, вычисляет влияние параметров на результат,
обновляет коэффициенты доверия и настройки в конфиге.

НЕ меняет математику анализаторов - только регулирует "силу влияния" через конфиг.
"""
import json
from pathlib import Path
from typing import Dict, List, Any, Optional, TYPE_CHECKING
from datetime import datetime
from collections import defaultdict
import yaml

if TYPE_CHECKING:
    from src.core.field import ProbabilityField


class AutoTuner:
    """
    Авто-настройщик системы.
    
    Принцип работы:
    1. Загружает карточки сделок из хранилища
    2. Группирует по типам анализаторов и значениям параметров
    3. Вычисляет метрики: WinRate, Avg PnL, Impact Score для каждой группы
    4. Обновляет confidence_factors и другие параметры в конфиге
    5. Сохраняет новый config.yaml
    
    Целевые метрики: 100% PnL/день, 100% WinRate, 0% Drawdown
    (На практике: стремление к максимуму этих показателей)
    """
    
    def __init__(self, config: Any, probability_field):
        # Конвертируем Pydantic модель в dict для совместимости
        if hasattr(config, 'model_dump'):
            self.config_dict = config.model_dump()
        else:
            self.config_dict = config
            
        self.field = probability_field
        
        # Пути из конфига
        logging_cfg = self.config_dict.get('logging', {})
        storage_cfg = self.config_dict.get('storage', {})
        
        cards_path = logging_cfg.get('cards_path', 'data_storage/cards') if isinstance(logging_cfg, dict) else getattr(logging_cfg, 'cards_path', 'data_storage/cards')
        config_path = 'configs/config.yaml'
        
        self.cards_path = Path(cards_path)
        self.config_path = Path(config_path)
        self.cards: List[Dict] = []
        self.current_config: Dict = {}
    
    def load_cards(self) -> int:
        """Загружает все карточки из хранилища."""
        self.cards = []
        
        if not self.cards_path.exists():
            return 0
        
        # Загрузка карточек (предполагаем формат JSON или Parquet)
        for card_file in self.cards_path.glob("*.txt"):
            with open(card_file, 'r', encoding='utf-8') as f:
                self.cards.append(json.load(f))
        
        return len(self.cards)
    
    def load_config(self):
        """Загружает текущий конфиг."""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.current_config = yaml.safe_load(f)
    
    def analyze_impact(self) -> Dict[str, Any]:
        """
        Анализирует влияние параметров на результат сделок.
        
        :return: Отчет с метриками по каждому параметру
        """
        if not self.cards:
            return {"error": "No cards loaded"}
        
        # Группировка по типам анализаторов
        analyzer_stats: Dict[str, Dict] = defaultdict(lambda: {
            "total_trades": 0,
            "profitable_trades": 0,
            "total_pnl": 0.0,
            "avg_confidence": 0.0
        })
        
        # Анализ каждой карточки
        for card in self.cards:
            if not card.get("trade_result"):
                continue  # Пропускаем незавершенные сделки
            
            pnl = card["trade_result"].get("pnl_usd", 0)
            is_profitable = pnl > 0
            
            # Анализ вкладов анализаторов
            tuner_notes = card.get("tuner_notes", {})
            
            for key, value in tuner_notes.items():
                if key.startswith("analyzer_") and key.endswith("_useful"):
                    analyzer_type = key.replace("analyzer_", "").replace("_useful", "")
                    
                    analyzer_stats[analyzer_type]["total_trades"] += 1
                    if is_profitable:
                        analyzer_stats[analyzer_type]["profitable_trades"] += 1
                    analyzer_stats[analyzer_type]["total_pnl"] += pnl
                    
                    # Учет доверия
                    conf_key = f"analyzer_{analyzer_type}_confidence"
                    if conf_key in tuner_notes:
                        current_avg = analyzer_stats[analyzer_type]["avg_confidence"]
                        count = analyzer_stats[analyzer_type]["total_trades"]
                        # Скользящее среднее
                        analyzer_stats[analyzer_type]["avg_confidence"] = (
                            (current_avg * (count - 1) + tuner_notes[conf_key]) / count
                        )
        
        # Расчет метрик
        report = {
            "analyzers": {},
            "timestamp": datetime.utcnow().isoformat()
        }
        
        for analyzer_type, stats in analyzer_stats.items():
            if stats["total_trades"] == 0:
                continue
            
            win_rate = stats["profitable_trades"] / stats["total_trades"]
            avg_pnl = stats["total_pnl"] / stats["total_trades"]
            
            # Impact Score: комбинация WinRate и Avg PnL
            # (Простая формула, можно усложнить)
            impact_score = win_rate * 0.7 + min(avg_pnl / 100, 0.3)  # Нормализация
            
            report["analyzers"][analyzer_type] = {
                "total_trades": stats["total_trades"],
                "win_rate": win_rate,
                "avg_pnl": avg_pnl,
                "impact_score": impact_score,
                "avg_confidence": stats["avg_confidence"]
            }
        
        return report
    
    def update_confidence_factors(self, report: Dict[str, Any], min_trades: int = 10):
        """
        Обновляет коэффициенты доверия в конфиге на основе отчета.
        
        :param report: Отчет из analyze_impact()
        :param min_trades: Минимальное количество сделок для обновления
        """
        if "analyzers" not in report:
            return
        
        new_confidence_factors = {}
        
        for analyzer_type, metrics in report["analyzers"].items():
            if metrics["total_trades"] < min_trades:
                # Недостаточно данных, оставляем старое значение
                old_value = self.current_config.get("tuner", {}).get("confidence_factors", {}).get(analyzer_type, 0.5)
                new_confidence_factors[analyzer_type] = old_value
                continue
            
            # Новая уверенность = Impact Score (нормализованный)
            # Можно добавить другие формулы
            new_confidence = min(max(metrics["impact_score"], 0.1), 1.0)  # Ограничение [0.1, 1.0]
            new_confidence_factors[analyzer_type] = round(new_confidence, 3)
        
        # Обновление конфига
        if "tuner" not in self.current_config:
            self.current_config["tuner"] = {}
        self.current_config["tuner"]["confidence_factors"] = new_confidence_factors
    
    def save_config(self, backup: bool = True):
        """Сохраняет обновленный конфиг."""
        if backup:
            backup_path = self.config_path.with_suffix(".yaml.bak")
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                with open(backup_path, 'w', encoding='utf-8') as f:
                    f.write(content)
        
        with open(self.config_path, 'w', encoding='utf-8') as f:
            yaml.dump(self.current_config, f, default_flow_style=False, allow_unicode=True)
    
    def run_full_cycle(self) -> Dict[str, Any]:
        """
        Запускает полный цикл настройки:
        1. Загрузка карточек
        2. Анализ влияния
        3. Обновление конфига
        4. Сохранение
        
        :return: Отчет о настройке
        """
        self.load_config()
        cards_count = self.load_cards()
        
        if cards_count == 0:
            return {"status": "no_data", "message": "No trade cards found"}
        
        report = self.analyze_impact()
        self.update_confidence_factors(report)
        self.save_config()
        
        return {
            "status": "success",
            "cards_analyzed": cards_count,
            "report": report,
            "new_config_path": str(self.config_path)
        }
