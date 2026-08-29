"""Tests for shared match score blending."""

from match_scoring import blend_match_scores, compute_final_match_score, score_label_for


class _FakeAtsResult:
    def __init__(
        self,
        *,
        ats_score: int,
        hard_constraint_failed: bool = False,
        mandatory_failed: bool = False,
        is_potential_junior_match: bool = False,
        domain_mismatch: bool = False,
        experience_gap_failed: bool = False,
        experience_gap_years: float = 0.0,
    ):
        self.ats_score = ats_score
        self.hard_constraint_failed = hard_constraint_failed
        self.mandatory_failed = mandatory_failed
        self.is_potential_junior_match = is_potential_junior_match
        self.domain_mismatch = domain_mismatch
        self.experience_gap_failed = experience_gap_failed
        self.experience_gap_years = experience_gap_years


def test_blend_match_scores_matches_pipeline_weights():
    assert blend_match_scores(80, 60) == 72


def test_compute_final_match_score_applies_hard_constraint_cap():
    result = _FakeAtsResult(ats_score=55, hard_constraint_failed=True)
    assert compute_final_match_score(90, result) <= 30


def test_compute_final_match_score_caps_experience_gap_despite_high_profile():
    """High profile overlap must not inflate the blend when years are far below the JD."""
    result = _FakeAtsResult(
        ats_score=42,
        mandatory_failed=True,
        experience_gap_failed=True,
        experience_gap_years=5.0,
    )
    assert compute_final_match_score(75, result) <= 45


def test_compute_final_match_score_allows_small_experience_gap_blend():
    """Small year gaps keep the profile/ATS blend for reachable junior roles."""
    result = _FakeAtsResult(
        ats_score=58,
        experience_gap_failed=True,
        experience_gap_years=1.5,
    )
    assert compute_final_match_score(72, result) == blend_match_scores(72, 58)


def test_score_label_for_buckets():
    assert score_label_for(88) == "Excellent Match"
    assert score_label_for(75) == "Good Match"
    assert score_label_for(55) == "Partial Match"
    assert score_label_for(40, is_potential_junior=True) == "Potential Match"
