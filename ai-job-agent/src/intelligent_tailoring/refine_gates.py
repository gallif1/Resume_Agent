"""Gates that decide whether to spend another refine round.

Happy-path generation uses one primary GPT-5 smart-agent call. Extra refine
rounds (HM / post-polish / narrative) stay deterministic-only in single-agent
mode so typical generations remain one LLM call.
"""

from __future__ import annotations

from typing import Any, Sequence


def should_run_hm_refine(
    *,
    overall_fit: int,
    overall_score: int,
    interview_probability: int,
    twenty_second_screen: int,
    weakest_sections: Sequence[Any] | None,
    quality_weak_sections: Sequence[Any] | None = None,
) -> bool:
    """True only for severe quality gaps with concrete weak sections."""
    severe_quality_gap = (
        int(overall_fit or 0) < 62
        or int(overall_score or 100) < 68
        or int(interview_probability or 100) < 62
        or int(twenty_second_screen or 100) < 62
    )
    has_weak_sections = bool(weakest_sections or quality_weak_sections)
    return severe_quality_gap and has_weak_sections


def should_run_post_polish_refine(
    *,
    interview_probability: int,
    twenty_second_screen: int,
    quality_passed: bool,
    weak_sections: Sequence[Any] | None,
    llm_refine_already_used: bool = False,
) -> bool:
    """Extra writer pass after one-page polish — only when still clearly weak."""
    if llm_refine_already_used:
        return False
    if not weak_sections:
        return False
    return (
        int(interview_probability or 0) < 62
        or int(twenty_second_screen or 0) < 62
        or (not quality_passed and int(interview_probability or 0) < 68)
    )
