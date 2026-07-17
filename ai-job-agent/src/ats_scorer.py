"""Deterministic ATS scoring engine — no AI in score calculation.

Applies a strict recruiter-style rubric: heavy keyword penalties, domain
misalignment deductions, and early-career caps against experienced roles.
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
from skill_normalizer import find_matching_skills, normalize_skill, skills_match

# Bump when scoring algorithm changes — used for match invalidation.
ATS_SCORER_VERSION = "v3"

# Section weights (must sum to 1.0).
WEIGHT_MANDATORY = 0.35
WEIGHT_REQUIRED_SKILLS = 0.25
WEIGHT_EXPERIENCE = 0.20
WEIGHT_SENIORITY = 0.10
WEIGHT_PREFERRED = 0.10

# Any failed mandatory requirement caps the total score at this value.
MANDATORY_FAIL_CAP = 49
# Soft ceiling when a junior candidate is a "potential" match (no hard Weak cap).
POTENTIAL_MATCH_SOFT_CAP = 69
# Early-career (0–1y) vs roles demanding 3+ years — hard ceiling.
JUNIOR_UNDERQUALIFIED_CAP = 70
EARLY_CAREER_MAX_YEARS = 1.0
EXPERIENCED_ROLE_MIN_YEARS = 3.0
# Nonlinear exponent: missing required keywords hurt more than linear ratio.
REQUIRED_SKILLS_PENALTY_EXPONENT = 1.65
# Domain mismatch (e.g. Web/eCommerce job vs Mobile-heavy CV).
DOMAIN_MISALIGNMENT_PENALTY = 20
# Extra per-keyword hit when critical JD terms are absent from the CV.
CRITICAL_KEYWORD_PENALTY = 8

# Junior profiles + entry-level-friendly jobs skip the hard mandatory fail cap.
JUNIOR_SENIORITIES = frozenset({"student", "intern", "junior"})
POTENTIAL_MAX_YEARS = 3.0
FOUNDATIONAL_SKILL_RATIO = 0.25
# Lead/manager roles are never treated as potential junior matches.
EXCLUDED_POTENTIAL_SENIORITIES = frozenset({"lead", "manager"})

# Business-domain signals for Web/eCommerce vs Mobile misalignment checks.
WEB_ECOMMERCE_MARKERS = (
    "ecommerce",
    "e-commerce",
    "e commerce",
    "web ",
    " website",
    "frontend",
    "front-end",
    "front end",
    "fullstack",
    "full-stack",
    "full stack",
    "responsive design",
    "responsive",
    "shopify",
    "woocommerce",
    "magento",
    "next.js",
    "nextjs",
)
MOBILE_MARKERS = (
    "mobile",
    "ios",
    "android",
    "react native",
    "react-native",
    "flutter",
    "swift",
    "kotlin",
    "xamarin",
    "expo",
    "iphone",
    "ipad",
)
CRITICAL_JD_KEYWORDS = (
    "responsive design",
    "ecommerce",
    "e-commerce",
    "e commerce",
    "accessibility",
    "seo",
    "graphql",
    "typescript",
    "ci/cd",
    "microservices",
)

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
    relevant_experience: list[str] = field(default_factory=list)
    score_reasons: list[str] = field(default_factory=list)
    cv_improvements: list[str] = field(default_factory=list)
    component_scores: dict[str, float] = field(default_factory=dict)
    mandatory_failed: bool = False
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
                if self.mandatory_failed and not self.is_potential_junior_match
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
    ratio = len(matched) / len(required)
    return ratio >= FOUNDATIONAL_SKILL_RATIO or len(matched) >= 2


def evaluate_potential_junior_match(
    candidate: AtsCandidateProfile,
    job: JobProfile,
    matched_required_skills: list[str],
) -> bool:
    """Whether a junior candidate should skip the hard mandatory fail cap."""
    if not is_junior_profile(candidate):
        return False
    required = job.required_skills or job.technologies
    years_ok = _job_within_junior_reach(job)
    skills_ok = _has_foundational_skill_overlap(matched_required_skills, required)
    return years_ok or skills_ok


def _blob(*parts: Any) -> str:
    return " ".join(str(p or "") for p in parts).lower()


def _count_markers(text: str, markers: tuple[str, ...]) -> int:
    return sum(1 for marker in markers if marker in text)


def _job_text_blob(job_profile: JobProfile, job: dict[str, Any] | None) -> str:
    parts: list[Any] = [
        job_profile.title,
        " ".join(job_profile.required_skills or []),
        " ".join(job_profile.preferred_skills or []),
        " ".join(job_profile.technologies or []),
        " ".join(job_profile.mandatory_requirements or []),
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
        candidate.domain,
    )


def evaluate_domain_misalignment(
    candidate: AtsCandidateProfile,
    job_profile: JobProfile,
    job: dict[str, Any] | None = None,
) -> tuple[int, str | None]:
    """Penalize Web/eCommerce jobs when the CV leans heavily Mobile (and vice versa)."""
    job_text = _job_text_blob(job_profile, job)
    cv_text = _candidate_text_blob(candidate)

    job_web = _count_markers(job_text, WEB_ECOMMERCE_MARKERS)
    job_mobile = _count_markers(job_text, MOBILE_MARKERS)
    cv_web = _count_markers(cv_text, WEB_ECOMMERCE_MARKERS)
    cv_mobile = _count_markers(cv_text, MOBILE_MARKERS)

    job_is_web = job_web >= 2 and job_web > job_mobile
    job_is_mobile = job_mobile >= 2 and job_mobile > job_web
    cv_is_web = cv_web >= 2 and cv_web > cv_mobile
    cv_is_mobile = cv_mobile >= 2 and cv_mobile > cv_web

    if job_is_web and cv_is_mobile:
        return (
            DOMAIN_MISALIGNMENT_PENALTY,
            "Domain misalignment: Web/eCommerce role vs Mobile-heavy CV "
            f"(-{DOMAIN_MISALIGNMENT_PENALTY})",
        )
    if job_is_mobile and cv_is_web:
        return (
            DOMAIN_MISALIGNMENT_PENALTY,
            "Domain misalignment: Mobile role vs Web-heavy CV "
            f"(-{DOMAIN_MISALIGNMENT_PENALTY})",
        )
    return 0, None


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


def _critical_keyword_penalty(
    candidate: AtsCandidateProfile,
    job_profile: JobProfile,
    job: dict[str, Any] | None = None,
) -> tuple[int, list[str]]:
    """Heavy hits for critical JD keywords that are missing or weak in the CV."""
    job_text = _job_text_blob(job_profile, job)
    cv_text = _candidate_text_blob(candidate)
    missing: list[str] = []
    for keyword in CRITICAL_JD_KEYWORDS:
        if keyword in job_text and keyword not in cv_text:
            # Also try loose skill match for single-token keywords.
            if " " not in keyword and _candidate_has_skill(candidate, keyword):
                continue
            missing.append(keyword)
    if not missing:
        return 0, []
    penalty = min(len(missing) * CRITICAL_KEYWORD_PENALTY, 32)
    return penalty, missing


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

    domain_penalty, domain_reason = evaluate_domain_misalignment(
        candidate, job_profile, job
    )
    keyword_penalty, critical_missing = _critical_keyword_penalty(
        candidate, job_profile, job
    )
    if domain_penalty:
        weighted -= domain_penalty
    if keyword_penalty:
        weighted -= keyword_penalty

    mandatory_failed = bool(missing_mandatory)
    is_potential = False
    if mandatory_failed:
        is_potential = evaluate_potential_junior_match(
            candidate, job_profile, matched
        )
        if is_potential:
            # Soft ceiling only — do not force the Weak Match hard-cap for juniors.
            weighted = min(weighted, POTENTIAL_MATCH_SOFT_CAP)
        else:
            weighted = min(weighted, MANDATORY_FAIL_CAP)

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
    if critical_missing:
        all_reasons.insert(
            0,
            "Critical JD keywords missing/weak — no credit for potential: "
            f"{', '.join(critical_missing[:5])} (-{keyword_penalty})",
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
    elif mandatory_failed:
        all_reasons.insert(0, "Hard requirements not met — score capped")

    improvements = _build_improvements(missing, missing_mandatory)
    if critical_missing:
        for kw in critical_missing[:3]:
            improvements.insert(0, f"Add explicit evidence for critical keyword: {kw}")
    if domain_penalty:
        improvements.insert(
            0,
            "Align CV domain with the job (Web/eCommerce vs Mobile) — highlight matching product experience",
        )

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
        is_potential_junior_match=is_potential,
    )
