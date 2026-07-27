"""
bot/discord_bot.py

Basic Discord bot skeleton for AI Coach & Keyholder — Phase 0.

Responsibility in this phase is deliberately limited to verifying the
communication layer:
  - connecting to Discord,
  - receiving messages,
  - saving every message to conversation_messages (short-term memory),
  - a simple acknowledgement reply, NO AI logic yet.

The connection to the Coach/Keyholder/Decision engine will land in
Phase 1+ at the spot marked `# TODO(phase 1)` below -- so it's clear
where the bot will hook into the core/ layer once it's ready.

Running:
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
        logger.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")
        logger.info("Ready on %d server(s).", len(self.guilds))

    async def on_message(self, message: discord.Message) -> None:
        # Ignore the bot's own messages so it doesn't loop on itself
        if message.author == self.user:
            return

        # Short-term memory: log every user message
        self.db.save_conversation_message(
            ConversationMessage(
                created_at=self.clock.now(),
                role=MessageRole.USER,
                content=message.content,
                discord_channel_id=str(message.channel.id),
                discord_message_id=str(message.id),
            )
        )

        # TODO(phase 1): instead of a fixed reply, call:
        #   1. context_engine -> ContextSnapshot
        #   2. coach_engine + keyholder_engine -> CoachAssessment / KeyholderAssessment
        #   3. decision_engine -> DecisionResult
        #   4. ai/personality.py -> synthesize a single voice (+ explanation, if
        #      decision.requires_user_approval or impact_score.is_significant)
        # For now, we're only verifying that the communication layer works.
        reply_text = (
            "Recorded. (Phase 0 -- no AI logic yet, just verifying the connection.)"
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
    intents.message_content = True  # required to read message content (privileged intent)
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

    # Migrations (they take care of their own backup before applying, if the DB already exists)
    applied = db.migrate(now=clock.now())
    if applied:
        logger.info("Applied migrations: %s", applied)

    # At most 1 automatic backup per day, even outside migrations
    daily_backup = db.ensure_daily_backup(now=clock.now())
    if daily_backup:
        logger.info("Created daily backup: %s", daily_backup)

    bot = build_bot(config, db, clock)
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
