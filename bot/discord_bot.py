"""
bot/discord_bot.py

The Discord adapter — Phase 3.1's minimal vertical slice. Deliberately
a THIN layer: this file's only responsibilities are

  1. connecting to Discord and filtering to direct messages,
  2. converting a discord.Message into a channel-agnostic
     application.models.IncomingMessage,
  3. calling application.service.ApplicationService.handle_message()
     (the ONLY call this file makes into the rest of the system --
     never a domain module directly, never a `_*_in_transaction` method),
  4. sending the returned OutgoingMessage.text back to the same DM,
  5. raw audit logging of both sides of the exchange (Phase 0's
     conversation_messages table -- an adapter-level concern, since the
     Discord-specific channel/message ids it records are inherently
     about THIS channel, not something the channel-agnostic application
     layer should know about),
  6. catching and logging any exception so a single bad message can
     never crash the bot or leak an internal error to the user.

No command parsing, no domain logic, and no decision-making lives here
-- see application/README.md for the actual boundary and
application/service.py for what "status"/"help" actually do.

Running:
    python -m bot.discord_bot
"""

from __future__ import annotations

import logging
import uuid

import discord

from application.models import IncomingMessage
from application.service import ApplicationService
from core.config import Config, ConfigError
from database.database import Database
from database.models import ConversationMessage, MessageRole
from infrastructure.clock import Clock, SystemClock
from infrastructure.database import Database as CoreDatabase
from system.startup import StartupLeaseNotAcquired, on_system_startup

logger = logging.getLogger("ai_coach_keyholder.bot")


class CoachKeyholderBot(discord.Client):
    def __init__(
        self, config: Config, db: Database, clock: Clock, application_service: ApplicationService,
        *, intents: discord.Intents,
    ):
        super().__init__(intents=intents)
        self.config = config
        self.db = db
        self.clock = clock
        self.application_service = application_service

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")
        logger.info("Ready. Listening for direct messages only (Phase 3.1 scope).")

    async def on_message(self, message: discord.Message) -> None:
        # Ignore the bot's own messages so it doesn't loop on itself.
        if message.author == self.user:
            return

        # DM only, per this phase's explicit scope -- anything sent in
        # a server channel is silently ignored, not processed and not
        # replied to.
        if not isinstance(message.channel, discord.DMChannel):
            return

        now = self.clock.now()

        # Audit logging is best-effort and never blocks the actual
        # conversation -- a failure here must not prevent a reply from
        # being generated or sent (a real gap found while writing this
        # adapter's own tests: the first implementation coupled the
        # incoming-message log with reply generation in one try/except,
        # so a logging failure produced the generic error reply instead
        # of the real one, and the OUTGOING log was unprotected entirely,
        # able to crash the handler after the user had already gotten a
        # reply).
        self._log_message_best_effort(now, MessageRole.USER, message.content, message.channel.id, message.id)

        try:
            incoming = IncomingMessage(
                channel="discord", external_user_id=str(message.author.id),
                text=message.content, received_at=now, external_message_id=str(message.id),
            )
            # ApplicationService.handle_message() itself never raises
            # (see its own docstring) -- this try/except is a second,
            # independent safety net in case something fails before or
            # after that call, not a substitute for that guarantee.
            outgoing = self.application_service.handle_message(incoming)
            reply_text = outgoing.text
        except Exception:
            logger.exception("Unhandled error processing a DM from user_id=%s", message.author.id)
            reply_text = "Something went wrong handling that. It's been logged."

        # Whatever state this reply reflects (including any onboarding
        # step transition) was already written by handle_message()
        # above -- write-before-send throughout
        # application/onboarding_service.py specifically so a failure
        # here can never leave persisted state inconsistent with what
        # was (or wasn't) shown to the user. Wrapped, not left to
        # propagate: a transient Discord API failure sending one reply
        # is not a reason to let an unhandled exception surface through
        # discord.py's own event dispatch -- the same "one bad message
        # never crashes the bot" posture this file already applies to
        # audit logging.
        try:
            sent = await message.channel.send(reply_text)
        except Exception:
            logger.exception("Failed to send a reply to user_id=%s -- persisted state is unaffected.", message.author.id)
            return

        self._log_message_best_effort(self.clock.now(), MessageRole.ASSISTANT, reply_text, message.channel.id, sent.id)

    def _log_message_best_effort(self, created_at, role: MessageRole, content: str, channel_id, message_id) -> None:
        try:
            self.db.save_conversation_message(
                ConversationMessage(
                    created_at=created_at, role=role, content=content,
                    discord_channel_id=str(channel_id), discord_message_id=str(message_id),
                )
            )
        except Exception:
            logger.exception("Failed to log a conversation message (role=%s) -- continuing anyway.", role.value)


def build_bot(config: Config, db: Database, clock: Clock, application_service: ApplicationService) -> CoachKeyholderBot:
    intents = discord.Intents.default()
    intents.message_content = True  # required to read message content (privileged intent)
    return CoachKeyholderBot(config, db, clock, application_service, intents=intents)


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
    core = CoreDatabase(config.db_path)
    db = Database(config.db_path, backup_retention=config.backup_retention_count, core=core)

    # Migrations (they take care of their own backup before applying, if the DB already exists)
    applied = db.migrate(now=clock.now())
    if applied:
        logger.info("Applied migrations: %s", applied)

    # At most 1 automatic backup per day, even outside migrations
    daily_backup = db.ensure_daily_backup(now=clock.now())
    if daily_backup:
        logger.info("Created daily backup: %s", daily_backup)

    # system_state_machine.md Section 7: on_system_startup() must run
    # BEFORE the Discord bot starts / before the first request is
    # accepted -- this was never actually wired into main() until now
    # (Phase 0-2.x had no adapter calling it at all). process_id is a
    # fresh uuid per process start, sufficient for the startup lease's
    # purpose (distinguishing THIS run from a concurrently-running one).
    try:
        on_system_startup(core, str(uuid.uuid4()), clock)
    except StartupLeaseNotAcquired:
        logger.error("Another instance is already performing startup reconciliation. Exiting.")
        raise SystemExit(1)

    application_service = ApplicationService(config.db_path, core=core)
    bot = build_bot(config, db, clock, application_service)
    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
