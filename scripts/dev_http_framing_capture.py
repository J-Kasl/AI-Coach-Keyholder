#!/usr/bin/env python3
"""
scripts/dev_http_framing_capture.py

DEVELOPMENT-ONLY, MANUAL, LOCAL-ONLY diagnostic. Never imported by
any production code path, never run automatically, never exposed
through Discord, the emergency listener, or Cloudflare.

Purpose: capture the EXACT raw HTTP/1.1 bytes that `requests` (and,
for comparison, bare stdlib `http.client`) actually writes to the
wire, so they can be compared structurally against a known-good,
manually-constructed request -- entirely LOCALLY, with NO network
contact to Chaster or any other real server at all.

Why this is a valid substitute for capturing `requests-schannel`'s
own real traffic directly: a source-level audit of `requests-schannel`
0.2.0 (see docs/architecture/chaster_integration_technical_design.md)
confirmed `SchannelSocket` (its TLS-wrapped socket class) is a pure
byte-level I/O passthrough -- `send()`/`recv()`/`makefile()` only, no
HTTP-layer knowledge whatsoever. `urllib3`'s own `HTTPConnection`
constructs and serializes every HTTP-layer byte (request line,
headers, their order) identically regardless of which TLS backend
(OpenSSL or `requests-schannel`'s Schannel) is plugged in underneath.
Capturing plain-`requests`'s bytes against a local, throwaway TCP
listener is therefore byte-for-byte equivalent to what
`requests-schannel`'s `create_session()` sends over the wire -- this
script never needs `requests-schannel` installed, and produces
identical results on Linux or Windows.

SECURITY, BY DESIGN:
- Binds only to `127.0.0.1` (localhost), on an ephemeral/local-only
  port -- never reachable from the network.
- No real token is ever used or needed -- headers use a clearly
  fake, obviously-placeholder value (`FAKE-TOKEN-FOR-LOCAL-CAPTURE-ONLY`)
  by default; if you pass a real value it is still never sent
  anywhere except this script's own local, throwaway listener, and
  is never printed on its own (the captured bytes ARE printed, since
  that is this script's entire purpose -- do not pass a real token
  when using this script for its intended local-only comparison
  purpose; a fake placeholder is exactly as informative for framing
  analysis, since only STRUCTURE is being compared, never the value).
- No connection to any real server, Chaster or otherwise, is ever
  made.
- The captured request line/headers are printed as-is (that is the
  point of the tool), but nothing beyond the request line and headers
  is ever captured or printed (this script stops reading at the first
  blank line terminating the header block, before any request body).

USAGE (from the repository root, either platform):

    python3 scripts/dev_http_framing_capture.py
"""

from __future__ import annotations

import http.client
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAKE_TOKEN = "FAKE-TOKEN-FOR-LOCAL-CAPTURE-ONLY"
FAKE_CLIENT_ID = "FAKE-CLIENT-ID-FOR-LOCAL-CAPTURE-ONLY"


def _capture_one_request(send_request_fn, *, port: int) -> bytes:
    """Starts a local, loopback-only TCP listener on `port`, calls
    `send_request_fn(port)` to trigger exactly one HTTP request
    against it, captures the raw bytes up to (not including) the
    blank line ending the header block, sends back a minimal 200
    response so the client doesn't hang, and returns the captured
    request-line+headers bytes. Never touches the real network."""
    captured: dict[str, bytes] = {}

    def _listen() -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        srv.settimeout(5.0)
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            captured["bytes"] = b"ACCEPT TIMEOUT -- no connection received"
            srv.close()
            return
        conn.settimeout(2.0)
        data = b""
        try:
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
        captured["bytes"] = data.split(b"\r\n\r\n", 1)[0] if b"\r\n\r\n" in data else data
        try:
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n[]")
        except OSError:
            pass
        conn.close()
        srv.close()

    thread = threading.Thread(target=_listen, daemon=True)
    thread.start()
    time.sleep(0.3)
    try:
        send_request_fn(port)
    except Exception as exc:  # noqa: BLE001 -- diagnostic only, any client-side exception is fine to report
        print(f"  (client-side exception while sending, often expected for this diagnostic: {exc})")
    thread.join(timeout=3)
    return captured.get("bytes", b"NO DATA CAPTURED")


def _capture_requests_default(port: int) -> None:
    import requests

    headers = {"Authorization": f"Bearer {FAKE_TOKEN}", "X-Chaster-Client-Id": FAKE_CLIENT_ID, "Accept": "*/*"}
    requests.get(f"http://127.0.0.1:{port}/locks?status=active", headers=headers, timeout=3)


def _capture_requests_connection_close(port: int) -> None:
    import requests

    headers = {"Authorization": f"Bearer {FAKE_TOKEN}", "X-Chaster-Client-Id": FAKE_CLIENT_ID, "Accept": "*/*", "Connection": "close"}
    requests.get(f"http://127.0.0.1:{port}/locks?status=active", headers=headers, timeout=3)


def _capture_bare_httpclient(port: int) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request("GET", "/locks?status=active", headers={"Authorization": f"Bearer {FAKE_TOKEN}"})
        conn.getresponse()
    finally:
        conn.close()


KNOWN_GOOD_MANUAL_REQUEST = (
    b"GET /locks?status=active HTTP/1.1\r\n"
    b"Host: api.chaster.app\r\n"
    b"Authorization: Bearer <token>\r\n"
    b"Accept: */*\r\n"
    b"Connection: close"
)


def main() -> None:
    print("=" * 78)
    print("DEVELOPMENT-ONLY local HTTP framing capture.")
    print("Makes ZERO real network requests -- only local loopback (127.0.0.1).")
    print("Uses fake placeholder credentials only -- comparison is structural, never value-based.")
    print("=" * 78)

    print("\n--- Known-good manually-constructed request (your own real, successful test) ---")
    print(KNOWN_GOOD_MANUAL_REQUEST.decode("ascii"))

    print("\n--- Captured: requests, default headers ---")
    print(_capture_one_request(_capture_requests_default, port=18443).decode("latin-1", errors="replace"))

    print("\n--- Captured: requests, explicit Connection: close ---")
    print(_capture_one_request(_capture_requests_connection_close, port=18444).decode("latin-1", errors="replace"))

    print("\n--- Captured: bare stdlib http.client ---")
    print(_capture_one_request(_capture_bare_httpclient, port=18445).decode("latin-1", errors="replace"))

    print("\nDone. No real network request was made. No real credentials were used.")


if __name__ == "__main__":
    main()
