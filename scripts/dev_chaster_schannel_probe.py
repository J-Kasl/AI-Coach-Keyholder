#!/usr/bin/env python3
"""
scripts/dev_chaster_schannel_probe.py

DEVELOPMENT-ONLY, MANUAL, WINDOWS-ONLY SPIKE. Never imported by any
production code path (`bot/discord_bot.py`, `application/`,
`chaster/emergency_unlock*.py`, `chaster/callback_service.py`,
`chaster/oauth_client.py`, `chaster/lock_client.py`), never run
automatically, never exposed through Discord, the emergency listener,
or Cloudflare.

Purpose: answer exactly one question -- does a real Windows Schannel
`requests`-compatible transport (the `requests-schannel` PyPI
package: https://pypi.org/project/requests-schannel/) make
`GET /locks?status=active` return HTTP 200 on this machine, where the
project's existing OpenSSL-backed `requests` calls return HTTP 400?
This is a proof-of-concept spike, NOT a production transport change --
`chaster/oauth_client.py` and `chaster/lock_client.py` are untouched,
`requirements.txt` is untouched, and `requests-schannel` is NOT a
project dependency. You install it yourself, locally, only to run
this one script.

Requires (Windows only, not installable/importable on this sandbox's
Linux environment -- that is expected and acceptable; see this
project's own audit notes for why):

    pip install requests-schannel[requests]

`requests-schannel` (reviewed via its own PyPI project page and
release/attestation metadata -- NOT via a full source-code read,
which this project's own research could not reach; see the audit
report accompanying this script for that caveat) is, per its own
documentation:
- `Development Status :: 3 - Alpha`, single maintainer, ~10 days of
  release history as of this writing -- a real, honest maturity risk,
  which is exactly why this stays a throwaway spike script, not a
  requirements.txt entry.
- Published via PyPI Trusted Publishing with Sigstore attestations
  (a legitimate modern supply-chain practice, not typical of a scam
  package) -- a positive signal, but not a substitute for reading the
  actual source before trusting it in anything beyond a spike.
- Documented defaults: `verify_mode = ssl.CERT_REQUIRED`,
  `check_hostname` enabled, native Windows trust store (no bundled CA
  file) -- this script never touches either setting, so those
  documented defaults are what actually run here.

This script uses EXACTLY the documented API
(`from requests_schannel import SchannelAdapter`,
`session.mount("https://", SchannelAdapter())`) -- no invented or
guessed import path.

SECURITY, BY DESIGN (mirrors `scripts/dev_chaster_schema_probe.py`'s
own established conventions in this project):
- The developer token is read via `getpass.getpass()` -- never
  echoed, never touched by shell history, never written to a file,
  never logged, never included in any printed output or exception
  message.
- `CHASTER_CLIENT_ID`, when configured (via this project's own
  `core.config.Config`, exactly as the production clients would read
  it), is included as `X-Chaster-Client-Id` but its VALUE is never
  printed -- it is not a secret like the token, but there is no need
  to display it either.
- Only ONE GET request is ever made: `GET /locks?status=active`. This
  script contains no POST/PUT/PATCH/DELETE call of any kind, and in
  particular never references `/locks/{lockId}/unlock` or
  `/locks/{lockId}/emergency-unlock`.
- TLS certificate and hostname verification are never disabled --
  `verify=False` (or any `SchannelContext.verify_mode`/
  `check_hostname` override) is never used anywhere in this file.
- No TLS-fingerprint spoofing of any kind is attempted -- this script
  either uses `requests-schannel`'s own real Schannel transport
  faithfully, or it doesn't run at all.
- The raw response body is never printed -- only safe, derived
  metadata (status, Content-Type, a redacted header subset, response
  size, whether the body parsed as a JSON array, and the COUNT of
  array elements). No lock IDs, usernames, timestamps, verification
  codes, or other account data are ever printed.

USAGE (Windows only, from the repository root):

    pip install requests-schannel[requests]
    python scripts\\dev_chaster_schannel_probe.py

You will be prompted for your developer token; nothing else is
required. The token exists only in local memory for the duration of
this one process and is discarded when it exits.
"""

from __future__ import annotations

import getpass
import importlib.metadata
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LOCKS_URL = "https://api.chaster.app/locks"
SENSITIVE_KEY_SUBSTRINGS = ("token", "secret", "password", "auth", "email", "key", "credential")


def _redact(value: object) -> object:
    """Recursively redacts any dict value whose key name contains a
    sensitive-looking substring. Mirrors
    `scripts/dev_chaster_schema_probe.py::redact()` exactly -- kept
    as an independent copy so this spike script remains fully
    self-contained and independently auditable, matching this
    project's own convention for every dev-only script."""
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if any(s in k.lower() for s in SENSITIVE_KEY_SUBSTRINGS) else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def main() -> None:
    print("=" * 78)
    print("DEVELOPMENT-ONLY Chaster Schannel transport spike (Windows only).")
    print("Makes exactly ONE real, read-only GET request:")
    print("  GET https://api.chaster.app/locks?status=active")
    print("Never makes any unlock/write request of any kind.")
    print("Your token is never echoed, logged, written to a file, or printed.")
    print("=" * 78)

    try:
        import requests
    except ImportError:
        print("\n'requests' is not installed in this environment. Install it and retry.")
        return

    try:
        from requests_schannel import SchannelAdapter
    except ImportError:
        print(
            "\n'requests-schannel' is not installed. This is expected on the Claude "
            "sandbox (Windows-only package) and on any machine where you haven't yet "
            "run:\n\n    pip install requests-schannel[requests]\n\n"
            "Install it on your Windows machine and rerun this script there."
        )
        return

    try:
        schannel_version = importlib.metadata.version("requests-schannel")
    except importlib.metadata.PackageNotFoundError:
        schannel_version = "unknown (package metadata not found)"
    print(f"\nTransport: requests-schannel {schannel_version} (SchannelAdapter)")
    print(f"requests: {requests.__version__}")

    from core.config import Config

    client_id = Config.load().chaster_client_id

    token = getpass.getpass("\nEnter your Chaster developer token (input hidden, not stored): ").strip()
    if not token:
        print("No token entered -- aborting. No request was made.")
        return

    headers = {"Authorization": f"Bearer {token}"}
    if client_id:
        headers["X-Chaster-Client-Id"] = client_id
        print("X-Chaster-Client-Id: configured, included (value not shown).")
    else:
        print("X-Chaster-Client-Id: not configured -- proceeding without it.")

    # Documented defaults only -- no verify=False, no check_hostname
    # override, no cipher/version override, no fingerprint spoofing.
    session = requests.Session()
    session.mount("https://", SchannelAdapter())

    print("\nMaking the request...")
    try:
        response = session.get(LOCKS_URL, params={"status": "active"}, headers=headers, timeout=15.0)
    except requests.RequestException as exc:
        print(f"Request failed: {exc}")
        return

    print(f"\nHTTP status: {response.status_code}")
    print(f"Content-Type: {response.headers.get('Content-Type', '<none>')}")
    print(f"Response size: {len(response.content)} bytes")
    safe_headers = {
        k: v for k, v in response.headers.items()
        if not any(s in k.lower() for s in SENSITIVE_KEY_SUBSTRINGS)
    }
    print(f"Response headers (redacted): {_redact(safe_headers)}")

    try:
        body = response.json()
    except ValueError:
        print("Response body did not parse as JSON.")
        return

    if isinstance(body, list):
        print(f"Response body parsed as a JSON array with {len(body)} element(s). Contents not shown.")
    else:
        print(f"Response body parsed as JSON but was not an array (type: {type(body).__name__}). Contents not shown.")

    print("\nDone. No unlock request was made. The token above is not stored anywhere.")


if __name__ == "__main__":
    main()
