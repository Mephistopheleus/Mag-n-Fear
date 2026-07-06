"""
Core Module for Mag-n-Fear Robot.
Configuration, models, and shared utilities.
"""
from src.core.config_loader import load_config, get_config, Config
from src.core.models import DataCard, NewsVector, RiskMetrics

__all__ = ['load_config', 'get_config', 'Config', 'DataCard', 'NewsVector', 'RiskMetrics']