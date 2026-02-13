"""Configuration management using Pydantic settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IBSettings(BaseSettings):
    """Interactive Brokers connection settings."""

    model_config = SettingsConfigDict(env_prefix="IB_")

    host: str = Field(default="127.0.0.1", description="TWS/Gateway host")
    port: int = Field(default=7497, description="7497 for paper, 7496 for live")
    client_id: int = Field(default=1, description="Unique client ID")
    readonly: bool = Field(default=False, description="Read-only mode")
    timeout: float = Field(default=20.0, description="Connection timeout in seconds")


class QuestDBSettings(BaseSettings):
    """QuestDB connection settings."""

    model_config = SettingsConfigDict(env_prefix="QUESTDB_")

    host: str = Field(default="127.0.0.1", description="QuestDB host")
    ilp_port: int = Field(default=9009, description="InfluxDB Line Protocol port")
    http_port: int = Field(default=9000, description="HTTP/REST API port")
    pg_port: int = Field(default=8812, description="PostgreSQL wire protocol port")


class RedisSettings(BaseSettings):
    """Redis connection settings."""

    model_config = SettingsConfigDict(env_prefix="REDIS_")

    host: str = Field(default="127.0.0.1", description="Redis host")
    port: int = Field(default=6379, description="Redis port")
    db: int = Field(default=0, description="Redis database number")


class RiskSettings(BaseSettings):
    """Risk management parameters."""

    model_config = SettingsConfigDict(env_prefix="RISK_")

    max_position_size: int = Field(default=100, description="Maximum position size in shares")
    max_daily_loss: float = Field(default=1000.0, description="Kill switch trigger (USD)")
    quote_size: int = Field(default=10, description="Default quote size in shares")
    min_spread_bps: float = Field(default=5.0, description="Minimum spread in basis points")


class PennyStockMomentumSettings(BaseSettings):
    """Settings for the Penny Stock Momentum Strategy."""

    model_config = SettingsConfigDict(env_prefix="PENNY_STOCK_")

    price_threshold: float = Field(default=5.0, description="Max price for a penny stock")
    market_cap_threshold: float = Field(default=300_000_000.0, description="Max market cap for a penny stock (USD)")
    volume_avg_days: int = Field(default=50, description="Number of days for average volume calculation")
    volume_multiplier_entry: float = Field(default=3.0, description="Volume multiplier for entry signal (e.g., 3.0 for 3x avg volume)")
    price_increase_entry_percent: float = Field(default=2.0, description="Price increase percentage for entry signal (e.g., 2.0 for 2%)")
    price_increase_entry_window_minutes: int = Field(default=5, description="Time window in minutes for price increase check")
    profit_target_min_percent: float = Field(default=10.0, description="Minimum profit target percentage for exit")
    profit_target_max_percent: float = Field(default=15.0, description="Maximum profit target percentage for exit")
    volume_decay_exit_percent: float = Field(default=0.5, description="Volume decay percentage for exit (e.g., 0.5 for 50% of peak)")
    stop_loss_percent: float = Field(default=3.0, description="Stop loss percentage from entry price")
    max_trade_amount: float = Field(default=1000.0, description="Maximum USD amount to trade per position")
    strategy_interval_seconds: int = Field(default=30, description="Interval in seconds for the penny stock strategy to run its main loop")


class Settings(BaseSettings):
    """Root settings container."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ib: IBSettings = Field(default_factory=IBSettings)
    questdb: QuestDBSettings = Field(default_factory=QuestDBSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    penny_stock: PennyStockMomentumSettings = Field(default_factory=PennyStockMomentumSettings) # Added

    # Trading symbols
    symbols: list[str] = Field(
        default=["AAPL", "MSFT", "GOOGL"],
        description="Symbols to trade",
    )

    # Options Strategy
    options_strategy_interval_seconds: int = Field(
        default=60, # Run every 60 seconds by default
        description="Interval in seconds for the options hedging strategy to run",
    )
    options_risk_free_rate: float = Field(
        default=0.04, # Default risk-free rate for options calculations
        description="Risk-free interest rate for options pricing models",
    )
    options_default_implied_volatility: float = Field(
        default=0.25, # Default implied volatility if not available from data source
        description="Default implied volatility to use if not provided by the data source",
    )

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")


# Global settings instance
settings = Settings()
