"""tests/conversation_engine/test_ollama_bounded_reader.py"""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest
import requests

from conversation_engine.model_types import LLMGenerationError, ModelGenerationRequest, ModelMessage, ModelMessageRole
from conversation_engine.ollama_adapter import (
    OllamaConversationModel,
    _is_accepted_content_type,
    _read_bounded_response_body,
)


def _fake_response(
    *, status_code: int = 200, headers: dict | None = None, chunks: list[bytes] | None = None,
    iter_content_raises: Exception | None = None,
) -> Mock:
    response = Mock(spec=requests.Response)
    response.status_code = status_code
    response.headers = headers or {"Content-Type": "application/json"}

    def _iter_content(chunk_size):
        if iter_content_raises is not None:
            raise iter_content_raises
        for c in (chunks or []):
            yield c

    response.iter_content = _iter_content
    response.close = Mock()
    return response


def _request(max_output_characters: int = 1800) -> ModelGenerationRequest:
    return ModelGenerationRequest(
        messages=(ModelMessage(role=ModelMessageRole.USER, content="hi"),),
        max_output_characters=max_output_characters,
    )


class TestContentType:
    def test_plain_application_json_accepted(self) -> None:
        assert _is_accepted_content_type("application/json") is True

    def test_application_json_utf8_charset_accepted(self) -> None:
        assert _is_accepted_content_type("application/json; charset=utf-8") is True

    def test_charset_case_insensitive(self) -> None:
        assert _is_accepted_content_type("APPLICATION/JSON; CHARSET=UTF-8") is True

    def test_other_media_type_rejected(self) -> None:
        assert _is_accepted_content_type("text/plain") is False

    def test_explicit_non_utf8_charset_rejected(self) -> None:
        assert _is_accepted_content_type("application/json; charset=latin-1") is False


class TestBoundedReaderContentLength:
    def test_content_length_over_limit_rejected(self) -> None:
        response = _fake_response(headers={"Content-Length": "1000"})
        with pytest.raises(LLMGenerationError, match="ollama_response_too_large"):
            _read_bounded_response_body(response, max_bytes=100)

    def test_non_numeric_content_length_rejected(self) -> None:
        response = _fake_response(headers={"Content-Length": "abc"})
        with pytest.raises(LLMGenerationError, match="ollama_invalid_content_length"):
            _read_bounded_response_body(response, max_bytes=100)

    def test_negative_content_length_rejected(self) -> None:
        response = _fake_response(headers={"Content-Length": "-5"})
        with pytest.raises(LLMGenerationError, match="ollama_invalid_content_length"):
            _read_bounded_response_body(response, max_bytes=100)

    def test_missing_content_length_body_under_limit_succeeds(self) -> None:
        response = _fake_response(headers={}, chunks=[b"hello"])
        body = _read_bounded_response_body(response, max_bytes=100)
        assert body == b"hello"

    def test_missing_content_length_body_over_limit_rejected(self) -> None:
        response = _fake_response(headers={}, chunks=[b"x" * 200])
        with pytest.raises(LLMGenerationError, match="ollama_response_too_large"):
            _read_bounded_response_body(response, max_bytes=100)

    def test_falsely_small_content_length_but_actual_body_over_limit_rejected(self) -> None:
        """The header is never trusted -- actual bytes read are checked regardless."""
        response = _fake_response(headers={"Content-Length": "5"}, chunks=[b"x" * 200])
        with pytest.raises(LLMGenerationError, match="ollama_response_too_large"):
            _read_bounded_response_body(response, max_bytes=100)


class TestBoundedReaderByteLimit:
    def test_exactly_max_bytes_accepted(self) -> None:
        response = _fake_response(chunks=[b"x" * 100])
        body = _read_bounded_response_body(response, max_bytes=100)
        assert len(body) == 100

    def test_max_bytes_plus_one_rejected(self) -> None:
        response = _fake_response(chunks=[b"x" * 101])
        with pytest.raises(LLMGenerationError, match="ollama_response_too_large"):
            _read_bounded_response_body(response, max_bytes=100)

    def test_multiple_chunks_summing_over_limit_rejected(self) -> None:
        response = _fake_response(chunks=[b"x" * 60, b"y" * 60])
        with pytest.raises(LLMGenerationError, match="ollama_response_too_large"):
            _read_bounded_response_body(response, max_bytes=100)

    def test_empty_chunks_are_ignored(self) -> None:
        response = _fake_response(chunks=[b"", b"hello", b"", b"world"])
        body = _read_bounded_response_body(response, max_bytes=100)
        assert body == b"helloworld"

    def test_never_uses_response_content_text_or_json(self) -> None:
        response = _fake_response(chunks=[b"hello"])
        response.content = Mock(side_effect=AssertionError("must not access .content"))
        response.text = Mock(side_effect=AssertionError("must not access .text"))
        response.json = Mock(side_effect=AssertionError("must not access .json()"))
        _read_bounded_response_body(response, max_bytes=100)
        response.json.assert_not_called()


class TestBoundedReaderTransportErrors:
    def test_read_error_during_iteration_becomes_llm_generation_error(self) -> None:
        response = _fake_response(iter_content_raises=requests.exceptions.ChunkedEncodingError("boom"))
        with pytest.raises(LLMGenerationError, match="ollama_read_error"):
            _read_bounded_response_body(response, max_bytes=100)


class TestOllamaConversationModelDecodingAndValidation:
    def _model(self) -> OllamaConversationModel:
        return OllamaConversationModel(host="http://localhost:11434", model_name="test-model")

    def _patch_post(self, response: Mock):
        return patch("conversation_engine.ollama_adapter.requests.post", return_value=response)

    def test_successful_generation(self) -> None:
        body = json.dumps({"message": {"content": "hello there"}}).encode("utf-8")
        response = _fake_response(chunks=[body])
        with self._patch_post(response):
            result = self._model().generate(request=_request())
        assert result == "hello there"

    def test_invalid_utf8_rejected(self) -> None:
        response = _fake_response(chunks=[b"\xff\xfe\x00invalid"])
        with self._patch_post(response):
            with pytest.raises(LLMGenerationError, match="ollama_invalid_utf8"):
                self._model().generate(request=_request())

    def test_invalid_json_rejected(self) -> None:
        response = _fake_response(chunks=[b"not json{{{"])
        with self._patch_post(response):
            with pytest.raises(LLMGenerationError, match="ollama_invalid_json"):
                self._model().generate(request=_request())

    def test_invalid_content_type_rejected(self) -> None:
        response = _fake_response(headers={"Content-Type": "text/plain"}, chunks=[b"hi"])
        with self._patch_post(response):
            with pytest.raises(LLMGenerationError, match="ollama_invalid_content_type"):
                self._model().generate(request=_request())

    def test_explicit_non_utf8_charset_rejected(self) -> None:
        response = _fake_response(headers={"Content-Type": "application/json; charset=latin-1"}, chunks=[b"{}"])
        with self._patch_post(response):
            with pytest.raises(LLMGenerationError, match="ollama_invalid_content_type"):
                self._model().generate(request=_request())

    def test_missing_message_content_field_rejected(self) -> None:
        body = json.dumps({"unexpected": "shape"}).encode("utf-8")
        response = _fake_response(chunks=[body])
        with self._patch_post(response):
            with pytest.raises(LLMGenerationError, match="ollama_invalid_response_shape"):
                self._model().generate(request=_request())

    def test_non_string_content_rejected(self) -> None:
        body = json.dumps({"message": {"content": 12345}}).encode("utf-8")
        response = _fake_response(chunks=[body])
        with self._patch_post(response):
            with pytest.raises(LLMGenerationError, match="ollama_invalid_response_shape"):
                self._model().generate(request=_request())

    def test_empty_text_after_strip_rejected(self) -> None:
        body = json.dumps({"message": {"content": "   "}}).encode("utf-8")
        response = _fake_response(chunks=[body])
        with self._patch_post(response):
            with pytest.raises(LLMGenerationError, match="ollama_empty_response"):
                self._model().generate(request=_request())

    def test_text_longer_than_max_output_characters_rejected(self) -> None:
        body = json.dumps({"message": {"content": "x" * 50}}).encode("utf-8")
        response = _fake_response(chunks=[body])
        with self._patch_post(response):
            with pytest.raises(LLMGenerationError, match="ollama_response_too_long"):
                self._model().generate(request=_request(max_output_characters=10))

    def test_non_200_status_rejected(self) -> None:
        response = _fake_response(status_code=500, chunks=[b"{}"])
        with self._patch_post(response):
            with pytest.raises(LLMGenerationError, match="ollama_http_error"):
                self._model().generate(request=_request())


class TestResponseLifecycle:
    def _model(self) -> OllamaConversationModel:
        return OllamaConversationModel(host="http://localhost:11434", model_name="test-model")

    def test_response_closed_on_success(self) -> None:
        body = json.dumps({"message": {"content": "ok"}}).encode("utf-8")
        response = _fake_response(chunks=[body])
        with patch("conversation_engine.ollama_adapter.requests.post", return_value=response):
            self._model().generate(request=_request())
        response.close.assert_called_once()

    @pytest.mark.parametrize("status_code", [500, 404])
    def test_response_closed_on_http_error(self, status_code: int) -> None:
        response = _fake_response(status_code=status_code, chunks=[b"{}"])
        with patch("conversation_engine.ollama_adapter.requests.post", return_value=response):
            with pytest.raises(LLMGenerationError):
                self._model().generate(request=_request())
        response.close.assert_called_once()

    def test_response_closed_on_invalid_json(self) -> None:
        response = _fake_response(chunks=[b"not json"])
        with patch("conversation_engine.ollama_adapter.requests.post", return_value=response):
            with pytest.raises(LLMGenerationError):
                self._model().generate(request=_request())
        response.close.assert_called_once()

    def test_response_closed_on_body_too_large(self) -> None:
        response = _fake_response(chunks=[b"x" * 2_000_000])
        with patch("conversation_engine.ollama_adapter.requests.post", return_value=response):
            with pytest.raises(LLMGenerationError):
                self._model().generate(request=_request())
        response.close.assert_called_once()

    def test_no_response_object_to_close_when_post_itself_raises(self) -> None:
        with patch("conversation_engine.ollama_adapter.requests.post", side_effect=requests.Timeout()):
            with pytest.raises(LLMGenerationError, match="ollama_timeout"):
                self._model().generate(request=_request())
        # No assertion on .close() needed here -- proving this doesn't
        # crash (no response object ever existed) is the test itself.

    def test_connection_error_mapped(self) -> None:
        with patch("conversation_engine.ollama_adapter.requests.post", side_effect=requests.ConnectionError()):
            with pytest.raises(LLMGenerationError, match="ollama_connection_error"):
                self._model().generate(request=_request())


class TestRequestShape:
    def test_stream_true_used_for_the_http_request(self) -> None:
        body = json.dumps({"message": {"content": "ok"}}).encode("utf-8")
        response = _fake_response(chunks=[body])
        with patch("conversation_engine.ollama_adapter.requests.post", return_value=response) as mock_post:
            OllamaConversationModel(host="http://localhost:11434", model_name="m").generate(request=_request())
        _, kwargs = mock_post.call_args
        assert kwargs["stream"] is True
        assert kwargs["allow_redirects"] is False

    def test_ollama_payload_uses_stream_false(self) -> None:
        body = json.dumps({"message": {"content": "ok"}}).encode("utf-8")
        response = _fake_response(chunks=[body])
        with patch("conversation_engine.ollama_adapter.requests.post", return_value=response) as mock_post:
            OllamaConversationModel(host="http://localhost:11434", model_name="m").generate(request=_request())
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["stream"] is False

    def test_uses_api_chat_endpoint(self) -> None:
        body = json.dumps({"message": {"content": "ok"}}).encode("utf-8")
        response = _fake_response(chunks=[body])
        with patch("conversation_engine.ollama_adapter.requests.post", return_value=response) as mock_post:
            OllamaConversationModel(host="http://localhost:11434", model_name="m").generate(request=_request())
        url = mock_post.call_args[0][0]
        assert url == "http://localhost:11434/api/chat"

    def test_host_double_slash_normalized(self) -> None:
        body = json.dumps({"message": {"content": "ok"}}).encode("utf-8")
        response = _fake_response(chunks=[body])
        with patch("conversation_engine.ollama_adapter.requests.post", return_value=response) as mock_post:
            OllamaConversationModel(host="http://localhost:11434/", model_name="m").generate(request=_request())
        url = mock_post.call_args[0][0]
        assert url == "http://localhost:11434/api/chat"
