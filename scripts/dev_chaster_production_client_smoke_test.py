#!/usr/bin/env python3
"""
scripts/dev_chaster_production_client_smoke_test.py

DEVELOPMENT-ONLY, MANUAL, LOCAL smoke test. Never imported by any
production code path, never run automatically, never exposed through
Discord, the emergency listener, or Cloudflare.

Purpose: prove the actual shipped production path works end-to-end
against the real Chaster API:

    ChasterLockClient.list_active_locks()
    -> chaster._http.send_with_ordered_headers()
    -> real Chaster API

This is deliberately NOT a new diagnostic transport -- it imports and
calls the existing production `chaster.lock_client.ChasterLockClient`
directly, exactly as `chaster/emergency_unlock_provider.py` does in
production. No HTTP logic is duplicated here.

Also makes ONE additional real, read-only call via
`ChasterOAuthClient.fetch_raw_profile()` -- a development/manual-test
-only method (never called from `chaster/callback_service.py`'s real
production callback flow, which still uses the unimplemented
`unconfirmed_identity_resolver`) that now routes through the same
`send_with_ordered_headers()` helper. This has never actually been
exercised against the real API since that transport fix landed, so
this script now also reports whether `/auth/profile` succeeds and,
if so, its real structure -- the same evidence-first approach that
was needed before `LockForWearer`'s own schema could be worked with,
now applied to `CurrentUser`. This does NOT implement identity
resolution -- it only observes real structure, safely.

Makes exactly TWO real, read-only calls:
    GET https://api.chaster.app/locks?status=active
    GET https://api.chaster.app/auth/profile

Never calls any write endpoint, never calls `/locks/{lockId}/unlock`
or `/locks/{lockId}/emergency-unlock`, never touches the database,
never uses Discord, never uses the OAuth callback flow.

SECURITY, BY DESIGN:
- The developer token is read from the `CHASTER_DEV_TOKEN`
  environment variable only -- never a command-line argument, never
  a hardcoded value, never requested interactively (this script is
  meant to be run in a shell where you've already set the variable
  for this one invocation).
- The token is never printed, logged, or written anywhere.
- Only safe, derived diagnostic information is printed: HTTP success/
  failure, the count of returned active locks, and a RECURSIVE
  structural summary of the first lock object -- key names and
  Python/JSON type names only, at every nesting depth, NEVER any
  actual value. This exists specifically to safely reveal the shape
  of nested fields (in particular `extensions`, the leading candidate
  for where bondage/emergency-release configuration lives) without
  ever exposing personal lock data, matching this project's own
  "do not guess a nested schema, observe it structurally instead"
  discipline -- see docs/architecture/chaster_integration_technical_design.md.

USAGE (from the repository root, either platform):

    CHASTER_DEV_TOKEN=<your token>  python3 scripts/dev_chaster_production_client_smoke_test.py

(On Windows PowerShell, set it for the one command instead of the
whole session -- see this script's own final report for the exact
command.)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chaster.lock_client import ChasterLockApiError, ChasterLockClient  # noqa: E402
from chaster.oauth_client import ChasterOAuthClient, ChasterTokenExchangeError  # noqa: E402


def print_schema(data: object, indent: int = 0) -> None:
    """Recursively prints the STRUCTURE of a JSON-shaped value --
    dict keys and Python types only. NEVER prints an actual primitive
    value (string, int, bool, etc.) -- only `type(value)`. Exists
    specifically to safely reveal the shape of nested fields (in
    particular `extensions`, the leading candidate for where
    bondage/emergency-release configuration lives) without ever
    exposing personal lock data, matching this project's own "do not
    guess a nested schema, observe it structurally instead"
    discipline -- see docs/architecture/chaster_integration_technical_design.md.

    - dict: prints each key name and the Python type of its value,
      then recurses into that value.
    - list: prints that it is a list (with its length), then -- if
      non-empty -- recurses into only the FIRST element, since every
      element of a given list is assumed to share the same shape and
      showing more risks exposing more personal data than necessary
      for no additional structural information.
    - anything else (a primitive): nothing further to print here --
      its type was already printed by the caller (the enclosing dict
      key, or the list-length line for a list of primitives, handled
      below)."""
    prefix = "  " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            print(f"{prefix}{key}: {type(value)}")
            print_schema(value, indent + 1)
    elif isinstance(data, list):
        print(f"{prefix}(list, length={len(data)})")
        if data:
            first = data[0]
            if isinstance(first, (dict, list)):
                print_schema(first, indent + 1)
            else:
                print(f"{prefix}  element type: {type(first)}")


def print_extension_config_keys(extensions: object) -> None:
    """Iterates EVERY entry in the lock's `extensions` list (not just
    the first, unlike `print_schema()`'s general list handling) --
    since each active extension has its own genuinely distinct
    `config` shape (confirmed active slugs on this account include
    'pillory', 'penalty', 'verification-picture', 'temporary-opening',
    'unlock-condition', 'link', 'wheel-of-fortune',
    'programmable-lock'), and an emergency-release/safety-feature flag
    could live inside any one of them -- not necessarily the first.
    Prints, for each extension entry: its own top-level key names, and
    its `config` dict's key names if present. NEVER prints any actual
    value -- not even an extension's own slug/type/name string --
    only key names and, where relevant, types. This is deliberately
    stricter than `print_schema()`: even an identifier value that
    might seem harmless is withheld here, since this function's whole
    purpose is safe for use against any account's real, live data."""
    if not isinstance(extensions, list):
        print(f"  extensions is not a list (type: {type(extensions)}) -- skipping.")
        return
    if not extensions:
        print("  extensions list is empty -- nothing to inspect.")
        return
    for index, entry in enumerate(extensions):
        if not isinstance(entry, dict):
            print(f"  [{index}] extension entry is not a dict (type: {type(entry)}) -- skipping.")
            continue
        print(f"  [{index}] extension top-level keys: {sorted(entry.keys())}")
        config = entry.get("config")
        if isinstance(config, dict):
            print(f"      config keys: {sorted(config.keys())}")
        elif config is None:
            print("      (no 'config' key present on this extension)")
        else:
            print(f"      'config' present but not a dict (type: {type(config)})")


def main() -> None:
    print("=" * 78)
    print("DEVELOPMENT-ONLY production-client smoke test.")
    print("Makes exactly TWO real, read-only GET requests:")
    print("  GET https://api.chaster.app/locks?status=active")
    print("  GET https://api.chaster.app/auth/profile")
    print("via the actual production ChasterLockClient / a dev-only ChasterOAuthClient")
    print("diagnostic method -- no duplicated HTTP logic, both already route through")
    print("the same fixed chaster._http.send_with_ordered_headers() helper.")
    print("Never makes any unlock/write request of any kind.")
    print("Your token is never echoed, logged, written to a file, or printed.")
    print("=" * 78)

    token = os.environ.get("CHASTER_DEV_TOKEN", "").strip()
    if not token:
        print("\nCHASTER_DEV_TOKEN is not set (or empty) -- aborting. No request was made.")
        print("Set it for this one command, e.g.:")
        print('  CHASTER_DEV_TOKEN="<your token>" python3 scripts/dev_chaster_production_client_smoke_test.py')
        return

    print("\n--- GET /locks?status=active (via the production ChasterLockClient) ---")
    lock_client = ChasterLockClient()
    try:
        locks = lock_client.list_active_locks(access_token=token)
    except ChasterLockApiError as exc:
        print(f"FAILED: {exc}")
    except ValueError as exc:
        print(f"FAILED (invalid input, no request was made): {exc}")
    else:
        print("SUCCESS: HTTP 200, response parsed as a JSON list.")
        print(f"Active locks returned: {len(locks)}")
        if locks and isinstance(locks[0], dict):
            print("\nRecursive schema of the first lock object (key names and TYPES only -- no values):")
            print_schema(locks[0])

            if "extensions" in locks[0]:
                print("\nPer-extension config key names (ALL extensions, not just the first -- key names only, no values):")
                print_extension_config_keys(locks[0]["extensions"])

    print("\n--- GET /auth/profile (via the dev-only ChasterOAuthClient.fetch_raw_profile()) ---")
    print("(This method is never called from the real production callback flow --")
    print(" chaster/callback_service.py still uses the unimplemented")
    print(" unconfirmed_identity_resolver. This is observation only.)")
    # fetch_raw_profile() uses no instance state at all -- these
    # placeholder values are never read; see chaster/oauth_client.py.
    oauth_client = ChasterOAuthClient(client_id="dev-smoke-test", client_secret="dev-smoke-test", redirect_uri="https://example.invalid/unused")
    try:
        profile = oauth_client.fetch_raw_profile(access_token=token)
    except ChasterTokenExchangeError as exc:
        print(f"FAILED: {exc}")
    else:
        print("SUCCESS: HTTP 200, response parsed as a JSON object.")
        if isinstance(profile, dict):
            print("\nRecursive schema of the profile object (key names and TYPES only -- no values):")
            print_schema(profile)

    print("\nDone. No unlock request was made. The token above is not stored anywhere.")


if __name__ == "__main__":
    main()
