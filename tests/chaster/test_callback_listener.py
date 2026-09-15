"""
tests/chaster/test_callback_listener.py

Uses asyncio.run() directly around the listener's own async
start()/stop() (pytest-asyncio is not a dependency of this project,
matching tests/bot/test_discord_bot.py's own established pattern),
and plain synchronous `requests` calls against the real bound
localhost port to exercise it -- a real server, a real HTTP request,
no live Chaster/Discord/Cloudflare involved.
"""

from __future__ import annotations

import asyncio
import socket

import pytest
import requests

from chaster.callback_listener import CALLBACK_PATH, ChasterCallbackListener


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestStartupAndShutdown:
    def test_listener_starts_and_stops_cleanly(self) -> None:
        port = _free_port()

        async def _run() -> None:
            calls = []

            async def on_callback(state, code, error):
                calls.append((state, code, error))
                return "ok"

            listener = ChasterCallbackListener(bind_host="127.0.0.1", bind_port=port, on_callback=on_callback)
            await listener.start()
            await listener.stop()

        asyncio.run(_run())  # must not raise

    def test_stopping_a_never_started_listener_is_safe(self) -> None:
        async def _run() -> None:
            listener = ChasterCallbackListener(bind_host="127.0.0.1", bind_port=_free_port(), on_callback=lambda s, c, e: "ok")
            await listener.stop()  # must not raise

        asyncio.run(_run())


class TestRequestHandling:
    def test_a_real_request_reaches_the_callback_handler(self) -> None:
        port = _free_port()
        received: list = []

        async def _run() -> None:
            async def on_callback(state, code, error):
                received.append((state, code, error))
                return "connected"

            listener = ChasterCallbackListener(bind_host="127.0.0.1", bind_port=port, on_callback=on_callback)
            await listener.start()
            try:
                response = await asyncio.to_thread(
                    requests.get, f"http://127.0.0.1:{port}{CALLBACK_PATH}",
                    params={"state": "abc123", "code": "authcode456"}, timeout=5,
                )
                assert response.status_code == 200
                assert response.text == "connected"
            finally:
                await listener.stop()

        asyncio.run(_run())
        assert received == [("abc123", "authcode456", None)]

    def test_a_malformed_request_missing_all_query_params_still_reaches_the_handler_with_nones(self) -> None:
        port = _free_port()
        received: list = []

        async def _run() -> None:
            async def on_callback(state, code, error):
                received.append((state, code, error))
                return "handled"

            listener = ChasterCallbackListener(bind_host="127.0.0.1", bind_port=port, on_callback=on_callback)
            await listener.start()
            try:
                response = await asyncio.to_thread(
                    requests.get, f"http://127.0.0.1:{port}{CALLBACK_PATH}", timeout=5,
                )
                assert response.status_code == 200
            finally:
                await listener.stop()

        asyncio.run(_run())
        assert received == [(None, None, None)]

    def test_an_unhandled_exception_in_the_callback_produces_a_safe_response_not_a_crash(self) -> None:
        port = _free_port()

        async def _run() -> None:
            async def on_callback(state, code, error):
                raise RuntimeError("boom -- simulated bug")

            listener = ChasterCallbackListener(bind_host="127.0.0.1", bind_port=port, on_callback=on_callback)
            await listener.start()
            try:
                response = await asyncio.to_thread(
                    requests.get, f"http://127.0.0.1:{port}{CALLBACK_PATH}",
                    params={"state": "s"}, timeout=5,
                )
                assert response.status_code == 200  # a safe response, not a 500 crash trace
                assert "boom" not in response.text
                assert "Traceback" not in response.text
            finally:
                await listener.stop()

        asyncio.run(_run())

    def test_a_request_to_an_unregistered_path_returns_404_not_a_generic_route(self) -> None:
        """No unnecessary endpoints -- confirms only CALLBACK_PATH exists."""
        port = _free_port()

        async def _run() -> None:
            listener = ChasterCallbackListener(bind_host="127.0.0.1", bind_port=port, on_callback=lambda s, c, e: "ok")
            await listener.start()
            try:
                response = await asyncio.to_thread(
                    requests.get, f"http://127.0.0.1:{port}/some/other/path", timeout=5,
                )
                assert response.status_code == 404
            finally:
                await listener.stop()

        asyncio.run(_run())
