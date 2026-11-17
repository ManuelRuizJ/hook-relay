from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "db"
    postgres_port: int = 5432
    redis_url: str
    api_port: int = 8000
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    worker_metrics_port: int = 9100
    # Connections kept open per process. SQLAlchemy's default (5, plus 10
    # "overflow" connections closed after every use) churns connections
    # under concurrency, and each new one pays Postgres' SCRAM handshake
    # (4096 HMAC rounds) in this process's CPU.
    # Every process has its own pool and Postgres allows 100 connections by
    # default, so keep processes x (size + overflow) well under that.
    db_pool_size: int = 10
    db_max_overflow: int = 5

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
