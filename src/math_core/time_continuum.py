"""
Time Continuum Module
Создает плавное "полотно" рыночных данных, устраняя жесткие границы таймфреймов.
Использует весовое затухание (exponential decay) для придания большей значимости свежим данным.
"""

import numpy as np
import polars as pl
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class ContinuumSnapshot:
    """Снимок состояния на основе непрерывного полотна"""
    timestamp: datetime
    method: str = "CONTINUUM"
    
    # Основные метрики
    price_current: float = 0.0
    vwap_continuum: float = 0.0  # Взвешенная средняя цена за окно
    momentum: float = 0.0        # Инерция цены
    volatility: float = 0.0      # Локальная волатильность
    
    # Метаданные для матрицы
    confidence_raw: float = 0.0  # Сырая уверенность (на основе плотности данных)
    time_horizon_sec: int = 300  # Горизонт прогноза
    data_density: float = 0.0    # Плотность данных в окне (0.0 - 1.0)
    
    # Для маркировки в матрице
    tags: List[str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []

class TimeContinuum:
    def __init__(self, window_sec: int = 300, decay_factor: float = 0.95):
        """
        :param window_sec: Размер скользящего окна в секундах (по умолчанию 5 мин)
        :param decay_factor: Коэффициент затухания (чем ближе к 1, тем дольше память)
        """
        self.window_sec = window_sec
        self.decay_factor = decay_factor
        self.buffer: List[Dict[str, Any]] = []
        
    def add_tick(self, timestamp: datetime, price: float, volume: float):
        """Добавляет тик в буфер с очисткой старых данных"""
        self.buffer.append({
            "ts": timestamp,
            "price": price,
            "volume": volume
        })
        self._clean_buffer(timestamp)
        
    def _clean_buffer(self, current_ts: datetime):
        """Удаляет данные старше окна"""
        cutoff = current_ts.timestamp() - self.window_sec
        self.buffer = [x for x in self.buffer if x["ts"].timestamp() > cutoff]
        
    def get_state(self, current_ts: datetime) -> Optional[ContinuumSnapshot]:
        """
        Вычисляет текущее состояние рынка на основе полотна.
        Возвращает None, если недостаточно данных.
        """
        if len(self.buffer) < 10:
            return None
            
        df = pl.DataFrame(self.buffer)
        
        # Расчет временных весов (экспоненциальное затухание)
        now_ts = current_ts.timestamp()
        df = df.with_columns([
            (pl.col("ts").apply(lambda x: now_ts - x.timestamp())).alias("age_sec"),
            (pl.col("age_sec").apply(lambda x: self.decay_factor ** (x / 60))).alias("weight")
        ])
        
        # Нормализация весов
        total_weight = df["weight"].sum()
        if total_weight == 0:
            return None
            
        df = df.with_columns([
            (pl.col("weight") / total_weight).alias("norm_weight")
        ])
        
        # 1. VWAP Continuum (взвешенный по времени и объему)
        vwap = (df["price"] * df["volume"] * df["norm_weight"]).sum() / (df["volume"] * df["norm_weight"]).sum()
        
        # 2. Momentum (взвешенная скорость изменения цены)
        # Сравниваем взвешенную цену сейчас и N секунд назад
        recent_mask = df["age_sec"] < 10  # последние 10 сек
        old_mask = (df["age_sec"] >= 10) & (df["age_sec"] < 40)
        
        if not recent_mask.any() or not old_mask.any():
            return None
            
        price_recent = (df.filter(recent_mask)["price"] * df.filter(recent_mask)["norm_weight"]).sum()
        price_old = (df.filter(old_mask)["price"] * df.filter(old_mask)["norm_weight"]).sum()
        momentum = price_recent - price_old
        
        # 3. Volatility (взвешенное стандартное отклонение)
        mean_price = (df["price"] * df["norm_weight"]).sum()
        variance = ((df["price"] - mean_price)**2 * df["norm_weight"]).sum()
        volatility = float(np.sqrt(variance))
        
        # 4. Data Density (насколько плотно заполнено окно)
        # Идеальная плотность зависит от актива, тут упрощенно
        data_density = min(1.0, len(self.buffer) / (self.window_sec / 0.5)) # Ожидаем тик каждые 0.5с
        
        # 5. Raw Confidence (зависит от плотности и объема)
        confidence_raw = data_density * 0.8 + 0.2 # База 0.2 + вклад плотности
        
        return ContinuumSnapshot(
            timestamp=current_ts,
            price_current=float(df[-1]["price"]),
            vwap_continuum=float(vwap),
            momentum=float(momentum),
            volatility=float(volatility),
            confidence_raw=float(confidence_raw),
            time_horizon_sec=int(self.window_sec / 2), # Прогноз на половину окна
            data_density=float(data_density),
            tags=["continuum", "vwap", "momentum", "volatility"]
        )
