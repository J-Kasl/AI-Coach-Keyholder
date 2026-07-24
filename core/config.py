"""
core/config.py

Centrální konfigurace a načítání secrets. Vědomě bez závislosti na
python-dotenv — vlastní minimální loader nám dá plnou kontrolu nad chybovými
hláškami (explicitní chyba při chybějící povinné proměnné, ne tichý None,
který se projeví až hluboko v Discord/Ollama volání).

.env soubor NIKDY nepatří do gitu — viz .env.example jako šablona.
Discord token, případné Chaster/Apple Health klíče a další citlivé hodnoty
žijí výhradně v .env, nikdy natvrdo v kódu.

Použití:
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
    """Vyhozeno, když chybí povinná konfigurační hodnota."""


def _parse_env_file(path: Path) -> dict[str, str]:
    """
    Minimalistický .env parser: KEY=VALUE na řádek, # jako komentář,
    prázdné řádky ignorovány, hodnoty se nezpracovávají (žádné escape
    sekvence) — pro secrets a jednoduché konfigurace to stačí.
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
    ollama_model: str = "llama3.1"  # TBD dokud nepotvrdíš konkrétní model

    # Database
    db_path: Path = DEFAULT_DB_PATH
    backup_retention_count: int = 14  # kolik posledních automatických záloh ponechat

    # Integrace (volitelné — Fáze 7, zatím jen připraveno)
    chaster_api_token: str | None = None
    apple_health_api_key: str | None = None

    # Obecné
    log_level: str = "INFO"
    quiet_hours_start: str = "22:00"   # pro budoucí scheduler (Fáze 5)
    quiet_hours_end: str = "07:00"

    @classmethod
    def load(cls, env_path: Path | None = None) -> Config:
        """
        Načte konfiguraci: nejdřív skutečné proměnné prostředí (mají přednost,
        užitečné pro CI/kontejnery), pak doplní z .env souboru.
        Vyhodí ConfigError, pokud chybí DISCORD_TOKEN.
        """
        env_path = env_path or DEFAULT_ENV_PATH
        file_values = _parse_env_file(env_path)

        def get(key: str, default: str | None = None) -> str | None:
            return os.environ.get(key) or file_values.get(key) or default

        discord_token = get("DISCORD_TOKEN")
        if not discord_token:
            raise ConfigError(
                f"DISCORD_TOKEN není nastaven. Zkopíruj .env.example do .env "
                f"(očekávaná cesta: {env_path}) a doplň token bota."
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
