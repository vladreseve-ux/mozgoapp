from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = ""
    telegram_webhook_secret: str = ""
    mini_app_url: str = ""
    database_url: str = "postgresql+psycopg://moz:moz@localhost:5432/moz"
    redis_url: str = "redis://localhost:6379/0"
    replicate_api_token: str = ""
    replicate_model: str = "black-forest-labs/flux-schnell"
    trophy_min_net_win: int = 500

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

STAR_PACKAGES: dict[str, dict[str, int | str]] = {
    "starter": {"title": "Стартовый импульс", "stars": 50, "neurons": 500},
    "neural": {"title": "Нейроускоритель", "stars": 150, "neurons": 1650},
    "synapse": {"title": "Синапс-пак", "stars": 500, "neurons": 6000},
    "avatar": {"title": "AI-аватар", "stars": 100, "neurons": 0},
}
