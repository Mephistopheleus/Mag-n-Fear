"""
Core Module: Configuration Loader
Загружает config.yaml, валидирует через Pydantic и предоставляет доступ ко всем настройкам.
В коде не должно быть магических констант - всё из этого файла.
"""
import yaml
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Dict, Optional


class BotConfig(BaseModel):
    name: str
    version: str
    mode: str = "shadow"  # shadow, live, backtest
    exchanges: List[str]


class DataConfig(BaseModel):
    symbols: List[str]
    timeframes: List[str]
    order_book_depth: int = 20
    snapshot_interval_sec: int = 1


class MatrixConfig(BaseModel):
    time_horizon_sec: int = 300
    price_bins_count: int = 50
    time_bins_count: int = 60
    decay_factor: float = 0.95


class TunerConfig(BaseModel):
    confidence_factors: Dict[str, float] = Field(default_factory=dict)
    min_trades_for_update: int = 50
    impact_threshold: float = 0.05


class RiskConfig(BaseModel):
    trading_balance_usd: float = 50.0
    max_daily_loss_pct: float = 2.0
    max_position_size_usd: float = 1000.0
    risk_per_trade_pct: float = 0.5
    min_reward_ratio: float = 1.5
    kelly_fraction: float = 0.25
    max_correlation_exposure: float = 0.8


class ScenarioConfig(BaseModel):
    min_confidence_threshold: float = 0.65
    entry_delay_sec: int = 2
    max_slippage_bps: int = 10


class ExecutorConfig(BaseModel):
    default_order_type: str = "limit"
    limit_order_timeout_sec: int = 30
    use_ioc: bool = False
    trailing_stop_enabled: bool = True
    trailing_stop_activation_pct: float = 0.5
    trailing_stop_distance_pct: float = 0.3


class LoggingConfig(BaseModel):
    level: str = "INFO"
    save_cards: bool = True
    cards_path: str = "data_storage/cards"
    log_path: str = "logs/bot.log"
    rotation: str = "1 day"


class StorageConfig(BaseModel):
    type: str = "sqlite"
    db_path: str = "data_storage/history.db"
    matrix_cache_path: str = "data_storage/matrix_cache.parquet"


class ApiKeysConfig(BaseModel):
    binance_testnet_api_key: str = ""
    binance_testnet_api_secret: str = ""


class Config(BaseModel):
    bot: BotConfig
    data: DataConfig
    matrix: MatrixConfig
    tuner: TunerConfig
    risk: RiskConfig
    scenario: ScenarioConfig
    executor: ExecutorConfig
    logging: LoggingConfig
    storage: StorageConfig
    api_keys: ApiKeysConfig = Field(default_factory=ApiKeysConfig)
    
    def get(self, key: str, default=None):
        """Метод для совместимости со старым кодом, ожидающим dict."""
        return getattr(self, key, default)


def load_config(config_path: str = "configs/config.yaml") -> Config:
    """Загружает конфигурацию из YAML файла."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(path, 'r', encoding='utf-8') as f:
        raw_config = yaml.safe_load(f)
    
    return Config(**raw_config)


# Глобальный экземпляр конфигурации (будет инициализирован при старте)
config: Optional[Config] = None


def get_config() -> Config:
    """Получает глобальную конфигурацию."""
    if config is None:
        raise RuntimeError("Config not loaded. Call load_config() first.")
    return config
