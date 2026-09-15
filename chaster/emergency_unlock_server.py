"""
chaster/emergency_unlock_server.py

Entry point for the PC-local emergency-unlock safety plane, run as a
SEPARATE standalone process from the Discord bot
(`python3 -m chaster.emergency_unlock_server`) -- never imported by,
and never imports, `bot/discord_bot.py`, `application/service.py`,
`conversation_engine/`, or anything else that constitutes this
project's normal control plane. This is deliberate and load-bearing:
see chaster/emergency_unlock_listener.py's own docstring for why.

Refuses to start at all if `CHASTER_EMERGENCY_UNLOCK_SECRET` or
`CHASTER_TOKEN_ENCRYPTION_KEY` is unset -- an unauthenticatable
emergency server, or one that could never actually decrypt a stored
connection, is worse than no emergency server at all, since it could
give a false sense that the safety plane is ready when it is not.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from chaster.emergency_unlock import EmergencyUnlockService
from chaster.emergency_unlock_listener import EmergencyUnlockListener
from chaster.emergency_unlock_provider import RealChasterEmergencyUnlockProvider
from chaster.lock_client import ChasterLockClient
from chaster.repository import ChasterConnectionRepository
from chaster.token_encryptor import TokenEncryptor, TokenEncryptorConfigurationError
from core.config import Config
from infrastructure.clock import SystemClock
from infrastructure.database import Database as CoreDatabase

logger = logging.getLogger("ai_coach_keyholder.chaster.emergency_unlock_server")


class EmergencyServerConfigurationError(RuntimeError):
    """Raised at startup when required configuration is missing --
    never logs or includes any secret value."""


def build_listener(config: Config, *, core: CoreDatabase | None = None) -> EmergencyUnlockListener:
    if not config.chaster_emergency_unlock_secret:
        raise EmergencyServerConfigurationError(
            "CHASTER_EMERGENCY_UNLOCK_SECRET is not set. The emergency-unlock server refuses "
            "to start without a dedicated secret -- it will never run unauthenticated."
        )
    if not config.chaster_token_encryption_key:
        raise EmergencyServerConfigurationError(
            "CHASTER_TOKEN_ENCRYPTION_KEY is not set. The emergency-unlock server cannot "
            "decrypt a stored Chaster connection without it."
        )

    core = core if core is not None else CoreDatabase(config.db_path)
    try:
        encryptor = TokenEncryptor(config.chaster_token_encryption_key)
    except TokenEncryptorConfigurationError as exc:
        raise EmergencyServerConfigurationError(str(exc)) from exc

    connections = ChasterConnectionRepository(config.db_path, core=core, encryptor=encryptor)
    service = EmergencyUnlockService(
        config.db_path, core=core, connections=connections,
        provider=RealChasterEmergencyUnlockProvider(lock_client=ChasterLockClient()),
        expected_secret=config.chaster_emergency_unlock_secret,
    )
    return EmergencyUnlockListener(
        bind_host=config.chaster_emergency_bind_host,
        bind_port=config.chaster_emergency_bind_port,
        service=service, clock=SystemClock(),
    )


async def _run() -> None:
    config = Config.load()
    listener = build_listener(config)
    await listener.start()
    logger.warning(
        "Chaster emergency-unlock server is running. NOTE: the real Chaster emergency-unlock "
        "endpoint and Option A target-lock discovery are implemented, but Chaster's exact "
        "LockForWearer response schema is not yet confirmed from an authoritative source -- "
        "every real request will currently report failure at the eligibility-check step. "
        "See docs/architecture/chaster_integration_technical_design.md Section 25c."
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # signal handlers are not available on some platforms (e.g. Windows) -- Ctrl+C still raises KeyboardInterrupt

    try:
        await stop_event.wait()
    finally:
        await listener.stop()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
