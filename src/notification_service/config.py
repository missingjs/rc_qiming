"""Shared environment configuration for the API, worker, and migrations."""

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: str = Field(repr=False)
    worker_poll_seconds: float = Field(default=1, gt=0)
    delivery_timeout_seconds: float = Field(default=15, gt=0)
    lease_seconds: float = Field(default=60, gt=0)
    max_attempts: int = Field(default=5, ge=1)
    retry_base_seconds: float = Field(default=5, gt=0)
    retry_max_seconds: float = Field(default=60, gt=0)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        try:
            url = make_url(value)
        except Exception as exc:
            raise ValueError("DATABASE_URL must be a valid database URL") from exc
        if url.drivername != "postgresql+psycopg":
            raise ValueError("DATABASE_URL must use postgresql+psycopg")
        return value

    @model_validator(mode="after")
    def validate_timings(self) -> Settings:
        if self.lease_seconds < self.delivery_timeout_seconds + 5:
            raise ValueError("The lease must exceed the delivery timeout by at least five seconds")
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("The maximum retry delay must be at least the base delay")
        return self
