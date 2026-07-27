"""
core/config.py

Central configuration and secret loading. Deliberately without a
dependency on python-dotenv -- our own minimal loader gives us full
control over error messages (an explicit error for a missing required
variable, rather than a silent None that only surfaces deep inside a
Discord/Ollama call).

The .env file must NEVER go into git -- see .env.example as a template.
The Discord token, any Chaster/Apple Health keys, and other sensitive
values live exclusively in .env, never hardcoded.

Usage:
    from core.config import Config
    config = Config.load()
    print(config.discord_token)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "coach_keyholder.db"


class ConfigError(Exception):
    """Raised when a required configuration value is missing."""


def _parse_env_file(path: Path) -> dict[str, str]:
    """
    Minimalist .env parser: KEY=VALUE per line, # as a comment, blank
    lines ignored, values are not further processed (no escape
    sequences) -- sufficient for secrets and simple configuration.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value

    return values


@dataclass
class Config:
    # Discord
    discord_token: str
    discord_command_prefix: str = "!"

    # Ollama
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"  # TBD until a specific model is confirmed

    # Database
    db_path: Path = DEFAULT_DB_PATH
    backup_retention_count: int = 14  # how many recent automatic backups to keep

    # Integrations (optional -- Phase 7, just scaffolded for now)
    chaster_api_token: str | None = None
    apple_health_api_key: str | None = None

    # General
    log_level: str = "INFO"
    quiet_hours_start: str = "22:00"   # for a future scheduler (Phase 5)
    quiet_hours_end: str = "07:00"

    @classmethod
    def load(cls, env_path: Path | None = None) -> Config:
        """
        Loads configuration: real environment variables first (they
        take precedence, useful for CI/containers), then fills in from
        the .env file. Raises ConfigError if DISCORD_TOKEN is missing.
        """
        env_path = env_path or DEFAULT_ENV_PATH
        file_values = _parse_env_file(env_path)

        def get(key: str, default: str | None = None) -> str | None:
            return os.environ.get(key) or file_values.get(key) or default

        discord_token = get("DISCORD_TOKEN")
        if not discord_token:
            raise ConfigError(
                f"DISCORD_TOKEN is not set. Copy .env.example to .env "
                f"(expected path: {env_path}) and fill in the bot token."
            )

        db_path_str = get("DB_PATH")
        retention_str = get("BACKUP_RETENTION_COUNT", "14")

        return cls(
            discord_token=discord_token,
            discord_command_prefix=get("DISCORD_COMMAND_PREFIX", "!"),
            ollama_host=get("OLLAMA_HOST", "http://localhost:11434"),
            ollama_model=get("OLLAMA_MODEL", "llama3.1"),
            db_path=Path(db_path_str) if db_path_str else DEFAULT_DB_PATH,
            backup_retention_count=int(retention_str) if retention_str else 14,
            chaster_api_token=get("CHASTER_API_TOKEN"),
            apple_health_api_key=get("APPLE_HEALTH_API_KEY"),
            log_level=get("LOG_LEVEL", "INFO"),
            quiet_hours_start=get("QUIET_HOURS_START", "22:00"),
            quiet_hours_end=get("QUIET_HOURS_END", "07:00"),
        )
