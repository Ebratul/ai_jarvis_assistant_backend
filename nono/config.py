"""NONO backend configuration (environment / .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class Settings:
    host: str = field(default_factory=lambda: env("NONO_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(env("NONO_PORT", "8000")))
    api_key: str = field(default_factory=lambda: env("NONO_API_KEY"))
    cors_origins: list[str] = field(
        default_factory=lambda: [o.strip() for o in env("CORS_ORIGINS", "*").split(",") if o.strip()]
    )
    data_dir: Path = field(default_factory=lambda: Path(env("NONO_DATA_DIR", "./data")).resolve())

    user_name: str = field(default_factory=lambda: env("USER_NAME", "Boss"))
    timezone: str = field(default_factory=lambda: env("TIMEZONE", "Asia/Dhaka"))
    language: str = field(default_factory=lambda: env("LANGUAGE", "en-US"))

    openrouter_api_key: str = field(default_factory=lambda: env("OPENROUTER_API_KEY"))
    groq_api_key: str = field(default_factory=lambda: env("GROQ_API_KEY"))
    hf_token: str = field(default_factory=lambda: env("HF_TOKEN"))
    openai_api_key: str = field(default_factory=lambda: env("OPENAI_API_KEY"))
    gemini_api_key: str = field(default_factory=lambda: env("GEMINI_API_KEY"))
    ollama_base_url: str = field(default_factory=lambda: env("OLLAMA_BASE_URL", "http://localhost:11434"))
    default_provider: str = field(default_factory=lambda: env("DEFAULT_PROVIDER", "openrouter"))
    default_model: str = field(default_factory=lambda: env("DEFAULT_MODEL", "openrouter/free"))

    elevenlabs_api_key: str = field(default_factory=lambda: env("ELEVENLABS_API_KEY"))
    elevenlabs_model: str = field(default_factory=lambda: env("ELEVENLABS_MODEL", "eleven_flash_v2_5"))
    default_voice: str = field(default_factory=lambda: env("DEFAULT_VOICE", "prime"))

    telegram_bot_token: str = field(default_factory=lambda: env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: env("TELEGRAM_CHAT_ID"))
    google_calendar_id: str = field(default_factory=lambda: env("GOOGLE_CALENDAR_ID", "primary"))

    rag_url: str = field(default_factory=lambda: env("RAG_URL", "http://localhost:8100").rstrip("/"))
    rag_api_key: str = field(default_factory=lambda: env("RAG_API_KEY"))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "nono.db"

    @property
    def google_token_path(self) -> Path:
        return self.data_dir / "google_token.json"


settings = Settings()
