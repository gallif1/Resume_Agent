"""Tests for query_builder bilingual query assembly."""

from __future__ import annotations

from query_builder import (
    build_collection_queries,
    build_mixed_queries,
    dedupe_queries,
    split_keywords_by_script,
)


def test_dedupe_queries_case_insensitive():
    result = dedupe_queries(["Python Developer", "python developer", "Backend"])
    assert result == ["Python Developer", "Backend"]


def test_split_keywords_by_script():
    en, he = split_keywords_by_script(["Python", "מפתח", "AWS", "אחות"])
    assert "Python" in en
    assert "AWS" in en
    assert "מפתח" in he
    assert "אחות" in he


def test_build_mixed_queries():
    mixed = build_mixed_queries(
        ["Python Developer"],
        ["מפתח"],
        technologies=["Python"],
    )
    assert any("Python" in q for q in mixed)
    assert any("מפתח" in q for q in mixed)


def test_build_collection_queries_from_profile():
    profile = {
        "preferred_role_titles": ["Registered Nurse", "ICU Nurse"],
        "alternative_role_titles": ["Staff Nurse"],
        "search_keywords_en": ["nurse", "RN"],
        "search_keywords_he": ["אחות", "סיעוד"],
        "technologies_tools": [],
        "exclusion_keywords": ["senior"],
        "seniority_level": "junior",
    }
    queries = build_collection_queries(profile)
    assert queries
    entry = queries[0]
    assert entry.get("queries_en")
    assert entry.get("queries_he")
    assert entry.get("queries")
    assert len(entry["queries"]) <= 16
