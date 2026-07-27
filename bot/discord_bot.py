"""
bot/discord_bot.py

Základní kostra Discord bota pro AI Coach & Keyholder — Fáze 0.

Zodpovědnost v této fázi je záměrně omezená na ověření komunikační vrstvy:
  - připojení k Discordu,
  - příjem zpráv,
  - uložení každé zprávy do conversation_messages (krátkodobá paměť),
  - jednoduchá potvrzovací odpověď, ŽÁDNÁ AI logika zatím.

Napojení na Coach/Keyholder/Decision engine přijde ve Fázi 1+ na místě
označeném `# TODO(fáze 1)` níže — tak, aby bylo zřejmé, kde se bot napojí
na core/ vrstvu, až bude hotová.

Spuštění:
    python -m bot.discord_bot
"""

from __future__ import annotations

import logging

import discord

from core.config import Config, ConfigError
from database.database import Database
from database.models import ConversationMessage, MessageRole
from infrastructure.clock import Clock, SystemClock

logger = logging.getLogger("ai_coach_keyholder.bot")


class CoachKeyholderBot(discord.Client):
    def __init__(self, config: Config, db: Database, clock: Clock, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.config = config
        self.db = db
        self.clock = clock

    async def on_ready(self) -> None:
        logger.info("Přihlášen jako %s (id=%s)", self.user, self.user.id if self.user else "?")
        logger.info("Připraven na %d serverech.", len(self.guilds))

    async def on_message(self, message: discord.Message) -> None:
        # Ignoruj vlastní zprávy bota, ať se nezacyklí
        if message.author == self.user:
            return

        # Krátkodobá paměť: každou zprávu uživatele zalogujeme
        self.db.save_conversation_message(
            ConversationMessage(
                created_at=self.clock.now(),
                role=MessageRole.USER,
                content=message.content,
                discord_channel_id=str(message.channel.id),
                discord_message_id=str(message.id),
            )
        )

        # TODO(fáze 1): místo pevné odpovědi zavolat:
        #   1. context_engine -> ContextSnapshot
        #   2. coach_engine + keyholder_engine -> CoachAssessment / KeyholderAssessment
        #   3. decision_engine -> DecisionResult
        #   4. ai/personality.py -> syntéza jednoho hlasu (+ vysvětlení, pokud
        #      decision.requires_user_approval nebo impact_score.is_significant)
        # Zatím jen ověřujeme, že komunikační vrstva funguje.
        reply_text = (
            "Zaznamenáno. (Fáze 0 — zatím bez AI logiky, jen ověřuju spojení.)"
        )

        sent = await message.channel.send(reply_text)

        self.db.save_conversation_message(
            ConversationMessage(
                created_at=self.clock.now(),
                role=MessageRole.ASSISTANT,
                content=reply_text,
                discord_channel_id=str(message.channel.id),
                discord_message_id=str(sent.id),
            )
        )


def build_bot(config: Config, db: Database, clock: Clock) -> CoachKeyholderBot:
    intents = discord.Intents.default()
    intents.message_content = True  # nutné pro čtení obsahu zpráv (privileged intent)
    return CoachKeyholderBot(config, db, clock, intents=intents)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        config = Config.load()
    except ConfigError as e:
        logger.error(str(e))
        raise SystemExit(1) from e

    logging.getLogger("ai_coach_keyholder").setLevel(config.log_level)

    clock = SystemClock()
    db = Database(config.db_path, backup_retention=config.backup_retention_count)

    # Migrace (samy si zajistí zálohu před aplikací, pokud DB už existuje)
    applied = db.migrate(now=clock.now())
    if applied:
        logger.info("Aplikovány migrace: %s", applied)

    # Max 1 automatická záloha za den, i mimo migrace
    daily_backup = db.ensure_daily_backup(now=clock.now())
    if daily_backup:
        logger.info("Vytvořena denní záloha: %s", daily_backup)

    bot = build_bot(config, db, clock)
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
