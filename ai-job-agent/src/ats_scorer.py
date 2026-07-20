"""Deterministic ATS scoring engine — no AI in score calculation.

Applies a strict recruiter-style rubric: heavy keyword penalties, dynamic
domain-misalignment deductions (no hardcoded industries), and hard-constraint
caps when must-have JD thresholds are unmet.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ai_client import clamp_score
from ats_candidate import AtsCandidateProfile
from job_analyzer import JobProfile
from job_classifier import score_to_action, score_to_decision
from multilingual_normalizer import best_title_similarity, title_similarity
from skill_normalizer import find_matching_skills, normalize_skill, skills_match

# Bump when scoring algorithm changes — used for match invalidation.
ATS_SCORER_VERSION = "v4"

# Section weights (must sum to 1.0).
WEIGHT_MANDATORY = 0.35
WEIGHT_REQUIRED_SKILLS = 0.25
WEIGHT_EXPERIENCE = 0.20
WEIGHT_SENIORITY = 0.10
WEIGHT_PREFERRED = 0.10

# Any failed mandatory requirement caps the total score at this value.
MANDATORY_FAIL_CAP = 49
# Failed critical hard constraint (must-have) — hard ceiling regardless of
# soft-skill / generic keyword overlap.
HARD_CONSTRAINT_FAIL_CAP = 30
# Soft ceiling when a junior candidate is a "potential" match (no hard Weak cap).
POTENTIAL_MATCH_SOFT_CAP = 69
# Early-career (0–1y) vs roles demanding 3+ years — hard ceiling.
JUNIOR_UNDERQUALIFIED_CAP = 70
EARLY_CAREER_MAX_YEARS = 1.0
EXPERIENCED_ROLE_MIN_YEARS = 3.0
# Nonlinear exponent: missing required keywords hurt more than linear ratio.
REQUIRED_SKILLS_PENALTY_EXPONENT = 1.65
# Fundamental professional-domain mismatch (dynamically compared — no taxonomy).
DOMAIN_MISALIGNMENT_PENALTY = 35
DOMAIN_PARTIAL_MISALIGNMENT_PENALTY = 18
# Similarity below this ⇒ fundamental domain mismatch.
DOMAIN_FUNDAMENTAL_MISMATCH_THRESHOLD = 0.22
# Similarity below this ⇒ partial domain misalignment.
DOMAIN_PARTIAL_MISMATCH_THRESHOLD = 0.40
# Extra ceiling when domains are fundamentally misaligned.
DOMAIN_MISMATCH_SCORE_CAP = 40
# Extra per-constraint hit when a hard must-have is unmet (before the hard cap).
HARD_CONSTRAINT_PENALTY = 12

# Junior profiles + entry-level-friendly jobs skip the hard mandatory fail cap
# (but NEVER skip the hard-constraint fail cap).
JUNIOR_SENIORITIES = frozenset({"student", "intern", "junior"})
POTENTIAL_MAX_YEARS = 3.0
FOUNDATIONAL_SKILL_RATIO = 0.25
# Lead/manager roles are never treated as potential junior matches.
EXCLUDED_POTENTIAL_SENIORITIES = frozenset({"lead", "manager"})

# Soft / generic filler terms that must NOT alone prove domain or hard-constraint
# fit. Domain-agnostic — no industry taxonomy. (Basic technical skills are still
# scored normally; domain mismatch + hard-constraint caps block false positives.)
GENERIC_OVERLAP_STOPWORDS = frozenset({
    "communication", "teamwork", "leadership", "english", "hebrew",
    "microsoft office", "excel", "powerpoint", "word", "outlook",
    "problem solving", "organization", "organisational", "collaborative",
    "motivation", "passionate", "detail", "self-starter",
    "תקשורת", "עברית", "אנגלית", "עבודת צוות",
})

SENIORITY_ORDER = {
    "student": 0,
    "intern": 1,
    "junior": 2,
    "mid": 3,
    "senior": 4,
    "lead": 5,
    "manager": 6,
    "unknown": 3,
}

SCORE_LABELS = (
    (85, "Excellent Match"),
    (70, "Good Match"),
    (50, "Partial Match"),
    (0, "Weak Match"),
)
POTENTIAL_MATCH_LABEL = "Potential Match"


@dataclass
class AtsMatchResult:
    ats_score: int
    score_label: str
    matched_required_skills: list[str] = field(default_factory=list)
    missing_required_skills: list[str] = field(default_factory=list)
    missing_mandatory_requirements: list[str] = field(default_factory=list)
    missing_hard_constraints: list[str] = field(default_factory=list)
    relevant_experience: list[str] = field(default_factory=list)
    score_reasons: list[str] = field(default_factory=list)
    cv_improvements: list[str] = field(default_factory=list)
    component_scores: dict[str, float] = field(default_factory=dict)
    mandatory_failed: bool = False
    hard_constraint_failed: bool = False
    domain_mismatch: bool = False
    candidate_domain: str = ""
    target_domain: str = ""
    is_potential_junior_match: bool = False

    def to_db_fields(self, *, strategy_hash: str = "", fallback_score: int | None = None) -> dict[str, Any]:
        decision = score_to_decision(self.ats_score)
        action = score_to_action(self.ats_score)

        reason_parts = [
            f"[ATS] {self.score_label} ({self.ats_score})",
            f"decision: {decision} -> {action}",
        ]
        if self.is_potential_junior_match:
            reason_parts.append("potential junior match")
        if self.domain_mismatch:
            reason_parts.append(
                f"domain mismatch: {self.candidate_domain or '?'} vs "
                f"{self.target_domain or '?'}"
            )
        if self.missing_hard_constraints:
            reason_parts.append(
                f"hard constraints unmet: {', '.join(self.missing_hard_constraints[:4])}"
            )
        if self.missing_mandatory_requirements:
            reason_parts.append(
                f"mandatory gaps: {', '.join(self.missing_mandatory_requirements[:5])}"
            )

        explanation = "; ".join(self.score_reasons[:6]) if self.score_reasons else self.score_label

        rejection = None
        if self.hard_constraint_failed:
            rejection = "; ".join(self.missing_hard_constraints)
        elif self.mandatory_failed and not self.is_potential_junior_match:
            rejection = "; ".join(self.missing_mandatory_requirements)

        return {
            "match_score": self.ats_score,
            "match_reason": "; ".join(reason_parts),
            "match_method": "ats",
            "match_category": "ats",
            "matched_keywords": json.dumps(self.matched_required_skills, ensure_ascii=False),
            "missing_keywords": json.dumps(self.missing_required_skills, ensure_ascii=False),
            "ai_decision": decision,
            "ai_strengths": json.dumps(self.matched_required_skills, ensure_ascii=False),
            "ai_missing_skills": json.dumps(self.missing_required_skills, ensure_ascii=False),
            "ai_recommended_action": action,
            "ai_explanation": explanation,
            "fallback_score": fallback_score,
            "rejection_reason": rejection,
            "candidate_strategy_hash": strategy_hash,
            "ats_score_label": self.score_label,
            "ats_missing_mandatory": json.dumps(
                self.missing_mandatory_requirements, ensure_ascii=False
            ),
            "ats_relevant_experience": json.dumps(
                self.relevant_experience, ensure_ascii=False
            ),
            "ats_reasons": json.dumps(self.score_reasons, ensure_ascii=False),
            "ats_improvements": json.dumps(self.cv_improvements, ensure_ascii=False),
            "ats_component_scores": json.dumps(self.component_scores, ensure_ascii=False),
            "is_potential_junior_match": 1 if self.is_potential_junior_match else 0,
        }


def score_label_for(score: int) -> str:
    for threshold, label in SCORE_LABELS:
        if score >= threshold:
            return label
    return "Weak Match"


def is_junior_profile(candidate: AtsCandidateProfile) -> bool:
    """True for student/intern/junior seniority or very early-career experience."""
    seniority = (candidate.seniority or "unknown").lower()
    if seniority in JUNIOR_SENIORITIES:
        return True
    years = candidate.experience_years
    return years is not None and years <= 2.0


def _job_within_junior_reach(job: JobProfile) -> bool:
    """True when the job's experience bar is entry-level friendly (≤3 years)."""
    seniority = (job.seniority or "unknown").lower()
    if seniority in EXCLUDED_POTENTIAL_SENIORITIES:
        return False
    years = job.years_experience_min
    if years is not None:
        return years <= POTENTIAL_MAX_YEARS
    # No years gate — treat junior/mid/unknown postings as reachable.
    return seniority in {"intern", "student", "junior", "mid", "unknown"}


def _has_foundational_skill_overlap(
    matched: list[str],
    required: list[str],
) -> bool:
    """True when enough required/tech keywords overlap with the candidate."""
    if not matched:
        return False
    if not required:
        return True
    # Ignore ubiquitous soft/basic terms when judging foundational overlap.
    meaningful_matched = [
        m for m in matched if m.strip().lower() not in GENERIC_OVERLAP_STOPWORDS
    ]
    meaningful_required = [
        r for r in required if r.strip().lower() not in GENERIC_OVERLAP_STOPWORDS
    ]
    if not meaningful_required:
        ratio = len(matched) / len(required)
        return ratio >= FOUNDATIONAL_SKILL_RATIO or len(matched) >= 2
    ratio = len(meaningful_matched) / len(meaningful_required)
    return ratio >= FOUNDATIONAL_SKILL_RATIO or len(meaningful_matched) >= 2


def evaluate_potential_junior_match(
    candidate: AtsCandidateProfile,
    job: JobProfile,
    matched_required_skills: list[str],
    *,
    domain_mismatch: bool = False,
    hard_constraint_failed: bool = False,
) -> bool:
    """Whether a junior candidate should skip the hard mandatory fail cap.

    Never relax caps when the professional domain is fundamentally mismatched
    or a critical hard constraint is unmet.
    """
    if domain_mismatch or hard_constraint_failed:
        return False
    if not is_junior_profile(candidate):
        return False
    required = job.required_skills or job.technologies
    years_ok = _job_within_junior_reach(job)
    skills_ok = _has_foundational_skill_overlap(matched_required_skills, required)
    return years_ok or skills_ok


def _blob(*parts: Any) -> str:
    return " ".join(str(p or "") for p in parts).lower()


def _job_text_blob(job_profile: JobProfile, job: dict[str, Any] | None) -> str:
    parts: list[Any] = [
        job_profile.title,
        job_profile.professional_domain,
        " ".join(job_profile.required_skills or []),
        " ".join(job_profile.preferred_skills or []),
        " ".join(job_profile.technologies or []),
        " ".join(job_profile.mandatory_requirements or []),
        " ".join(job_profile.hard_constraints or []),
    ]
    if job:
        parts.extend(
            [
                job.get("title"),
                job.get("description"),
                job.get("full_description"),
                job.get("company"),
            ]
        )
    return _blob(*parts)


def _candidate_text_blob(candidate: AtsCandidateProfile) -> str:
    return _blob(
        " ".join(candidate.skills),
        " ".join(candidate.technologies),
        " ".join(candidate.previous_roles),
        " ".join(candidate.projects),
        " ".join(candidate.domain_keywords),
        candidate.core_professional_domain or candidate.domain,
    )


def _candidate_domain_signals(candidate: AtsCandidateProfile) -> list[str]:
    """Free-form Core Professional Domain signals from the CV (no taxonomy)."""
    signals: list[str] = []
    for value in (
        candidate.core_professional_domain,
        candidate.domain,
        *candidate.domain_keywords,
        *candidate.previous_roles,
    ):
        text = str(value or "").strip()
        if text and text not in signals:
            signals.append(text)
    return signals


def _job_domain_signals(
    job_profile: JobProfile,
    job: dict[str, Any] | None = None,
) -> list[str]:
    """Free-form Target Professional Domain signals from the JD (no taxonomy)."""
    signals: list[str] = []
    for value in (
        job_profile.professional_domain,
        job_profile.title,
        (job or {}).get("title") if job else None,
    ):
        text = str(value or "").strip()
        if text and text not in signals:
            signals.append(text)
    return signals


def _domain_similarity(candidate_signals: list[str], job_signals: list[str]) -> float:
    if not candidate_signals or not job_signals:
        return 0.5  # unknown — do not invent a mismatch
    best = 0.0
    for cs in candidate_signals:
        for js in job_signals:
            best = max(best, title_similarity(cs, js))
        best = max(best, best_title_similarity(cs, job_signals))
    # Also compare the concatenated signal blobs for broader token overlap.
    cand_blob = " ".join(candidate_signals)
    job_blob = " ".join(job_signals)
    best = max(best, title_similarity(cand_blob, job_blob))
    return best


def _skill_bridge_ratio(
    candidate: AtsCandidateProfile,
    job_profile: JobProfile,
) -> float:
    """Fraction of non-generic JD skills evidenced on the CV (career-pivot bridge)."""
    required = list(job_profile.required_skills or []) + list(job_profile.technologies or [])
    meaningful = [
        s for s in required
        if s and s.strip().lower() not in GENERIC_OVERLAP_STOPWORDS
    ]
    if not meaningful:
        return 0.0
    hits = sum(1 for skill in meaningful if _candidate_has_skill(candidate, skill))
    return hits / len(meaningful)


def evaluate_domain_misalignment(
    candidate: AtsCandidateProfile,
    job_profile: JobProfile,
    job: dict[str, Any] | None = None,
) -> tuple[int, bool, str | None, str, str]:
    """Dynamically compare Core vs Target Professional Domain.

    No hardcoded industry lists. Uses free-form domain labels + role titles
    already extracted from the CV/JD. Returns
    (penalty, fundamental_mismatch, reason, candidate_domain, target_domain).

    Adjacent career pivots with strong evidenced skill bridges (e.g. support →
    backend with overlapping stack) are treated as partial misalignment at most —
    not a fundamental domain failure.
    """
    candidate_signals = _candidate_domain_signals(candidate)
    job_signals = _job_domain_signals(job_profile, job)
    candidate_domain = (
        candidate.core_professional_domain
        or (candidate_signals[0] if candidate_signals else "")
    )
    target_domain = (
        job_profile.professional_domain
        or (job_signals[0] if job_signals else "")
    )

    if not candidate_signals or not job_signals:
        return 0, False, None, candidate_domain, target_domain

    similarity = _domain_similarity(candidate_signals, job_signals)
    bridge = _skill_bridge_ratio(candidate, job_profile)

    if similarity < DOMAIN_FUNDAMENTAL_MISMATCH_THRESHOLD and bridge < 0.35:
        return (
            DOMAIN_MISALIGNMENT_PENALTY,
            True,
            (
                f"Fundamental domain mismatch: candidate '{candidate_domain}' "
                f"vs target '{target_domain}' "
                f"(similarity={similarity:.0%}, skill_bridge={bridge:.0%}, "
                f"-{DOMAIN_MISALIGNMENT_PENALTY})"
            ),
            candidate_domain,
            target_domain,
        )
    if similarity < DOMAIN_PARTIAL_MISMATCH_THRESHOLD:
        # Title/domain wording diverges, but a skill bridge may still exist.
        penalty = (
            DOMAIN_PARTIAL_MISALIGNMENT_PENALTY
            if bridge < 0.5
            else max(8, DOMAIN_PARTIAL_MISALIGNMENT_PENALTY // 2)
        )
        return (
            penalty,
            False,
            (
                f"Partial domain misalignment: candidate '{candidate_domain}' "
                f"vs target '{target_domain}' "
                f"(similarity={similarity:.0%}, skill_bridge={bridge:.0%}, -{penalty})"
            ),
            candidate_domain,
            target_domain,
        )
    return 0, False, None, candidate_domain, target_domain


def evaluate_junior_underqualified_cap(
    candidate: AtsCandidateProfile,
    job: JobProfile,
) -> bool:
    """True when 0–1y candidate applies to a role demanding 3+ years."""
    years = candidate.experience_years
    required = job.years_experience_min
    if years is None or required is None:
        return False
    return years <= EARLY_CAREER_MAX_YEARS and required >= EXPERIENCED_ROLE_MIN_YEARS


def _seniority_distance(cv_seniority: str, job_seniority: str | None) -> float:
    cv_rank = SENIORITY_ORDER.get((cv_seniority or "unknown").lower(), 3)
    job_rank = SENIORITY_ORDER.get((job_seniority or "unknown").lower(), 3)
    distance = abs(cv_rank - job_rank)
    if distance == 0:
        return 1.0
    if distance == 1:
        return 0.75
    if distance == 2:
        return 0.5
    return 0.25


def _candidate_has_skill(candidate: AtsCandidateProfile, skill: str) -> bool:
    domain = candidate.domain
    pool = candidate.all_skills_set
    return any(skills_match(s, skill, domain=domain) for s in pool)


def _check_mandatory_requirement(
    req: str,
    candidate: AtsCandidateProfile,
    job: JobProfile,
) -> bool:
    """Return True if the candidate satisfies a mandatory requirement."""
    req_l = req.lower()
    domain = candidate.domain

    # Years of experience check.
    years_match = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?|שנ)", req_l)
    if years_match:
        required_years = float(years_match.group(1))
        cv_years = candidate.experience_years
        if cv_years is None or cv_years < required_years:
            return False
        return True

    # Language requirement.
    for lang in job.languages:
        if lang.lower() in req_l:
            if not _candidate_has_skill(candidate, lang):
                return False
            return True

    # Certification requirement.
    for cert in job.certifications:
        if cert.lower() in req_l:
            if not any(cert.lower() in c.lower() for c in candidate.certifications):
                return False
            return True

    # Technology/skill requirement — check against normalized skills.
    req_canon = normalize_skill(req, domain=domain)
    if req_canon and _candidate_has_skill(candidate, req_canon):
        return True

    # Fallback: check if any word from requirement appears in CV skills/roles.
    tokens = [t for t in re.split(r"[^\w\u0590-\u05FF]+", req_l) if len(t) > 2]
    if tokens:
        cv_text = " ".join(
            candidate.skills
            + candidate.technologies
            + candidate.previous_roles
            + candidate.certifications
            + candidate.projects
            + list(candidate.domain_keywords)
        ).lower()
        if any(token in cv_text for token in tokens):
            return True

    return False


def _check_hard_constraint(
    constraint: str,
    candidate: AtsCandidateProfile,
    job: JobProfile,
) -> bool:
    """Strict evaluation for hard must-haves — no loose soft-skill rescue.

    Unlike general mandatory checks, hard constraints require explicit evidence
    (years threshold, certification, or meaningful skill/role/domain phrase
    overlap). Generic token hits on stopwords alone do not count.
    """
    req = (constraint or "").strip()
    if not req:
        return True
    req_l = req.lower()

    years_match = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?|שנ)", req_l)
    if years_match and ("experience" in req_l or "ניסיון" in req_l or "years" in req_l or "שנ" in req_l):
        required_years = float(years_match.group(1))
        cv_years = candidate.experience_years
        if cv_years is None or cv_years < required_years:
            return False
        # If the constraint names a niche role/domain, also require role evidence.
        niche_tokens = [
            t for t in re.split(r"[^\w\u0590-\u05FF]+", req_l)
            if len(t) > 3
            and t not in GENERIC_OVERLAP_STOPWORDS
            and t not in {"years", "year", "experience", "minimum", "required", "must", "have", "with"}
        ]
        if niche_tokens:
            cv_blob = _candidate_text_blob(candidate)
            if not any(tok in cv_blob for tok in niche_tokens):
                return False
        return True

    for cert in list(job.certifications) + [req]:
        cert_l = cert.lower().strip()
        if len(cert_l) < 3:
            continue
        if "cert" in req_l or cert_l in req_l or req_l in cert_l:
            if any(cert_l in c.lower() or c.lower() in cert_l for c in candidate.certifications):
                return True
            if "cert" in req_l and cert_l == req_l:
                # Explicit cert constraint with no matching candidate cert.
                if not any(req_l in c.lower() for c in candidate.certifications):
                    continue

    if _candidate_has_skill(candidate, req):
        return True

    req_canon = normalize_skill(req, domain=candidate.domain)
    if req_canon and _candidate_has_skill(candidate, req_canon):
        return True

    # Phrase / meaningful-token evidence in CV blob (roles, projects, domain).
    cv_blob = _candidate_text_blob(candidate)
    if req_l in cv_blob:
        return True
    meaningful = [
        t for t in re.split(r"[^\w\u0590-\u05FF]+", req_l)
        if len(t) > 3 and t not in GENERIC_OVERLAP_STOPWORDS
    ]
    if meaningful and all(tok in cv_blob for tok in meaningful[:3]):
        return True
    if meaningful and sum(1 for tok in meaningful if tok in cv_blob) >= max(2, len(meaningful) // 2):
        return True

    return False


def evaluate_hard_constraints(
    candidate: AtsCandidateProfile,
    job: JobProfile,
) -> list[str]:
    """Return unmet hard constraints (critical must-haves) from the JD."""
    constraints = list(job.hard_constraints or [])
    # Deduplicate while preserving order; fall back to certifications as hard gates.
    if not constraints:
        for cert in job.certifications or []:
            constraints.append(f"Certification: {cert}")
    missing: list[str] = []
    seen: set[str] = set()
    for constraint in constraints:
        key = constraint.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        if not _check_hard_constraint(constraint, candidate, job):
            missing.append(constraint)
    return missing


def _score_mandatory(
    candidate: AtsCandidateProfile,
    job: JobProfile,
) -> tuple[float, list[str], list[str]]:
    """Score mandatory requirements section. Returns (score 0-100, missing, reasons)."""
    requirements: list[str] = list(job.mandatory_requirements)

    if job.years_experience_min is not None:
        years_req = f"{job.years_experience_min}+ years experience"
        if years_req not in requirements:
            requirements.append(years_req)

    for lang in job.languages:
        lang_req = f"Language: {lang}"
        if lang_req not in requirements:
            requirements.append(lang_req)

    for cert in job.certifications:
        cert_req = f"Certification: {cert}"
        if cert_req not in requirements:
            requirements.append(cert_req)

    if not requirements:
        return 100.0, [], ["No mandatory requirements specified — full marks"]

    missing: list[str] = []
    for req in requirements:
        if not _check_mandatory_requirement(req, candidate, job):
            missing.append(req)

    if missing:
        ratio = (len(requirements) - len(missing)) / len(requirements)
        section_score = ratio * 30  # heavily penalized but not always zero
        reasons = [f"Missing mandatory: {', '.join(missing[:5])}"]
        return section_score, missing, reasons

    return 100.0, [], [f"All {len(requirements)} mandatory requirements met"]


def _score_required_skills(
    candidate: AtsCandidateProfile,
    job: JobProfile,
) -> tuple[float, list[str], list[str], list[str]]:
    required = job.required_skills or job.technologies
    if not required:
        return 100.0, [], [], ["No required skills specified — full marks"]

    matched, missing = find_matching_skills(
        candidate.all_skills_set, required, domain=candidate.domain
    )
    ratio = len(matched) / len(required) if required else 1.0
    # Nonlinear: missing keywords hurt harder than a linear ratio.
    section_score = (ratio ** REQUIRED_SKILLS_PENALTY_EXPONENT) * 100
    reasons = [f"Required skills: {len(matched)}/{len(required)} matched"]
    if missing:
        reasons.append(
            f"Missing keywords penalized heavily: {', '.join(missing[:5])}"
        )
    return section_score, matched, missing, reasons


def _score_experience(
    candidate: AtsCandidateProfile,
    job: JobProfile,
    job_title: str,
) -> tuple[float, list[str], list[str]]:
    relevant: list[str] = []
    title_l = (job_title or job.title or "").lower()
    title_tokens = [t for t in re.split(r"[^\w\u0590-\u05FF]+", title_l) if len(t) > 2]

    for role in candidate.previous_roles:
        role_l = role.lower()
        if title_tokens and any(tok in role_l for tok in title_tokens):
            relevant.append(role)
        elif job.title and job.title.lower() in role_l:
            relevant.append(role)
        elif job.professional_domain and title_similarity(role, job.professional_domain) >= 0.4:
            relevant.append(role)

    for project in candidate.projects:
        proj_l = project.lower()
        if title_tokens and any(tok in proj_l for tok in title_tokens):
            relevant.append(project)

    # Years component.
    years_score = 50.0
    if job.years_experience_min is not None:
        cv_years = candidate.experience_years
        if cv_years is None:
            years_score = 0.0
        elif cv_years >= job.years_experience_min:
            years_score = 100.0
        else:
            years_score = max(0.0, (cv_years / job.years_experience_min) * 60)
    else:
        years_score = 80.0 if candidate.experience_years else 50.0

    # Role relevance component.
    role_score = 100.0 if relevant else (30.0 if candidate.previous_roles else 0.0)

    section_score = 0.6 * years_score + 0.4 * role_score
    reasons = []
    if relevant:
        reasons.append(f"Relevant experience: {', '.join(relevant[:3])}")
    if job.years_experience_min is not None:
        cv_y = candidate.experience_years
        reasons.append(
            f"Experience years: {cv_y if cv_y is not None else 'unknown'}"
            f" vs required {job.years_experience_min}"
        )

    return section_score, relevant, reasons


def _score_seniority(candidate: AtsCandidateProfile, job: JobProfile) -> tuple[float, list[str]]:
    if not job.seniority or job.seniority == "unknown":
        return 80.0, ["Seniority not specified in job posting"]
    distance = _seniority_distance(candidate.seniority, job.seniority)
    section_score = distance * 100
    reasons = [
        f"Seniority: CV={candidate.seniority}, job={job.seniority}"
        f" (match={int(distance * 100)}%)"
    ]
    return section_score, reasons


def _score_preferred(
    candidate: AtsCandidateProfile,
    job: JobProfile,
) -> tuple[float, list[str]]:
    preferred = job.preferred_skills
    if not preferred:
        return 100.0, ["No preferred skills listed — full marks"]

    matched, _ = find_matching_skills(
        candidate.all_skills_set, preferred, domain=candidate.domain
    )
    ratio = len(matched) / len(preferred)
    section_score = ratio * 100
    reasons = [f"Preferred skills: {len(matched)}/{len(preferred)} matched"]
    return section_score, reasons


def _build_improvements(
    missing_skills: list[str],
    missing_mandatory: list[str],
    *,
    missing_hard: list[str] | None = None,
    domain_mismatch: bool = False,
    candidate_domain: str = "",
    target_domain: str = "",
) -> list[str]:
    improvements: list[str] = []
    if domain_mismatch:
        improvements.append(
            "Professional domain mismatch — emphasize transferable skills and an "
            f"honest career-pivot summary toward '{target_domain or 'the target role'}' "
            f"(current core: '{candidate_domain or 'unspecified'}'). "
            "Do not invent domain experience."
        )
    for constraint in (missing_hard or [])[:3]:
        improvements.append(f"Address critical hard constraint: {constraint}")
    for skill in missing_skills[:5]:
        improvements.append(f"Add or highlight skill: {skill}")
    for req in missing_mandatory[:3]:
        if "years" in req.lower() or "שנ" in req:
            improvements.append(f"Address experience requirement: {req}")
        elif "language" in req.lower() or "שפה" in req:
            improvements.append(f"Demonstrate language proficiency: {req}")
        elif "certification" in req.lower():
            improvements.append(f"Obtain or mention certification: {req}")
        else:
            improvements.append(f"Address mandatory requirement: {req}")
    if not improvements:
        improvements.append("CV aligns well with job requirements")
    return improvements


def score(
    candidate: AtsCandidateProfile,
    job_profile: JobProfile,
    job: dict[str, Any] | None = None,
    *,
    fallback_score: int | None = None,
) -> AtsMatchResult:
    """Calculate deterministic ATS score (0-100) for a candidate against a job."""
    job_title = (job or {}).get("title") or job_profile.title

    mand_score, missing_mandatory, mand_reasons = _score_mandatory(candidate, job_profile)
    req_score, matched, missing, req_reasons = _score_required_skills(candidate, job_profile)
    exp_score, relevant, exp_reasons = _score_experience(candidate, job_profile, job_title)
    sen_score, sen_reasons = _score_seniority(candidate, job_profile)
    pref_score, pref_reasons = _score_preferred(candidate, job_profile)

    component_scores = {
        "mandatory": round(mand_score, 1),
        "required_skills": round(req_score, 1),
        "experience": round(exp_score, 1),
        "seniority": round(sen_score, 1),
        "preferred": round(pref_score, 1),
    }

    weighted = (
        WEIGHT_MANDATORY * mand_score
        + WEIGHT_REQUIRED_SKILLS * req_score
        + WEIGHT_EXPERIENCE * exp_score
        + WEIGHT_SENIORITY * sen_score
        + WEIGHT_PREFERRED * pref_score
    )

    domain_penalty, domain_mismatch, domain_reason, cand_domain, target_domain = (
        evaluate_domain_misalignment(candidate, job_profile, job)
    )
    missing_hard = evaluate_hard_constraints(candidate, job_profile)
    hard_constraint_failed = bool(missing_hard)
    hard_penalty = min(len(missing_hard) * HARD_CONSTRAINT_PENALTY, 36) if missing_hard else 0

    if domain_penalty:
        weighted -= domain_penalty
    if hard_penalty:
        weighted -= hard_penalty

    mandatory_failed = bool(missing_mandatory)
    is_potential = False
    if mandatory_failed:
        is_potential = evaluate_potential_junior_match(
            candidate,
            job_profile,
            matched,
            domain_mismatch=domain_mismatch,
            hard_constraint_failed=hard_constraint_failed,
        )
        if is_potential:
            # Soft ceiling only — do not force the Weak Match hard-cap for juniors.
            weighted = min(weighted, POTENTIAL_MATCH_SOFT_CAP)
        else:
            weighted = min(weighted, MANDATORY_FAIL_CAP)

    # Hard constraints always cap — never overridden by junior "potential".
    if hard_constraint_failed:
        weighted = min(weighted, HARD_CONSTRAINT_FAIL_CAP)
        is_potential = False

    if domain_mismatch:
        weighted = min(weighted, DOMAIN_MISMATCH_SCORE_CAP)

    underqualified = evaluate_junior_underqualified_cap(candidate, job_profile)
    if underqualified:
        # No points for "potential": early-career vs 3y+ roles cannot clear 70.
        weighted = min(weighted, JUNIOR_UNDERQUALIFIED_CAP)

    ats_score = clamp_score(round(weighted))
    label = score_label_for(ats_score)
    if is_potential and ats_score < 50:
        label = POTENTIAL_MATCH_LABEL

    all_reasons = mand_reasons + req_reasons + exp_reasons + sen_reasons + pref_reasons
    if domain_reason:
        all_reasons.insert(0, domain_reason)
    if missing_hard:
        all_reasons.insert(
            0,
            "Hard constraints unmet — score capped at "
            f"{HARD_CONSTRAINT_FAIL_CAP}%: {', '.join(missing_hard[:5])} "
            f"(-{hard_penalty})",
        )
    if underqualified:
        all_reasons.insert(
            0,
            f"Early-career (≤{EARLY_CAREER_MAX_YEARS:g}y) vs "
            f"{job_profile.years_experience_min}+ year role — score capped at "
            f"{JUNIOR_UNDERQUALIFIED_CAP}",
        )
    if is_potential:
        all_reasons.insert(
            0,
            "Potential junior match — mandatory hard-cap relaxed for entry-level reach",
        )
    elif mandatory_failed and not hard_constraint_failed:
        all_reasons.insert(0, "Hard requirements not met — score capped")

    improvements = _build_improvements(
        missing,
        missing_mandatory,
        missing_hard=missing_hard,
        domain_mismatch=domain_mismatch,
        candidate_domain=cand_domain,
        target_domain=target_domain,
    )

    return AtsMatchResult(
        ats_score=ats_score,
        score_label=label,
        matched_required_skills=matched,
        missing_required_skills=missing,
        missing_mandatory_requirements=missing_mandatory,
        missing_hard_constraints=missing_hard,
        relevant_experience=relevant,
        score_reasons=all_reasons,
        cv_improvements=improvements,
        component_scores=component_scores,
        mandatory_failed=mandatory_failed,
        hard_constraint_failed=hard_constraint_failed,
        domain_mismatch=domain_mismatch,
        candidate_domain=cand_domain,
        target_domain=target_domain,
        is_potential_junior_match=is_potential,
    )
