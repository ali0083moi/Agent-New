"""Environment configuration (read from .env / environment variables)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM (GapGPT, OpenAI-compatible) ---
    gapgpt_api_key: str = ""
    gapgpt_base_url: str = "https://api.gapgpt.app/v1"
    chat_model: str = "gpt-4.1"
    fast_model: str = "gpt-4.1-mini"
    vision_model: str = "gpt-4.1"
    transcribe_model: str = "whisper-1"
    llm_timeout: float = 90.0

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""  # optional: pre-bind owner chat id
    telegram_api_base: str = "https://api.telegram.org"
    telegram_proxy: str = ""  # e.g. socks5://127.0.0.1:1080 or http://...

    # --- App ---
    timezone: str = "Asia/Tehran"
    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'peygir.db'}"
    app_password: str = ""  # if set, web UI requires login
    secret_key: str = "change-me-please"
    host: str = "0.0.0.0"
    port: int = 8000
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 30
    frontend_dist: str = str(ROOT_DIR / "frontend" / "dist")
    upload_dir: str = str(ROOT_DIR / "data" / "uploads")


@lru_cache
def get_settings() -> Settings:
    return Settings()
