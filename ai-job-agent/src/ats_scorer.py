"""Deterministic ATS scoring engine — no AI in score calculation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ai_client import clamp_score
from ats_candidate import AtsCandidateProfile
from job_analyzer import JobProfile
from job_classifier import score_to_action, score_to_decision
from skill_normalizer import find_matching_skills, normalize_skill, skills_match

# Bump when scoring algorithm changes — used for match invalidation.
ATS_SCORER_VERSION = "v1"

# Section weights (must sum to 1.0).
WEIGHT_MANDATORY = 0.35
WEIGHT_REQUIRED_SKILLS = 0.25
WEIGHT_EXPERIENCE = 0.20
WEIGHT_SENIORITY = 0.10
WEIGHT_PREFERRED = 0.10

# Any failed mandatory requirement caps the total score at this value.
MANDATORY_FAIL_CAP = 49

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


@dataclass
class AtsMatchResult:
    ats_score: int
    score_label: str
    matched_required_skills: list[str] = field(default_factory=list)
    missing_required_skills: list[str] = field(default_factory=list)
    missing_mandatory_requirements: list[str] = field(default_factory=list)
    relevant_experience: list[str] = field(default_factory=list)
    score_reasons: list[str] = field(default_factory=list)
    cv_improvements: list[str] = field(default_factory=list)
    component_scores: dict[str, float] = field(default_factory=dict)
    mandatory_failed: bool = False

    def to_db_fields(self, *, strategy_hash: str = "", fallback_score: int | None = None) -> dict[str, Any]:
        decision = score_to_decision(self.ats_score)
        action = score_to_action(self.ats_score)

        reason_parts = [
            f"[ATS] {self.score_label} ({self.ats_score})",
            f"decision: {decision} -> {action}",
        ]
        if self.missing_mandatory_requirements:
            reason_parts.append(
                f"mandatory gaps: {', '.join(self.missing_mandatory_requirements[:5])}"
            )

        explanation = "; ".join(self.score_reasons[:6]) if self.score_reasons else self.score_label

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
            "rejection_reason": (
                "; ".join(self.missing_mandatory_requirements)
                if self.mandatory_failed
                else None
            ),
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
        }


def score_label_for(score: int) -> str:
    for threshold, label in SCORE_LABELS:
        if score >= threshold:
            return label
    return "Weak Match"


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
        ).lower()
        if any(token in cv_text for token in tokens):
            return True

    return False


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
    section_score = ratio * 100
    reasons = [f"Required skills: {len(matched)}/{len(required)} matched"]
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
) -> list[str]:
    improvements: list[str] = []
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

    mandatory_failed = bool(missing_mandatory)
    if mandatory_failed:
        weighted = min(weighted, MANDATORY_FAIL_CAP)

    ats_score = clamp_score(round(weighted))
    label = score_label_for(ats_score)

    all_reasons = mand_reasons + req_reasons + exp_reasons + sen_reasons + pref_reasons
    if mandatory_failed:
        all_reasons.insert(0, "Hard requirements not met — score capped")

    improvements = _build_improvements(missing, missing_mandatory)

    return AtsMatchResult(
        ats_score=ats_score,
        score_label=label,
        matched_required_skills=matched,
        missing_required_skills=missing,
        missing_mandatory_requirements=missing_mandatory,
        relevant_experience=relevant,
        score_reasons=all_reasons,
        cv_improvements=improvements,
        component_scores=component_scores,
        mandatory_failed=mandatory_failed,
    )
