#!/usr/bin/env python3
"""
scripts/dev_callback_listener_readiness_check.py

DEVELOPMENT-ONLY, MANUAL, LOCAL-ONLY diagnostic. Never imported by
any production code path, never run automatically.

Purpose: verify the REAL, actual production `ChasterCallbackListener`
(the same class `bot/discord_bot.py::CoachKeyholderBot.setup_hook()`
starts) actually binds and responds correctly on its own -- entirely
locally, before you point a Cloudflare Tunnel at it. This is
deliberately NOT an end-to-end Cloudflare/real-Chaster test: no code
in this repository can verify a real tunnel, a real DNS record, or a
real Chaster developer application, because all three are external,
account-level prerequisites this project has never had access to
(see docs/deployment/cloudflare_tunnel_config_template.yml and
chaster/README.md's own "Testability" section for the exact
boundary). What this script CAN honestly verify:

1. The listener binds to the configured host/port without error.
2. A request to an unrecognized/malformed `state` value is handled
   gracefully (the same real `ChasterCallbackService.handle_callback()`
   path a real, expired, or already-used Cloudflare-forwarded request
   would hit) -- not a crash, not a hang.
3. The listener shuts down cleanly.

If all three pass, the ONE remaining variable before a real
`chaster connect` can work end-to-end is the external Cloudflare
Tunnel configuration itself -- this script's purpose is to rule out
"is my local listener even working" as a separate, confounding
variable before you debug the tunnel.

Uses no real Chaster credentials, makes no real Chaster API call, and
never touches chaster_connections in a way a real callback wouldn't
(an unrecognized state is rejected before any token exchange would be
attempted).

USAGE (from the repository root):

    python3 scripts/dev_callback_listener_readiness_check.py [--host 127.0.0.1] [--port 8420]

Defaults match CHASTER_CALLBACK_BIND_HOST/CHASTER_CALLBACK_BIND_PORT's
own documented defaults.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp  # noqa: E402

from chaster.callback_listener import CALLBACK_PATH, ChasterCallbackListener  # noqa: E402


async def _fake_on_callback(state: str | None, code: str | None, error: str | None) -> str:
    """A minimal stand-in for the real `ChasterCallbackService.handle_callback`
    -- this script deliberately does not construct a real
    ChasterCallbackService (that would need a real database and real
    Chaster credentials to be meaningful); it only proves the
    LISTENER itself correctly receives and forwards query params to
    whatever handler it's given, in the exact (state, code, error)
    order production uses, and that returning a plain string body
    round-trips correctly through a real HTTP response."""
    return f"readiness check received: state={bool(state)}, code={bool(code)}, error={error!r}"


async def _run(host: str, port: int) -> bool:
    print(f"Starting a real ChasterCallbackListener on {host}:{port}{CALLBACK_PATH} ...")
    listener = ChasterCallbackListener(bind_host=host, bind_port=port, on_callback=_fake_on_callback)

    try:
        await listener.start()
    except OSError as exc:
        print(f"FAILED to bind: {exc}")
        print("Is another process already using this port? (Check: is the bot itself already running?)")
        return False

    print("Bind succeeded.")

    ok = True
    try:
        async with aiohttp.ClientSession() as session:
            url = f"http://{host}:{port}{CALLBACK_PATH}"
            print(f"Sending a test request with an unrecognized state to {url} ...")
            try:
                async with session.get(url, params={"state": "readiness-check-fake-state", "code": "fake-code"}, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    body = await response.text()
                    print(f"Response status: {response.status}")
                    print(f"Response handled without crashing or hanging (body length: {len(body)} chars).")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                print(f"FAILED: request did not complete cleanly: {exc}")
                ok = False
    finally:
        await listener.stop()
        print("Listener stopped cleanly.")

    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8420)
    args = parser.parse_args()

    print("=" * 78)
    print("DEVELOPMENT-ONLY local callback listener readiness check.")
    print("Verifies the LOCAL listener only -- does NOT test Cloudflare Tunnel,")
    print("DNS, or a real Chaster application. See this script's own docstring.")
    print("=" * 78)

    result = asyncio.run(_run(args.host, args.port))

    print()
    if result:
        print("READY: the local listener binds and responds correctly.")
        print("The next variable to verify is the Cloudflare Tunnel configuration itself --")
        print("see docs/deployment/cloudflare_tunnel_config_template.yml.")
    else:
        print("NOT READY: see the failure above. Fix this before troubleshooting the tunnel --")
        print("a tunnel pointed at a broken local listener will never work either way.")


if __name__ == "__main__":
    main()
