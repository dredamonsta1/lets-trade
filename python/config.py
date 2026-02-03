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

    # Trading symbols
    symbols: list[str] = Field(
        default=["AAPL", "MSFT", "GOOGL"],
        description="Symbols to trade",
    )

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")


# Global settings instance
settings = Settings()
