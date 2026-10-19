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
    ielts_model: str = ""  # grading essays/speaking; empty = chat_model
    transcribe_model: str = "gapgpt/whisper-1"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "nova"
    embedding_model: str = "text-embedding-3-small"
    # tried in order when the main model fails (comma separated)
    fallback_models: str = "gpt-4o,gpt-4.1-mini"
    transcribe_fallback_models: str = "whisper-1"
    tts_fallback_models: str = "tts-1"
    llm_timeout: float = 90.0
    llm_proxy: str = ""  # e.g. http://user:pass@host:port — if GapGPT isn't reachable directly
    llm_streaming: bool = True
    # optional price overrides: {"model": [input_per_1M, output_per_1M]}
    model_prices: dict[str, list[float]] = {}

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""  # optional: pre-bind owner chat id
    telegram_api_base: str = "https://api.telegram.org"
    telegram_proxy: str = ""  # e.g. socks5://127.0.0.1:1080 or http://...

    # --- Web push (PWA) ---
    vapid_subject: str = "mailto:admin@example.com"

    # --- App ---
    timezone: str = "Asia/Tehran"
    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'peygir.db'}"
    app_password: str = ""  # if set, web UI requires login
    secret_key: str = "change-me-please"
    host: str = "0.0.0.0"
    port: int = 8000
    scheduler_enabled: bool = True
    background_jobs: bool = True
    scheduler_interval_seconds: int = 30
    frontend_dist: str = str(ROOT_DIR / "frontend" / "dist")
    upload_dir: str = str(ROOT_DIR / "data" / "uploads")


@lru_cache
def get_settings() -> Settings:
    return Settings()
