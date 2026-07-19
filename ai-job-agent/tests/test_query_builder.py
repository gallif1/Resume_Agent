"""Tests for query_builder bilingual query assembly."""

from __future__ import annotations

from query_builder import (
    build_collection_queries,
    build_mixed_queries,
    dedupe_queries,
    expand_domain_search_queries,
    inject_domain_query_expansions,
    queries_for_board,
    select_diverse_queries,
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


def test_select_diverse_queries_prefers_specific_over_generic():
    queries = [
        "Software Engineer",
        "Developer",
        "Python Backend Developer",
        "מפתח Python",
        "FastAPI Developer",
        "מפתח",
    ]
    selected = select_diverse_queries(queries, max_items=3)
    assert len(selected) == 3
    assert "Software Engineer" not in selected
    assert "Developer" not in selected
    assert "מפתח" not in selected
    assert "Python Backend Developer" in selected
    assert "מפתח Python" in selected


def test_build_collection_queries_puts_tech_specific_terms_early():
    profile = {
        "preferred_role_titles": ["Software Engineer"],
        "alternative_role_titles": [],
        "search_keywords_en": ["software"],
        "search_keywords_he": ["מפתח"],
        "technologies_tools": ["Python", "FastAPI"],
        "exclusion_keywords": [],
        "seniority_level": "mid",
    }
    entry = build_collection_queries(profile)[0]
    joined = " | ".join(entry["queries"][:4]).lower()
    assert "python" in joined


def test_queries_for_board_linkedin_uses_english_only():
    entry = {
        "primary_role": "Software Developer",
        "queries_en": [
            "Software Developer",
            "Backend Developer",
            "Data Scientist",
            "Data Analyst",
        ],
        "queries_he": ["מפתח פייתון", "מפתח תוכנה"],
        "queries_mixed": ["מפתח Python"],
        "queries": ["מפתח פייתון", "Software Developer", "Backend Developer"],
    }
    linkedin = queries_for_board(entry, "linkedin", max_items=5)
    gotfriends = queries_for_board(entry, "gotfriends", max_items=5)
    drushim = queries_for_board(entry, "drushim", max_items=5)

    assert linkedin
    assert all(not any("\u0590" <= ch <= "\u05FF" for ch in q) for q in linkedin)
    assert "Software Developer" in linkedin or "Backend Developer" in linkedin
    assert "מפתח פייתון" not in linkedin
    assert gotfriends
    assert all(not any("\u0590" <= ch <= "\u05FF" for ch in q) for q in gotfriends)
    # Drushim may include Hebrew / mixed terms.
    assert drushim
    assert any(any("\u0590" <= ch <= "\u05FF" for ch in q) for q in drushim) or any(
        q in drushim for q in entry["queries_en"]
    )


def test_queries_for_board_recovers_english_from_flat_legacy_list():
    entry = {
        "primary_role": "מפתח פייתון",
        "queries": [
            "מפתח פייתון",
            "Python Developer",
            "Backend Developer",
            "מפתח Python",
        ],
    }
    linkedin = queries_for_board(entry, "linkedin", max_items=3)
    assert "Python Developer" in linkedin
    assert "Backend Developer" in linkedin
    assert "מפתח פייתון" not in linkedin


def test_expand_domain_search_queries_includes_support_engineer_variants():
    """Selecting Technical Support must expand to titles boards actually use."""
    queries = expand_domain_search_queries("Technical Support")
    joined = " | ".join(q.casefold() for q in queries)
    assert "technical support" in joined
    assert "technical support engineer" in joined
    assert "help desk" in joined or "helpdesk" in joined
    assert "it support" in joined


def test_pinned_domain_expansions_survive_small_query_budget():
    """Even with max_queries=2, pinned expansions must not be dropped."""
    entry = inject_domain_query_expansions(
        {
            "primary_role": "IT Support Specialist",
            "queries_en": [
                "IT Support Specialist",
                "Help Desk Technician",
                "Support Technician",
            ],
            "search_queries": [
                "IT Support Specialist",
                "Help Desk Technician",
                "Support Technician",
            ],
            "queries": [],
        },
        "Technical Support",
    )
    linkedin = queries_for_board(entry, "linkedin", max_items=2)
    assert len(linkedin) == 2
    # Exact selected domain and/or engineer variant must lead the LinkedIn search.
    assert any(
        "technical support" in q.casefold() for q in linkedin
    ), linkedin


def test_select_diverse_queries_keeps_pinned_first():
    selected = select_diverse_queries(
        ["Generic Developer", "Software Engineer", "Python Backend"],
        max_items=2,
        pinned=["Technical Support Engineer", "Help Desk"],
    )
    assert selected[0] == "Technical Support Engineer"
    assert "Help Desk" in selected
