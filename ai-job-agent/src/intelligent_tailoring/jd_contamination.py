"""Detect job-posting language leaking into candidate resume claims.

Honest maximal tailoring may emphasize skills that overlap the JD, but must
never copy the employer's voice, second-person instructions, or motivational
slogans into the candidate's summary/bullets.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Pronouns / employer-voice tokens that must never appear as "skills" or
# title-cased crumbs in candidate claims.
_JD_VOICE_STOPWORDS = frozenset(
    {
        "you",
        "your",
        "yours",
        "we",
        "our",
        "ours",
        "us",
        "they",
        "their",
        "them",
        "i",
        "me",
        "my",
        "mine",
        "are",
        "is",
        "am",
        "be",
        "been",
        "being",
        "was",
        "were",
        "will",
        "would",
        "should",
        "must",
        "can",
        "could",
        "shall",
        "may",
        "might",
        "need",
        "want",
        "demand",
        "looking",
        "seeking",
        "hiring",
        "join",
        "joined",
        "now",
        "today",
        "best",
        "greatest",
        "awesome",
        "amazing",
        "passionate",
        "love",
        "hate",
        "please",
        "apply",
        "click",
        "here",
        "team",  # alone — "best in your team" style slogans
        "culture",
        "thrives",
    }
)

# Standalone stopwords used when extracting skill-like highlight tokens.
_SKILL_HIGHLIGHT_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "or",
        "the",
        "of",
        "for",
        "with",
        "to",
        "in",
        "at",
        "on",
        "from",
        "into",
        "as",
        "by",
        "via",
        "per",
        "plus",
        "also",
        "etc",
        "including",
        "include",
        "using",
        "use",
        "used",
        "able",
        "ability",
        "strong",
        "good",
        "great",
        "excellent",
        "solid",
        "deep",
        "hands",
        "handson",
        "experience",
        "experienced",
        "knowledge",
        "understanding",
        "familiar",
        "familiarity",
        "proficient",
        "proficiency",
        "required",
        "requirement",
        "requirements",
        "preferred",
        "preference",
        "qualification",
        "qualifications",
        "responsibility",
        "responsibilities",
        "must",
        "have",
        "has",
        "having",
        "years",
        "year",
        "yrs",
        "plus",
        "minimum",
        "least",
        "senior",
        "junior",
        "mid",
        "lead",
        "principal",
        "staff",
        "engineer",
        "developer",
        "software",
        "professional",
        "candidate",
        "person",
        "people",
        "someone",
        "who",
        "that",
        "this",
        "these",
        "those",
        "work",
        "working",
        "job",
        "role",
        "position",
        "company",
        "our",
        "you",
        "your",
        "we",
        "are",
        "is",
        "be",
        "best",
        "team",
    }
)

_SECOND_PERSON_RE = re.compile(
    r"\b(?:you|your|yours|you're|youre)\b",
    flags=re.I,
)
_EMPLOYER_WE_RE = re.compile(r"\b(?:we|our|ours|we're|were)\b", flags=re.I)
_MOTIVATIONAL_RE = re.compile(
    r"\b(?:"
    r"you are the best|best in (?:your|the) team|we demand|"
    r"now is the time|join us|we're looking|we are looking|"
    r"you will|you'll|you must|you should|you need|"
    r"must[- ]have|nice[- ]to[- ]have"
    r")\b",
    flags=re.I,
)
_WORD_RE = re.compile(r"[A-Za-z0-9+#.]{2,}|[\u0590-\u05FF]{2,}")


def normalize_for_overlap(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def word_tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text or "")]


def looks_like_jd_voice(text: str) -> bool:
    """True when text reads as employer/JD voice rather than candidate facts."""
    sample = (text or "").strip()
    if not sample:
        return False
    if _SECOND_PERSON_RE.search(sample):
        return True
    if _MOTIVATIONAL_RE.search(sample):
        return True
    # "We/Our" directed at company culture — not candidate achievements
    if _EMPLOYER_WE_RE.search(sample) and len(sample.split()) <= 16:
        return True
    return False


def is_skill_like_highlight_token(token: str) -> bool:
    t = (token or "").strip().lower()
    if len(t) < 2:
        return False
    if t in _SKILL_HIGHLIGHT_STOPWORDS or t in _JD_VOICE_STOPWORDS:
        return False
    # Prefer tokens that look technical / proper nouns (digit, #, +, camelish)
    if any(ch.isdigit() or ch in "#+." for ch in t):
        return True
    if t[:1].isupper() and t[1:].islower():
        return True
    # Common tech / domain tokens are lowercase but still skill-like
    return t not in _SKILL_HIGHLIGHT_STOPWORDS and len(t) >= 3


def extract_skill_highlight_tokens(requirement: str, *, max_tokens: int = 3) -> list[str]:
    """Pull skill-like nouns from a JD requirement; never pronouns/slogans."""
    words = word_tokens(requirement)
    kept: list[str] = []
    for w in words:
        if not is_skill_like_highlight_token(w):
            continue
        if w not in kept:
            kept.append(w)
        if len(kept) >= max_tokens:
            break
    return kept


def ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    if n <= 0 or len(tokens) < n:
        return set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def shared_ngrams(
    left: str,
    right: str,
    *,
    min_n: int = 5,
    max_n: int = 8,
) -> list[str]:
    """Return shared word n-grams (n>=min_n) between two texts."""
    a = word_tokens(left)
    b = word_tokens(right)
    if len(a) < min_n or len(b) < min_n:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for n in range(max_n, min_n - 1, -1):
        overlap = ngrams(a, n) & ngrams(b, n)
        for gram in sorted(overlap):
            phrase = " ".join(gram)
            if phrase in seen:
                continue
            # Skip if a longer containing gram already recorded
            if any(phrase in prior for prior in seen):
                continue
            seen.add(phrase)
            found.append(phrase)
    return found


def find_jd_contamination(
    candidate_text: str,
    *,
    jd_text: str,
    min_ngram: int = 5,
) -> dict[str, Any]:
    """Detect JD language reuse and employer voice inside candidate prose."""
    text = (candidate_text or "").strip()
    jd = (jd_text or "").strip()
    issues: list[str] = []
    overlaps: list[str] = []

    if not text:
        return {
            "contaminated": False,
            "issues": [],
            "shared_ngrams": [],
            "has_second_person": False,
            "has_motivational_jd_voice": False,
        }

    has_second = bool(_SECOND_PERSON_RE.search(text))
    has_motivational = bool(_MOTIVATIONAL_RE.search(text))
    if has_second:
        issues.append("second_person_voice")
    if has_motivational:
        issues.append("motivational_jd_voice")

    if jd:
        overlaps = shared_ngrams(text, jd, min_n=min_ngram)
        # Also catch title-cased slogan crumbs like "You Are Best" (3 tokens)
        # when those tokens form a contiguous JD sequence of length >= 3 and
        # include a second-person / motivational marker.
        short = shared_ngrams(text, jd, min_n=3, max_n=4)
        for phrase in short:
            tokens = phrase.split()
            if any(t in {"you", "your", "yours", "we", "our", "ours"} for t in tokens):
                if phrase not in overlaps:
                    overlaps.append(phrase)
        if overlaps:
            issues.append("jd_ngram_overlap")

    return {
        "contaminated": bool(issues),
        "issues": issues,
        "shared_ngrams": overlaps[:8],
        "has_second_person": has_second,
        "has_motivational_jd_voice": has_motivational,
    }


def strip_jd_contaminated_sentences(
    text: str,
    *,
    jd_text: str = "",
) -> str:
    """Drop summary sentences that look like JD voice / JD n-gram reuse."""
    sample = (text or "").strip()
    if not sample:
        return ""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", sample) if p.strip()]
    kept: list[str] = []
    for sent in parts:
        report = find_jd_contamination(sent, jd_text=jd_text, min_ngram=4)
        if report["contaminated"]:
            continue
        if not sent.endswith((".", "!", "?")):
            sent = sent + "."
        kept.append(sent)
    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def collect_resume_claim_texts(resume: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Return (path, text) pairs for summary + bullets + project descriptions."""
    data = resume if isinstance(resume, dict) else {}
    rows: list[tuple[str, str]] = []
    summary = str(
        data.get("professional_summary") or data.get("summary") or ""
    ).strip()
    if summary:
        rows.append(("summary", summary))
    for idx, entry in enumerate(data.get("experience") or []):
        if not isinstance(entry, dict):
            continue
        for bi, bullet in enumerate(entry.get("bullets") or []):
            text = str(bullet or "").strip()
            if text:
                rows.append((f"experience[{idx}].bullets[{bi}]", text))
    for idx, entry in enumerate(data.get("projects") or []):
        if not isinstance(entry, dict):
            continue
        desc = str(entry.get("description") or "").strip()
        if desc:
            rows.append((f"projects[{idx}].description", desc))
        for bi, bullet in enumerate(entry.get("bullets") or []):
            text = str(bullet or "").strip()
            if text:
                rows.append((f"projects[{idx}].bullets[{bi}]", text))
    return rows


def validate_resume_against_jd(
    resume: dict[str, Any] | None,
    *,
    jd_text: str,
) -> dict[str, Any]:
    """Scan resume claims for JD contamination. Deterministic, no LLM."""
    issues: list[dict[str, str]] = []
    if not (jd_text or "").strip():
        # Still catch second-person even without JD text available
        for path, text in collect_resume_claim_texts(resume):
            report = find_jd_contamination(text, jd_text="", min_ngram=5)
            if report["contaminated"]:
                for code in report["issues"]:
                    issues.append(
                        {
                            "code": code,
                            "path": path,
                            "message": f"Candidate claim uses employer/JD voice ({code}).",
                            "overlap": "",
                        }
                    )
        return {"passed": not issues, "issues": issues}

    for path, text in collect_resume_claim_texts(resume):
        report = find_jd_contamination(text, jd_text=jd_text, min_ngram=5)
        if not report["contaminated"]:
            continue
        overlap = "; ".join(report["shared_ngrams"][:3])
        for code in report["issues"]:
            msg = {
                "second_person_voice": (
                    "Claim uses second-person ('you/your') — that is employer voice, "
                    "not a candidate fact."
                ),
                "motivational_jd_voice": (
                    "Claim contains motivational/instructional JD language."
                ),
                "jd_ngram_overlap": (
                    f"Claim reuses job-posting wording"
                    + (f" (overlap: '{overlap}')" if overlap else "")
                    + ". Echo keywords only via truthful candidate phrasing."
                ),
            }.get(code, f"JD contamination ({code}).")
            issues.append(
                {
                    "code": code,
                    "path": path,
                    "message": msg,
                    "overlap": overlap,
                }
            )
    return {"passed": not issues, "issues": issues}


SOURCE_SEPARATION_INSTRUCTION = """
Rule: Only text inside <candidate_facts> may be used to generate claims about the
candidate. Text inside <job_posting> may only be used to decide what to emphasize
and which skill/keyword tokens to echo — never copied or paraphrased as a
first/second-person claim about the candidate.
The job posting may describe what the employer wants or how the employer talks
about itself/the role — it is NEVER a source of facts about the candidate.
""".strip()

SOURCE_SEPARATION_RULES = f"""
SOURCE SEPARATION (mandatory — honest maximal tailoring):
- <candidate_facts> is the ONLY source of claims about the candidate.
- <job_posting> is for relevance, ordering, emphasis, and keyword targeting ONLY.
- Never copy, paraphrase as a first/second-person claim, or title-case fragments
  from the job posting into the resume (Summary, bullets, titles, skills).
- Job postings may say "You are the best", "We demand a lot", "NOW is the time" —
  those are employer voice, NEVER candidate facts.
- Closing the gap between the candidate and the role is done by truthful
  rewording/re-emphasis of what the candidate actually did — never by borrowing
  the job posting's own language as claims about the candidate.

{SOURCE_SEPARATION_INSTRUCTION}
""".strip()


def wrap_candidate_facts(text: str) -> str:
    return f"<candidate_facts>\n{(text or '').strip()}\n</candidate_facts>"


def wrap_job_posting(text: str) -> str:
    return f"<job_posting>\n{(text or '').strip()}\n</job_posting>"


def format_source_separated_block(*, candidate_facts: str, job_posting: str) -> str:
    return (
        f"{wrap_candidate_facts(candidate_facts)}\n\n"
        f"{wrap_job_posting(job_posting)}\n\n"
        f"{SOURCE_SEPARATION_INSTRUCTION}"
    )


def summary_describes_candidate_only(summary: str) -> tuple[bool, list[str]]:
    """Lightweight sanity check: summary must describe the candidate, not JD voice.

    Returns (ok, error_codes). Fails when fragments read as instructions, opinions,
    or claims directed at/about someone other than the candidate.
    """
    text = (summary or "").strip()
    if not text:
        return False, ["empty_summary"]
    errors: list[str] = []
    report = find_jd_contamination(text, jd_text="", min_ngram=5)
    for code in report.get("issues") or []:
        errors.append(code)
    # Imperative / instructional openers that belong in JDs, not resumes
    if re.search(
        r"^(?:you must|you will|you should|you need|join us|apply now|"
        r"we demand|we are looking|we're looking)\b",
        text,
        flags=re.I,
    ):
        errors.append("instructional_jd_voice")
    # "Professional with You Are Best experience" style title-case crumbs
    if re.search(
        r"\b(?:with|and)\s+You\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\s+experience\b",
        text,
    ):
        errors.append("title_cased_jd_crumb")
    errors = list(dict.fromkeys(errors))
    return len(errors) == 0, errors
