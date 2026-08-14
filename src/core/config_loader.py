"""
Core Module: Configuration Loader
Загружает config.yaml, валидирует через Pydantic и предоставляет доступ ко всем настройкам.
В коде не должно быть магических констант - всё из этого файла.
"""
import yaml
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Optional, Any


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
    trading_balance_usd: float = 50.0      # Выделенный баланс для торговли (USDT)
    max_daily_loss_pct: float = 2.0      # Максимальная просадка за день (%)
    max_position_size_pct: float = 0.66  # Максимальный размер позиции (% от баланса), 2/3 = 0.66
    risk_per_trade_pct: float = 0.5      # Риск на сделку (%)
    min_reward_ratio: float = 1.5        # Минимальное соотношение Профит/Риск

    # Режим обучения (Learning Mode)
    learning_mode: bool = True

    # Минимальная целевая прибыль (%) для рассмотрения сценария
    min_profit_threshold: float = 0.05

    # Скидка к порогу прибыли на флэте (настраивается автотюнером)
    flat_market_discount: float = 0.7    # 0.7 = снижение на 30%

    # Реальные комиссии Binance Futures (maker/taker)
    commission_rate: float = 0.0002      # 0.02% базовая комиссия
    commission_buffer: float = 0.0003    # Дополнительный запас (итого 0.05%)
    
    # Проскальзывание (с запасом для симуляции)
    slippage_buffer: float = 0.0005      # 0.05% запас на проскальзывание

    # Формула размера позиции (Kelly с ограничением)
    kelly_fraction: float = 0.25         # Доля от оптимального Келли (0.25 = 25%)

    # Корреляционные ограничения
    max_correlation_exposure: float = 0.8  # Максимальная корреляция между позициями


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
    db_path: str = "data_storage/history.txt"
    matrix_cache_path: str = "data_storage/matrix_cache.txt"


class ApiKeysConfig(BaseModel):
    binance_testnet_api_key: str = ""
    binance_testnet_api_secret: str = ""


class NewsConfig(BaseModel):
    """Конфигурация новостного агрегатора."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    finnhub_api_key: str = ""
    newsapi_key: str = ""
    fred_api_key: str = ""
    
    # Веса источников
    news_weight_finnhub: float = 1.0
    news_weight_newsapi: float = 0.8
    news_weight_fred: float = 0.9
    
    # Период полураспада (секунды)
    news_half_life_sec: int = 900
    
    def get(self, key: str, default=None):
        """Метод для совместимости со старым кодом, ожидающим dict."""
        return getattr(self, key, default)


class ConstantsConfig(BaseModel):
    """Константы - неизменяемые параметры."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    indiator_periods: Dict[str, Any] = Field(default_factory=dict)
    timeframes: Dict[str, Any] = Field(default_factory=dict)
    convergence_threshold: float = 0.85
    stability_window_sec: int = 600
    kelly_formula_cap: float = 0.25
    max_leverage_cap: int = 20
    heartbeat_interval_sec: int = 5
    data_snapshot_interval_sec: int = 1


class TunableParamsConfig(BaseModel):
    """Настраиваемые параметры для автотюнера."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    risk: RiskConfig = Field(default_factory=RiskConfig)
    leverage: Dict[str, float] = Field(default_factory=dict)
    trailing_stop: Dict[str, Any] = Field(default_factory=dict)
    analyzers: Dict[str, Any] = Field(default_factory=dict)
    scenarios: ScenarioConfig = Field(default_factory=ScenarioConfig)
    matrix: MatrixConfig = Field(default_factory=MatrixConfig)
    tuner: TunerConfig = Field(default_factory=TunerConfig)
    lab: Dict[str, Any] = Field(default_factory=dict)


class SystemConfig(BaseModel):
    """Системные настройки."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    bot_name: str = "HybridMathScalper"
    version: str = "3.0.0"
    mode: str = "shadow"
    exchange: str = "binance_futures"
    
    # Настройки data_feed - добавлено поле для корректного чтения конфига
    data_feed: Optional[Dict[str, Any]] = Field(default_factory=dict)
    
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    data: Dict[str, Any] = Field(default_factory=dict)


class Config(BaseModel):
    api_keys: ApiKeysConfig = Field(default_factory=ApiKeysConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    CONSTANTS: ConstantsConfig = Field(default_factory=ConstantsConfig)
    TUNABLE_PARAMS: TunableParamsConfig = Field(default_factory=TunableParamsConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)
    
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
