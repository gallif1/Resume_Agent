"""Shared match-score blending used by scan/match and CV tailoring."""

from __future__ import annotations

from typing import Any

from ai_client import clamp_score
from ats_scorer import (
    DOMAIN_MISMATCH_SCORE_CAP,
    HARD_CONSTRAINT_FAIL_CAP,
)

PROFILE_MATCH_WEIGHT = 0.60
ATS_MATCH_WEIGHT = 0.40


def blend_match_scores(profile_match_score: int, ats_match_score: int) -> int:
    """Blend profile and ATS scores the same way as the scan/match pipeline."""
    blended = (
        profile_match_score * PROFILE_MATCH_WEIGHT
        + ats_match_score * ATS_MATCH_WEIGHT
    )
    return clamp_score(int(round(blended)))


def compute_final_match_score(
    profile_score: int,
    ats_result: Any,
    *,
    profile_exclusion_hit: bool = False,
) -> int:
    """Apply the same caps as match_jobs when combining profile + ATS results."""
    final_score = blend_match_scores(profile_score, int(ats_result.ats_score))
    if profile_exclusion_hit:
        final_score = min(final_score, profile_score)
    if ats_result.hard_constraint_failed:
        final_score = min(
            final_score, HARD_CONSTRAINT_FAIL_CAP, int(ats_result.ats_score)
        )
    elif ats_result.mandatory_failed and not ats_result.is_potential_junior_match:
        final_score = min(final_score, int(ats_result.ats_score))
    if ats_result.domain_mismatch:
        final_score = min(
            final_score, DOMAIN_MISMATCH_SCORE_CAP, int(ats_result.ats_score)
        )
    return clamp_score(final_score)


def score_label_for(final_score: int, *, is_potential_junior: bool = False) -> str:
    if final_score >= 85:
        return "Excellent Match"
    if final_score >= 70:
        return "Good Match"
    if final_score >= 50:
        return "Partial Match"
    if is_potential_junior:
        return "Potential Match"
    return "Weak Match"
