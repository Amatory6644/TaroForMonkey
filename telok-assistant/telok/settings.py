from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TELOK_", env_file=".env", extra="ignore")
    env: str = "development"
    database_url: str = "postgresql+psycopg://telok:telok-local-only@127.0.0.1:54391/telok"
    owner_id: int = 1
    credentials_dir: Path = Path(".secrets")
    reasoning_provider: str = "chatgpt_plan"
    admin_token: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    text_model: str = "gpt-6.1-sol"
    image_model: str = "gpt-image-2.5-flare"
    text_call_reserve_usd: float = 0
    image_call_reserve_usd: float = 0
    seedance_call_reserve_usd: float = 0
    task_budget_usd: float = 0
    daily_budget_usd: float = 0
    pricing_confirmed: bool = False
    telegram_token: SecretStr = SecretStr("")
    allowed_telegram_ids: list[int] = Field(default_factory=list)
    storage: str = "local"
    data_dir: Path = Path("data")
    s3_bucket: str = ""
    s3_endpoint: str = ""
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    s3_region: str = "us-east-1"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    research_allowed_domains: list[str] = Field(default_factory=list)
    enable_auto: bool = False
    publishing_enabled: bool = False
    application_version: str = "0.1.0"
    executor_id: str = "telok-executor-1"

    @model_validator(mode="after")
    def validate_production(self):
        if self.env == "production":
            if len(self.admin_token.get_secret_value()) < 24:
                raise ValueError("Production требует TELOK_ADMIN_TOKEN длиной от 24 символов.")
            if self.storage != "s3" or not self.s3_bucket:
                raise ValueError("Production требует приватное S3-хранилище.")
        if not self.database_url.startswith("postgresql"):
            raise ValueError("Telok использует PostgreSQL; SQLite не является production/runtime заменой.")
        return self

    def provider_ready(self, image: bool = False) -> bool:
        reserve = self.image_call_reserve_usd if image else self.text_call_reserve_usd
        return (
            bool(self.openai_api_key.get_secret_value())
            and self.pricing_confirmed
            and reserve > 0
            and self.task_budget_usd >= reserve
            and self.daily_budget_usd >= reserve
        )


@lru_cache
def settings() -> Settings:
    return Settings()
