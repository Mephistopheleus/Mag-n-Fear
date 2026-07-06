"""
Classic Timeframes Module
Агрегирует данные в классические "ступени" (свечи) по стандартным таймфреймам.
Используется для сравнения с Continuum и для традиционных индикаторов.
"""

import polars as pl
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

@dataclass
class Candle:
    """Стандартная свеча"""
    timestamp: datetime
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    tf_sec: int = 300

@dataclass
class ClassicSnapshot:
    """Снимок состояния на основе классических ТФ"""
    timestamp: datetime
    method: str = "STEPS"
    
    # Таймфрейм
    tf_sec: int = 300
    tf_label: str = "5m"  # "5m", "1h", etc.
    
    # Данные свечи
    candle: Optional[Candle] = None
    
    # Базовые метрики
    price_current: float = 0.0
    vwap: float = 0.0      # VWAP за свечу
    range_pct: float = 0.0 # Процентный диапазон свечи
    
    # Метаданные
    confidence_raw: float = 0.0
    time_horizon_sec: int = 300
    tags: List[str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []

class ClassicTF:
    def __init__(self, config=None):
        self.config = config
        self.buffers: Dict[int, List[Dict[str, Any]]] = {}
        # Стандартные таймфреймы (секунды)
        self.tfs = [
            300,    # 5m
            900,    # 15m
            1800,   # 30m
            3600,   # 1h
            14400,  # 4h
            86400   # 1d
        ]
        
        for tf in self.tfs:
            self.buffers[tf] = []
            
    def _get_tf_label(self, tf_sec: int) -> str:
        labels = {
            300: "5m", 900: "15m", 1800: "30m",
            3600: "1h", 14400: "4h", 86400: "1d"
        }
        return labels.get(tf_sec, f"{tf_sec}s")
        
    def add_tick(self, timestamp: datetime, price: float, volume: float):
        """Добавляет тик во все буферы таймфреймов"""
        for tf in self.tfs:
            # Округляем время до начала свечи
            start_ts = datetime.fromtimestamp(
                (timestamp.timestamp() // tf) * tf
            )
            
            self.buffers[tf].append({
                "ts": start_ts,
                "price": price,
                "volume": volume
            })
            
            # Храним только последние 2 свечи для экономии памяти
            if len(self.buffers[tf]) > 100: 
                # Оставляем данные только за последние 2 свечи + небольшой запас
                cutoff_ts = start_ts - timedelta(seconds=tf*2)
                self.buffers[tf] = [
                    x for x in self.buffers[tf] 
                    if x["ts"] >= cutoff_ts
                ]
    
    def get_state(self, tf_sec: int, current_ts: datetime) -> Optional[ClassicSnapshot]:
        """
        Вычисляет состояние для конкретного таймфрейма.
        Формирует текущую (незавершенную) свечу.
        """
        if tf_sec not in self.buffers:
            return None
            
        buffer = self.buffers[tf_sec]
        if not buffer:
            return None
            
        # Группируем по свечам
        df = pl.DataFrame(buffer)
        
        # Находим текущую свечу
        current_start_ts = datetime.fromtimestamp(
            (current_ts.timestamp() // tf_sec) * tf_sec
        )
        
        candle_df = df.filter(pl.col("ts") == current_start_ts)
        
        if candle_df.is_empty():
            # Если данных за текущую свечу нет, берем предыдущую как референс
            prev_start_ts = current_start_ts - timedelta(seconds=tf_sec)
            candle_df = df.filter(pl.col("ts") == prev_start_ts)
            if candle_df.is_empty():
                return None
                
        # Агрегация свечи
        ohlcv = candle_df.select([
            pl.col("price").first().alias("open"),
            pl.col("price").max().alias("high"),
            pl.col("price").min().alias("low"),
            pl.col("price").last().alias("close"),
            pl.col("volume").sum().alias("volume")
        ]).row(0)
        
        open_p, high, low, close, volume = ohlcv
        
        candle = Candle(
            timestamp=current_start_ts,
            open=open_p,
            high=high,
            low=low,
            close=close,
            volume=volume,
            tf_sec=tf_sec
        )
        
        # Расчет VWAP свечи (упрощенно, если нет данных о типах ордеров)
        vwap = close # Заглушка, можно улучшить
        
        # Диапазон в процентах
        range_pct = ((high - low) / open_p * 100) if open_p > 0 else 0.0
        
        # Уверенность: зависит от объема и заполненности свечи
        # Чем больше прошло времени от начала свечи, тем выше уверенность в закрытии
        elapsed = (current_ts - current_start_ts).total_seconds()
        time_progress = min(1.0, elapsed / tf_sec)
        
        # Объемная уверенность (сравнение со средним объемом, тут упрощенно)
        vol_confidence = min(1.0, volume / 1000.0) # Нормализация
        
        confidence_raw = (time_progress * 0.6) + (vol_confidence * 0.4)
        
        return ClassicSnapshot(
            timestamp=current_ts,
            tf_sec=tf_sec,
            tf_label=self._get_tf_label(tf_sec),
            candle=candle,
            price_current=close,
            vwap=vwap,
            range_pct=float(range_pct),
            confidence_raw=float(confidence_raw),
            time_horizon_sec=tf_sec, # Горизонт равен ТФ
            tags=["steps", f"tf_{self._get_tf_label(tf_sec)}", "ohlcv"]
        )
