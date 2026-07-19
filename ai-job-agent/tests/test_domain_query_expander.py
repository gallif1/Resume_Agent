"""Tests for AI-driven domain → board-query expansion."""

from __future__ import annotations

from domain_query_expander import (
    expand_selected_domains_with_ai,
    flatten_expansion_queries,
    _normalize_expansions,
)


def test_fallback_when_ai_disabled():
    result = expand_selected_domains_with_ai(
        ["Technical Support", "שיווק"],
        use_ai=False,
    )
    assert result["Technical Support"]["search_queries_en"] == ["Technical Support"]
    assert result["Technical Support"]["search_queries_he"] == []
    assert result["שיווק"]["search_queries_he"] == ["שיווק"]
    assert result["שיווק"]["search_queries_en"] == []


def test_normalize_expansions_keeps_exact_domain_and_ai_synonyms():
    raw = {
        "expansions": [
            {
                "domain": "Technical Support",
                "search_queries_en": [
                    "Technical Support Engineer",
                    "Help Desk",
                    "IT Support",
                ],
                "search_queries_he": ["תמיכה טכנית"],
            },
            {
                "domain": "Marketing",
                "search_queries_en": ["Marketing Specialist", "Marketing Coordinator"],
                "search_queries_he": [],
            },
        ]
    }
    normalized = _normalize_expansions(
        raw, ["Technical Support", "Marketing", "Logistics"]
    )
    support = flatten_expansion_queries(normalized["Technical Support"])
    assert support[0] == "Technical Support"
    assert "Technical Support Engineer" in support
    assert "Help Desk" in support
    assert "תמיכה טכנית" in support

    marketing = flatten_expansion_queries(normalized["Marketing"])
    assert "Marketing" in marketing
    assert "Marketing Specialist" in marketing

    # Missing AI row → exact-domain fallback
    assert flatten_expansion_queries(normalized["Logistics"]) == ["Logistics"]


def test_expand_selected_domains_with_ai_uses_openai(monkeypatch):
    calls: list[dict] = []

    def fake_call_openai_json(system, user, **kwargs):
        calls.append({"system": system, "user": user})
        return {
            "expansions": [
                {
                    "domain": "Registered Nurse",
                    "search_queries_en": [
                        "Registered Nurse",
                        "Staff Nurse",
                        "Clinical Nurse",
                    ],
                    "search_queries_he": ["אחות מוסמכת"],
                }
            ]
        }

    monkeypatch.setattr(
        "domain_query_expander.is_ai_available", lambda: True
    )
    monkeypatch.setattr(
        "domain_query_expander.call_openai_json", fake_call_openai_json
    )

    result = expand_selected_domains_with_ai(["Registered Nurse"])
    assert calls, "expected OpenAI to be called"
    assert "ANY profession" in calls[0]["system"] or "any profession" in calls[0]["system"].lower()
    queries = flatten_expansion_queries(result["Registered Nurse"])
    assert "Registered Nurse" in queries
    assert "Staff Nurse" in queries
    assert "אחות מוסמכת" in queries


def test_expand_falls_back_on_openai_error(monkeypatch):
    from ai_client import OpenAIAPIError

    monkeypatch.setattr("domain_query_expander.is_ai_available", lambda: True)

    def boom(*_a, **_k):
        raise OpenAIAPIError("down")

    monkeypatch.setattr("domain_query_expander.call_openai_json", boom)
    result = expand_selected_domains_with_ai(["DevOps"])
    assert result["DevOps"]["search_queries_en"] == ["DevOps"]
