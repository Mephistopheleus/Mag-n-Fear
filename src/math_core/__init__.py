"""
Инициализация пакета math_core.
Экспортирует базовые классы и реестр.
"""
from .base_indicator import BaseIndicator
from .registry import IndicatorRegistry, register_indicator

__all__ = [
    'BaseIndicator',
    'IndicatorRegistry',
    'register_indicator'
]