"""tests/conversation_engine/test_fallback.py"""

from __future__ import annotations

from conversation_engine.fallback import FallbackReason, render_fallback
from conversation_engine.models import ResponseCategory


class TestRenderFallback:
    def test_default_reason_produces_generic_text(self) -> None:
        response = render_fallback()
        assert response.text
        assert response.response_category == ResponseCategory.ERROR_FALLBACK

    def test_each_reason_produces_a_non_empty_distinct_message(self) -> None:
        texts = {reason: render_fallback(reason).text for reason in FallbackReason}
        assert all(texts.values())
        assert len(set(texts.values())) == len(texts)  # all distinct

    def test_never_calls_any_provider_or_llm_and_always_returns(self) -> None:
        """No provider list, no context, no LLM client -- this
        function's own signature proves it structurally, this test
        proves it behaviorally by simply calling it with nothing else
        set up at all."""
        for reason in FallbackReason:
            response = render_fallback(reason, language="cs")  # language accepted, currently ignored
            assert response.response_category == ResponseCategory.ERROR_FALLBACK
