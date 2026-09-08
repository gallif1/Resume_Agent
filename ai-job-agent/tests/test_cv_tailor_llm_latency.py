"""CV Tailor LLM latency knobs must not alter prompt builders."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import ai_client
from cv_tailor.service import _cv_tailor_llm_kwargs


def test_cv_tailor_llm_kwargs_defaults_to_low_effort(monkeypatch):
    monkeypatch.setattr("cv_tailor.service.OPENAI_CV_TAILOR_REASONING_EFFORT", "low")
    monkeypatch.setattr("cv_tailor.service.OPENAI_CV_TAILOR_VERBOSITY", "low")
    assert _cv_tailor_llm_kwargs() == {
        "reasoning_effort": "low",
        "verbosity": "low",
    }


def test_cv_tailor_llm_kwargs_can_disable(monkeypatch):
    monkeypatch.setattr("cv_tailor.service.OPENAI_CV_TAILOR_REASONING_EFFORT", "")
    monkeypatch.setattr("cv_tailor.service.OPENAI_CV_TAILOR_VERBOSITY", "")
    assert _cv_tailor_llm_kwargs() == {}


def test_call_openai_json_passes_reasoning_effort(monkeypatch):
    monkeypatch.setattr(ai_client, "OPENAI_API_KEY", "test-key")

    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content='{"ok": true}'))]
    fake_response.usage = MagicMock(
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        completion_tokens_details=MagicMock(reasoning_tokens=5),
    )

    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            pass

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            captured.update(kwargs)
            return fake_response

    with patch("openai.OpenAI", FakeOpenAI):
        result = ai_client.call_openai_json(
            "system",
            "user",
            use_cache=False,
            model="gpt-5",
            reasoning_effort="low",
            verbosity="low",
        )

    assert captured["model"] == "gpt-5"
    assert captured["reasoning_effort"] == "low"
    assert captured["verbosity"] == "low"
    assert "temperature" not in captured
    assert result["ok"] is True
    assert result["_usage"]["reasoning_tokens"] == 5
