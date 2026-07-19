"""Lightweight deterministic job matching using universal candidate profile (no AI)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ai_client import clamp_score
from ats_scorer import SENIORITY_ORDER
from job_analyzer import JobProfile
from multilingual_normalizer import (
    best_title_similarity,
    expand_synonyms,
    terms_overlap,
    to_canonical,
)

EXCLUSION_FAIL_CAP = 49

WEIGHT_TITLE = 0.25
WEIGHT_SKILLS = 0.25
WEIGHT_DOMAIN = 0.15
WEIGHT_SENIORITY = 0.15
WEIGHT_KEYWORDS = 0.10
WEIGHT_LOCATION = 0.10


@dataclass
class ProfileMatchResult:
    score: int
    score_label: str
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    score_reasons: list[str] = field(default_factory=list)
    component_scores: dict[str, float] = field(default_factory=dict)
    exclusion_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "score_label": self.score_label,
            "matched_skills": self.matched_skills,
            "missing_skills": self.missing_skills,
            "matched_keywords": self.matched_keywords,
            "score_reasons": self.score_reasons,
            "component_scores": self.component_scores,
            "exclusion_hit": self.exclusion_hit,
        }


def _score_label(score: int) -> str:
    if score >= 85:
        return "Excellent Match"
    if score >= 70:
        return "Good Match"
    if score >= 50:
        return "Partial Match"
    return "Weak Match"


def _job_text(job: dict[str, Any], job_profile: JobProfile | None = None) -> str:
    parts = [
        job.get("title") or "",
        job.get("description") or "",
        job.get("full_description") or "",
        job.get("location") or "",
    ]
    if job_profile and job_profile.title:
        parts.insert(0, job_profile.title)
    return "\n".join(p for p in parts if p)


def _title_score(universal: dict[str, Any], job: dict[str, Any]) -> tuple[float, list[str]]:
    title = str(job.get("title") or "")
    roles = list(universal.get("preferred_role_titles") or []) + list(
        universal.get("alternative_role_titles") or universal.get("canonical_roles") or []
    )
    if not title or not roles:
        return 0.0, []
    similarity = best_title_similarity(title, roles)
    reasons = []
    if similarity >= 0.5:
        reasons.append(f"Title similarity {similarity:.0%}")
    return similarity, reasons


def _skill_score(
    universal: dict[str, Any],
    job_text: str,
    job_profile: JobProfile | None,
) -> tuple[float, list[str], list[str]]:
    skills = list(universal.get("canonical_skills") or [])
    tech = list(universal.get("technologies_tools") or [])
    all_skills = skills + tech

    if job_profile:
        required = list(job_profile.required_skills or []) + list(job_profile.technologies or [])
        if required:
            matched, missing = terms_overlap(all_skills, job_text)
            for req in required:
                req_canon = to_canonical(req)
                if req_canon and any(
                    to_canonical(m).lower() == req_canon.lower() for m in matched
                ):
                    continue
                if to_canonical(req) and not any(
                    to_canonical(req).lower() in to_canonical(m).lower()
                    or to_canonical(m).lower() in to_canonical(req).lower()
                    for m in matched
                ):
                    if req_canon not in missing:
                        found_in_text = any(
                            variant.lower() in job_text.lower()
                            for variant in expand_synonyms(req_canon)
                        )
                        if found_in_text and req_canon not in matched:
                            matched.append(req_canon)
                        elif req_canon not in missing:
                            missing.append(req_canon)

            ratio = len(matched) / max(len(required), 1)
            return min(1.0, ratio), matched, missing

    matched, missing = terms_overlap(all_skills, job_text)
    ratio = len(matched) / max(len(all_skills), 1) if all_skills else 0.0
    return min(1.0, ratio), matched, missing


def _domain_score(
    universal: dict[str, Any],
    job_text: str,
    job: dict[str, Any] | None = None,
    job_profile: JobProfile | None = None,
) -> tuple[float, list[str]]:
    """Score domain alignment using free-form CV/JD domain signals (no taxonomy)."""
    domains = list(universal.get("domain_keywords") or [])
    core = str(universal.get("core_professional_domain") or "").strip()
    if core and core not in domains:
        domains = [core, *domains]
    roles = list(universal.get("preferred_role_titles") or []) + list(
        universal.get("canonical_roles") or []
    )

    target_signals: list[str] = []
    if job_profile and job_profile.professional_domain:
        target_signals.append(job_profile.professional_domain)
    if job_profile and job_profile.title:
        target_signals.append(job_profile.title)
    if job and job.get("title"):
        target_signals.append(str(job.get("title")))

    reasons: list[str] = []
    keyword_ratio = 0.5
    if domains:
        matched, _ = terms_overlap(domains, job_text)
        keyword_ratio = len(matched) / max(len(domains), 1)
        if matched:
            reasons.append(f"Domain match: {', '.join(matched[:3])}")

    title_ratio = 0.5
    if roles and target_signals:
        title_ratio = max(
            (best_title_similarity(signal, roles) for signal in target_signals),
            default=0.0,
        )
        if title_ratio < 0.22:
            reasons.append(
                "Domain misalignment: candidate core track vs target role "
                f"(similarity={title_ratio:.0%})"
            )
        elif title_ratio >= 0.5:
            reasons.append(f"Domain/title alignment {title_ratio:.0%}")

    # Blend keyword overlap with role/domain title similarity.
    if domains and roles and target_signals:
        score = 0.45 * keyword_ratio + 0.55 * title_ratio
    elif roles and target_signals:
        score = title_ratio
    elif domains:
        score = keyword_ratio
    else:
        score = 0.5
    return min(1.0, max(0.0, score)), reasons


def _seniority_score(universal: dict[str, Any], job_profile: JobProfile | None, job_text: str) -> tuple[float, list[str]]:
    candidate_level = str(universal.get("seniority_level") or "unknown").lower()
    job_level = "unknown"
    if job_profile and job_profile.seniority:
        job_level = str(job_profile.seniority).lower()

    if job_level == "unknown":
        text_l = job_text.lower()
        for level in SENIORITY_ORDER:
            if level == "unknown":
                continue
            for variant in expand_synonyms(level):
                if variant.lower() in text_l:
                    job_level = level
                    break
            if job_level != "unknown":
                break

    cand_rank = SENIORITY_ORDER.get(candidate_level, 3)
    job_rank = SENIORITY_ORDER.get(job_level, 3)
    distance = abs(cand_rank - job_rank)
    score = max(0.0, 1.0 - distance * 0.25)
    reasons = []
    if distance == 0:
        reasons.append(f"Seniority match: {candidate_level}")
    elif distance >= 3:
        reasons.append(f"Seniority gap: candidate {candidate_level}, job {job_level}")
    return score, reasons


def _keyword_score(universal: dict[str, Any], job_text: str) -> tuple[float, list[str]]:
    keywords = list(universal.get("search_keywords_en") or []) + list(
        universal.get("search_keywords_he") or []
    )
    if not keywords:
        return 0.5, []
    matched, _ = terms_overlap(keywords, job_text)
    ratio = len(matched) / max(len(keywords), 1)
    return ratio, matched


def _location_score(universal: dict[str, Any], job: dict[str, Any], job_text: str) -> tuple[float, list[str]]:
    location_prefs = universal.get("location_preferences") or {}
    preferred = list(location_prefs.get("preferred_locations") or [])
    remote_ok = bool(location_prefs.get("remote_ok", True))

    job_location = str(job.get("location") or "").lower()
    text_l = job_text.lower()
    reasons: list[str] = []

    if remote_ok and any(
        kw in text_l for kw in ("remote", "hybrid", "מהבית", "עבודה מהבית", "work from home")
    ):
        return 1.0, ["Remote/hybrid acceptable"]

    if not preferred:
        return 0.7, []

    for loc in preferred:
        loc_l = loc.lower()
        if loc_l in job_location or loc_l in text_l:
            reasons.append(f"Location match: {loc}")
            return 1.0, reasons

    return 0.3, []


def _check_exclusions(universal: dict[str, Any], job: dict[str, Any], job_text: str) -> bool:
    exclusions = list(universal.get("exclusion_keywords") or [])
    title_l = str(job.get("title") or "").lower()
    text_l = job_text.lower()
    for term in exclusions:
        term_l = str(term).lower().strip()
        if len(term_l) < 2:
            continue
        if term_l in title_l or term_l in text_l:
            return True
    return False


def score(
    universal: dict[str, Any],
    job: dict[str, Any],
    job_profile: JobProfile | None = None,
) -> ProfileMatchResult:
    """Score a job against the universal candidate profile (deterministic, no AI)."""
    job_text = _job_text(job, job_profile)
    reasons: list[str] = []

    if _check_exclusions(universal, job, job_text):
        return ProfileMatchResult(
            score=EXCLUSION_FAIL_CAP,
            score_label="Weak Match",
            score_reasons=["Exclusion keyword matched"],
            exclusion_hit=True,
        )

    title_s, title_reasons = _title_score(universal, job)
    skill_s, matched_skills, missing_skills = _skill_score(universal, job_text, job_profile)
    domain_s, domain_reasons = _domain_score(universal, job_text, job, job_profile)
    seniority_s, seniority_reasons = _seniority_score(universal, job_profile, job_text)
    keyword_s, matched_keywords = _keyword_score(universal, job_text)
    location_s, location_reasons = _location_score(universal, job, job_text)

    reasons.extend(title_reasons + domain_reasons + seniority_reasons + location_reasons)

    components = {
        "title": round(title_s * 100, 1),
        "skills": round(skill_s * 100, 1),
        "domain": round(domain_s * 100, 1),
        "seniority": round(seniority_s * 100, 1),
        "keywords": round(keyword_s * 100, 1),
        "location": round(location_s * 100, 1),
    }

    weighted = (
        title_s * WEIGHT_TITLE
        + skill_s * WEIGHT_SKILLS
        + domain_s * WEIGHT_DOMAIN
        + seniority_s * WEIGHT_SENIORITY
        + keyword_s * WEIGHT_KEYWORDS
        + location_s * WEIGHT_LOCATION
    )
    final_score = clamp_score(int(round(weighted * 100)))

    return ProfileMatchResult(
        score=final_score,
        score_label=_score_label(final_score),
        matched_skills=matched_skills,
        missing_skills=missing_skills,
        matched_keywords=matched_keywords,
        score_reasons=reasons,
        component_scores=components,
    )
