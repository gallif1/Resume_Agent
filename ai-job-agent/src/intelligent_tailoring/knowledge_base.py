"""Resume Knowledge Base — atomic fact extraction with source traceability.

Extends existing resume_extraction without replacing the CV parser.
Every useful fact is stored separately; the original resume remains immutable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from intelligent_tailoring.experience_math import estimate_years_from_text
from intelligent_tailoring.stages.resume_extraction import extract_structured_resume

PARSER_VERSION = "resume_kb_v1"
KB_VERSION = "1"

# Coverage below this triggers a fallback extraction pass.
DEFAULT_COVERAGE_THRESHOLD = 0.55

_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_TOKEN_RE = re.compile(r"[A-Za-z\u0590-\u05FF0-9][\w\u0590-\u05FF.+#/\-]{1,}", re.U)
_METRIC_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?\s*%|\d+\+?\s*(?:people|employees|customers|users|"
    r"students|patients|clients|teams?|years?|months?|\$[\d,]+))\b",
    re.I,
)

FACT_TYPES = (
    "role",
    "task",
    "responsibility",
    "achievement",
    "project",
    "technology",
    "tool",
    "methodology",
    "education",
    "certification",
    "domain",
    "communication_activity",
    "leadership_activity",
    "customer_facing_activity",
    "administrative_activity",
    "operational_activity",
    "analytical_activity",
    "safety_activity",
    "training_activity",
    "problem_solving_activity",
    "ownership_activity",
    "architecture_activity",
    "debugging_activity",
    "optimization_activity",
    "automation_activity",
    "testing_activity",
    "monitoring_activity",
    "documentation_activity",
    "collaboration_activity",
    "initiative_activity",
    "decision_making_activity",
    "learning_activity",
    "scalability_activity",
    "measurable_result",
    "skill",
    "summary",
    "other",
)

# Patterns that classify bullet/activity text into fact types (profession-agnostic).
# Order matters: more specific evidence types are matched first.
_ACTIVITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("customer_facing_activity", re.compile(
        r"customer|client|complaint|ticket|service desk|front.?desk|"
        r"patient|guest|patron|לקוח|פניות", re.I
    )),
    ("leadership_activity", re.compile(
        r"\bled\b|\bmanaged\b|\bsupervised\b|\bmentored\b|team lead|"
        r"coordinat|delegat|ניהול צוות|הנחיה", re.I
    )),
    ("ownership_activity", re.compile(
        r"\bowned\b|ownership|accountable|end[- ]to[- ]end|"
        r"drove|championed|took ownership|solely responsible", re.I
    )),
    ("problem_solving_activity", re.compile(
        r"problem.?solv|root cause|diagnos|investigat|"
        r"troubleshoot|resolved|unblocked|workaround", re.I
    )),
    ("debugging_activity", re.compile(
        r"\bdebug(?:ged|ging)?\b|fix(?:ed|ing)?\s+(?:bug|issue|defect|incident)|"
        r"incident response|postmortem|root.?cause", re.I
    )),
    ("architecture_activity", re.compile(
        r"architect(?:ed|ure)?|system design|microservice|schema design|"
        r"data model|component design|infrastructure design", re.I
    )),
    ("scalability_activity", re.compile(
        r"scalab|high.?traffic|throughput|latency|distributed|"
        r"horizontal scale|load.?balanc|concurrency", re.I
    )),
    ("optimization_activity", re.compile(
        r"optimiz|performance|reduc(?:ed|ing)\s+(?:latency|cost|time)|"
        r"improv(?:ed|ing)\s+(?:speed|efficiency|throughput)|profil(?:ed|ing)", re.I
    )),
    ("automation_activity", re.compile(
        r"automat(?:ed|ion|ing)|script(?:ed|ing)|ci/?cd|orchestrat|"
        r"pipeline|self[- ]service|bot\b", re.I
    )),
    ("testing_activity", re.compile(
        r"\btest(?:ed|ing|s)?\b|qa\b|unit test|integration test|"
        r"regression|coverage|quality assurance|uat\b", re.I
    )),
    ("monitoring_activity", re.compile(
        r"monitor(?:ed|ing)|observability|alert(?:s|ing)|dashboard|"
        r"telemetry|logging|metrics|on[- ]call", re.I
    )),
    ("documentation_activity", re.compile(
        r"document(?:ed|ation|ing)|runbook|playbook|wiki|"
        r"wrote\s+(?:docs|spec|guide)|knowledge base", re.I
    )),
    ("collaboration_activity", re.compile(
        r"cross[- ]functional|collaborat|partner(?:ed|ing)\s+with|"
        r"stakeholder|worked with|interdisciplin", re.I
    )),
    ("initiative_activity", re.compile(
        r"initiated|proposed|self[- ]started|proactive|"
        r"volunteered|introduced|pioneered|started from scratch", re.I
    )),
    ("decision_making_activity", re.compile(
        r"decid(?:ed|ing)|chose|selected|trade[- ]off|"
        r"prioritiz|judgment|evaluated options", re.I
    )),
    ("learning_activity", re.compile(
        r"learn(?:ed|ing)|upskill|self[- ]taught|studied|"
        r"rapidly adopted|onboarded to|new (?:stack|tool|framework)", re.I
    )),
    ("training_activity", re.compile(
        r"train|teach|tutor|instruct|onboard|workshop|lesson|"
        r"curriculum|הדרכה|הוראה|העברת ידע", re.I
    )),
    ("administrative_activity", re.compile(
        r"invoice|billing|schedule|roster|filing|"
        r"record.?keep|appointment|admin|חשבונית|תיעוד", re.I
    )),
    ("operational_activity", re.compile(
        r"inventory|stock|warehouse|logistics|route|operations|"
        r"procurement|fulfillment|מלאי|לוגיסטיקה|תפעול", re.I
    )),
    ("analytical_activity", re.compile(
        r"analy[sz]|report|forecast|audit|reconcil|metric|"
        r"data entry|ניתוח|דוח", re.I
    )),
    ("safety_activity", re.compile(
        r"safety|inspection|compliance|hazard|PPE|quality control|"
        r"בטיחות|בקרת איכות", re.I
    )),
    ("communication_activity", re.compile(
        r"present|negotiat|communicat|wrote|drafted|email|phone|"
        r"social media|campaign|הצגה|תקשורת", re.I
    )),
]


@dataclass
class ResumeFact:
    id: str
    fact_type: str
    normalized_value: str
    original_text: str
    source_section: str = ""
    source_entry_id: str = ""
    source_page: int | None = None
    source_order: int = 0
    start_date: str = ""
    end_date: str = ""
    organization: str = ""
    role: str = ""
    context: str = ""
    explicit_skills: list[str] = field(default_factory=list)
    implied_competencies: list[str] = field(default_factory=list)
    confidence: float = 1.0
    extraction_method: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractionCoverageReport:
    source_fact_count: int = 0
    extracted_fact_count: int = 0
    extraction_coverage_score: float = 0.0
    potentially_missing_fragments: list[str] = field(default_factory=list)
    duplicated_facts: list[str] = field(default_factory=list)
    parsing_warnings: list[str] = field(default_factory=list)
    fallback_applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResumeKnowledgeBase:
    candidate_identity: dict[str, Any] = field(default_factory=dict)
    source_language: str = "en"
    target_output_language: str = "en"
    facts: list[ResumeFact] = field(default_factory=list)
    source_fragments: list[str] = field(default_factory=list)
    raw_text: str = ""
    coverage: ExtractionCoverageReport | None = None
    parser_version: str = PARSER_VERSION
    kb_version: str = KB_VERSION
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_identity": self.candidate_identity,
            "source_language": self.source_language,
            "target_output_language": self.target_output_language,
            "facts": [f.to_dict() for f in self.facts],
            "source_fragments": list(self.source_fragments),
            "raw_text": self.raw_text,
            "coverage": self.coverage.to_dict() if self.coverage else {},
            "parser_version": self.parser_version,
            "kb_version": self.kb_version,
            "content_hash": self.content_hash,
            # Convenience groupings (derived, not authoritative)
            "employment_history": self.facts_by_type("role"),
            "project_history": self.facts_by_type("project"),
            "education": self.facts_by_type("education"),
            "certifications": self.facts_by_type("certification"),
            "tools": self.facts_by_type("tool") + self.facts_by_type("technology"),
            "achievements": self.facts_by_type("achievement")
            + self.facts_by_type("measurable_result"),
            "responsibilities": self.facts_by_type("responsibility")
            + self.facts_by_type("task"),
        }

    def facts_by_type(self, fact_type: str) -> list[dict[str, Any]]:
        return [f.to_dict() for f in self.facts if f.fact_type == fact_type]

    def fact_by_id(self, fact_id: str) -> ResumeFact | None:
        for f in self.facts:
            if f.id == fact_id:
                return f
        return None

    def all_original_texts(self) -> list[str]:
        return [f.original_text for f in self.facts if f.original_text.strip()]


def _fact_id(*parts: str) -> str:
    blob = "|".join(p.strip().lower() for p in parts if p)
    return "fact_" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _detect_language(text: str) -> str:
    if not text.strip():
        return "en"
    hebrew = len(_HEBREW_RE.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if hebrew > latin * 0.3 and hebrew >= 20:
        return "he"
    return "en"


def _classify_activity(text: str) -> str:
    for fact_type, pattern in _ACTIVITY_PATTERNS:
        if pattern.search(text):
            return fact_type
    if _METRIC_RE.search(text):
        return "measurable_result"
    return "responsibility"


def _split_fragments(text: str) -> list[str]:
    """Split raw resume text into candidate fact fragments."""
    if not text:
        return []
    # Prefer line / bullet boundaries
    parts = re.split(r"[\n\r]+|(?<=[.!?])\s+(?=[A-Z\u0590-\u05FF])|[•●▪◦\-–—]\s*", text)
    fragments: list[str] = []
    for p in parts:
        cleaned = re.sub(r"\s+", " ", p).strip(" \t•●▪◦-–—")
        if len(cleaned) < 8:
            continue
        if cleaned not in fragments:
            fragments.append(cleaned)
    return fragments


def build_knowledge_base(
    cv_profile: dict[str, Any],
    source_documents: str | None = None,
    *,
    target_output_language: str | None = None,
    coverage_threshold: float = DEFAULT_COVERAGE_THRESHOLD,
) -> ResumeKnowledgeBase:
    """Build a reusable ResumeKnowledgeBase from profile + source documents."""
    # Keep original casing — do NOT use lowered source_resume_text for KB.
    from match_tailor_service import build_candidate_payload

    raw_profile_text = build_candidate_payload(cv_profile, source_documents)
    # Prefer explicit source documents when available (full prose)
    if source_documents and len(source_documents.strip()) > 80:
        raw_text = f"{source_documents.strip()}\n\n{raw_profile_text}"
    else:
        raw_text = raw_profile_text

    structured = extract_structured_resume(cv_profile, source_documents)
    # Prefer cased payload over lowered raw_text from extract_structured_resume
    if not raw_text.strip():
        raw_text = str(structured.get("raw_text") or "")

    source_lang = _detect_language(raw_text)
    out_lang = target_output_language or source_lang

    facts: list[ResumeFact] = []
    order = 0

    contact = structured.get("contact") if isinstance(structured.get("contact"), dict) else {}
    identity = {
        "name": contact.get("name") or (cv_profile.get("contact") or {}).get("name") or "",
        "email": contact.get("email") or "",
        "phone": contact.get("phone") or "",
        "location": contact.get("location") or "",
    }

    # --- Skills / tools / technologies ---
    display_skills = list(structured.get("skills") or [])
    skills_block = cv_profile.get("skills") or {}
    if isinstance(skills_block, dict):
        for cat, vals in skills_block.items():
            items = vals if isinstance(vals, list) else ([vals] if vals else [])
            for s in items:
                text = str(s).strip()
                if not text:
                    continue
                order += 1
                facts.append(
                    ResumeFact(
                        id=_fact_id("skill", cat, text),
                        fact_type="technology" if _looks_technical(text) else "skill",
                        normalized_value=text.lower(),
                        original_text=text,
                        source_section="skills",
                        source_entry_id=str(cat),
                        source_order=order,
                        explicit_skills=[text],
                        confidence=1.0,
                        extraction_method="profile",
                    )
                )
    else:
        for s in display_skills:
            text = str(s).strip()
            if not text:
                continue
            order += 1
            facts.append(
                ResumeFact(
                    id=_fact_id("skill", text),
                    fact_type="skill",
                    normalized_value=text.lower(),
                    original_text=text,
                    source_section="skills",
                    source_order=order,
                    explicit_skills=[text],
                    confidence=1.0,
                    extraction_method="profile",
                )
            )

    # --- Employment ---
    for role_idx, role in enumerate(structured.get("experience_roles") or []):
        if not isinstance(role, dict):
            continue
        company = str(role.get("company") or "")
        title = str(role.get("title") or "")
        dates = str(role.get("dates") or "")
        entry_id = f"role_{role_idx}"
        order += 1
        facts.append(
            ResumeFact(
                id=_fact_id("role", company, title, dates),
                fact_type="role",
                normalized_value=f"{title} @ {company}".strip(" @"),
                original_text=f"{title} at {company} ({dates})".strip(),
                source_section="experience",
                source_entry_id=entry_id,
                source_order=order,
                start_date=dates,
                organization=company,
                role=title,
                confidence=1.0,
                extraction_method="profile",
            )
        )
        for b_idx, bullet in enumerate(role.get("bullets") or []):
            text = str(bullet).strip()
            if not text:
                continue
            order += 1
            ftype = _classify_activity(text)
            facts.append(
                ResumeFact(
                    id=_fact_id("bullet", entry_id, str(b_idx), text),
                    fact_type=ftype,
                    normalized_value=text.lower(),
                    original_text=text,
                    source_section="experience",
                    source_entry_id=entry_id,
                    source_order=order,
                    start_date=dates,
                    organization=company,
                    role=title,
                    context=title,
                    confidence=1.0,
                    extraction_method="profile",
                )
            )

    # --- Projects ---
    for p_idx, proj in enumerate(structured.get("projects") or []):
        if not isinstance(proj, dict):
            continue
        name = str(proj.get("name") or "")
        desc = str(proj.get("description") or "")
        entry_id = f"project_{p_idx}"
        order += 1
        facts.append(
            ResumeFact(
                id=_fact_id("project", name, desc),
                fact_type="project",
                normalized_value=name.lower() or desc.lower(),
                original_text=f"{name}: {desc}".strip(": "),
                source_section="projects",
                source_entry_id=entry_id,
                source_order=order,
                confidence=1.0,
                extraction_method="profile",
            )
        )
        # Explicit technology bindings for this project only
        for tech in proj.get("technologies") or proj.get("tech") or []:
            text = str(tech).strip()
            if not text:
                continue
            order += 1
            facts.append(
                ResumeFact(
                    id=_fact_id("proj_tech", entry_id, text),
                    fact_type="technology",
                    normalized_value=text.lower(),
                    original_text=text,
                    source_section="projects",
                    source_entry_id=entry_id,
                    source_order=order,
                    context=name,
                    explicit_skills=[text],
                    confidence=1.0,
                    extraction_method="profile",
                )
            )
        for b_idx, bullet in enumerate(proj.get("bullets") or []):
            text = str(bullet).strip()
            if not text:
                continue
            order += 1
            facts.append(
                ResumeFact(
                    id=_fact_id("proj_bullet", entry_id, str(b_idx), text),
                    fact_type=_classify_activity(text),
                    normalized_value=text.lower(),
                    original_text=text,
                    source_section="projects",
                    source_entry_id=entry_id,
                    source_order=order,
                    context=name,
                    confidence=1.0,
                    extraction_method="profile",
                )
            )

    # --- Education ---
    for e_idx, edu in enumerate(structured.get("education") or []):
        if isinstance(edu, dict):
            text = " — ".join(
                str(edu.get(k) or "")
                for k in ("institution", "degree", "field", "dates", "specialization")
                if edu.get(k)
            ) or str(edu)
        else:
            text = str(edu)
        text = text.strip()
        if not text:
            continue
        order += 1
        facts.append(
            ResumeFact(
                id=_fact_id("edu", str(e_idx), text),
                fact_type="education",
                normalized_value=text.lower(),
                original_text=text,
                source_section="education",
                source_entry_id=f"edu_{e_idx}",
                source_order=order,
                organization=str(edu.get("institution") or "") if isinstance(edu, dict) else "",
                confidence=1.0,
                extraction_method="profile",
            )
        )

    # --- Certifications ---
    for c_idx, cert in enumerate(structured.get("certifications") or []):
        text = str(cert.get("name") if isinstance(cert, dict) else cert).strip()
        if not text:
            continue
        order += 1
        facts.append(
            ResumeFact(
                id=_fact_id("cert", str(c_idx), text),
                fact_type="certification",
                normalized_value=text.lower(),
                original_text=text,
                source_section="certifications",
                source_entry_id=f"cert_{c_idx}",
                source_order=order,
                confidence=1.0,
                extraction_method="profile",
            )
        )

    # --- Summary facts from profile ---
    summary = ""
    for key in ("summary", "professional_summary", "about"):
        if cv_profile.get(key):
            summary = str(cv_profile[key])
            break
    if summary.strip():
        for frag in _split_fragments(summary):
            order += 1
            facts.append(
                ResumeFact(
                    id=_fact_id("summary", frag),
                    fact_type="summary",
                    normalized_value=frag.lower(),
                    original_text=frag,
                    source_section="summary",
                    source_order=order,
                    confidence=0.95,
                    extraction_method="profile",
                )
            )

    fragments = _split_fragments(raw_text)
    coverage = validate_extraction_coverage(facts, fragments, raw_text)

    # Fallback: recover missing fragments as facts
    if coverage.extraction_coverage_score < coverage_threshold:
        recovered = fallback_extract_missing(facts, coverage.potentially_missing_fragments)
        facts.extend(recovered)
        coverage.fallback_applied = bool(recovered)
        coverage = validate_extraction_coverage(facts, fragments, raw_text)
        coverage.fallback_applied = bool(recovered)

    content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:24]

    # Attach ontology-implied competencies onto facts (deterministic)
    try:
        from intelligent_tailoring.ontology import get_ontology

        ontology = get_ontology()
        for fact in facts:
            hits = ontology.infer_from_resume_text(
                fact.original_text, min_confidence=0.8, language=source_lang
            )
            implied: list[str] = []
            for hit in hits:
                implied.append(hit.inferred_competency or hit.relation.target)
                implied.extend(list(hit.relation.also_implies))
            seen: set[str] = set()
            cleaned: list[str] = []
            for c in implied:
                key = c.lower().strip()
                if key and key not in seen:
                    seen.add(key)
                    cleaned.append(c)
            fact.implied_competencies = cleaned
    except Exception:  # noqa: BLE001
        pass

    return ResumeKnowledgeBase(
        candidate_identity=identity,
        source_language=source_lang,
        target_output_language=out_lang,
        facts=facts,
        source_fragments=fragments,
        raw_text=raw_text,
        coverage=coverage,
        content_hash=content_hash,
    )


def _looks_technical(text: str) -> bool:
    return bool(
        re.search(
            r"python|java|sql|react|api|aws|docker|excel|crm|erp|sap|"
            r"linux|git|html|css|\.net|node",
            text,
            re.I,
        )
    )


def validate_extraction_coverage(
    facts: list[ResumeFact],
    fragments: list[str],
    raw_text: str,
) -> ExtractionCoverageReport:
    """Compare extracted facts against source fragments to detect lost information."""
    fact_texts_norm = {_norm(f.original_text) for f in facts}
    fact_tokens: set[str] = set()
    for f in facts:
        fact_tokens |= set(_TOKEN_RE.findall(f.original_text.lower()))

    missing: list[str] = []
    covered = 0
    for frag in fragments:
        n = _norm(frag)
        if len(n) < 12:
            continue
        # Covered if substring match or high token overlap
        if any(n in ft or ft in n for ft in fact_texts_norm if len(ft) > 10):
            covered += 1
            continue
        frag_tokens = set(_TOKEN_RE.findall(frag.lower()))
        if frag_tokens and len(frag_tokens & fact_tokens) / max(len(frag_tokens), 1) >= 0.6:
            covered += 1
            continue
        # Skip fragments that are just contact/header noise
        if re.search(r"@|tel:|phone:|linkedin\.com|http", frag, re.I):
            covered += 1
            continue
        missing.append(frag[:240])

    total = covered + len(missing)
    score = (covered / total) if total else 1.0

    # Duplicate detection
    seen: dict[str, int] = {}
    dups: list[str] = []
    for f in facts:
        key = _norm(f.original_text)
        seen[key] = seen.get(key, 0) + 1
    for key, count in seen.items():
        if count > 1 and key:
            dups.append(key[:120])

    warnings: list[str] = []
    if score < 0.7:
        warnings.append(
            f"Extraction coverage {score:.0%} is below recommended 70%; "
            f"{len(missing)} fragments may be under-represented."
        )
    # Detect quantified achievements in source missing from facts
    for m in _METRIC_RE.finditer(raw_text or ""):
        metric = m.group(0)
        if not any(metric.lower() in f.original_text.lower() for f in facts):
            warnings.append(f"Quantified detail possibly lost: {metric}")

    return ExtractionCoverageReport(
        source_fact_count=total or len(fragments),
        extracted_fact_count=len(facts),
        extraction_coverage_score=round(score, 4),
        potentially_missing_fragments=missing[:40],
        duplicated_facts=dups[:20],
        parsing_warnings=warnings[:20],
    )


def fallback_extract_missing(
    existing: list[ResumeFact],
    missing_fragments: list[str],
) -> list[ResumeFact]:
    """Create facts from fragments that were not covered by structured extraction."""
    existing_norms = {_norm(f.original_text) for f in existing}
    recovered: list[ResumeFact] = []
    for i, frag in enumerate(missing_fragments[:30]):
        n = _norm(frag)
        if n in existing_norms or len(frag) < 12:
            continue
        recovered.append(
            ResumeFact(
                id=_fact_id("fallback", str(i), frag),
                fact_type=_classify_activity(frag),
                normalized_value=n,
                original_text=frag,
                source_section="source_fragment",
                source_entry_id=f"fallback_{i}",
                source_order=10000 + i,
                confidence=0.75,
                extraction_method="fallback",
            )
        )
        existing_norms.add(n)
    return recovered


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def knowledge_base_to_resume_facts(kb: ResumeKnowledgeBase) -> dict[str, Any]:
    """Adapt KB into the resume_facts dict shape expected by existing stages."""
    skills: list[str] = []
    roles_map: dict[str, dict[str, Any]] = {}
    projects_map: dict[str, dict[str, Any]] = {}
    education: list[Any] = []
    certifications: list[Any] = []

    for f in kb.facts:
        if f.fact_type in ("skill", "technology", "tool"):
            if f.original_text not in skills:
                skills.append(f.original_text)
        elif f.fact_type == "role":
            roles_map[f.source_entry_id] = {
                "company": f.organization,
                "title": f.role,
                "dates": f.start_date,
                "bullets": roles_map.get(f.source_entry_id, {}).get("bullets") or [],
            }
        elif f.source_section == "experience" and f.fact_type != "role":
            entry = roles_map.setdefault(
                f.source_entry_id,
                {
                    "company": f.organization,
                    "title": f.role,
                    "dates": f.start_date,
                    "bullets": [],
                },
            )
            entry["bullets"].append(f.original_text)
        elif f.fact_type == "project":
            projects_map[f.source_entry_id] = {
                "name": f.original_text.split(":")[0].strip(),
                "description": ":".join(f.original_text.split(":")[1:]).strip(),
                "bullets": projects_map.get(f.source_entry_id, {}).get("bullets") or [],
                "technologies": projects_map.get(f.source_entry_id, {}).get("technologies")
                or [],
            }
        elif f.source_section == "projects" and f.fact_type == "technology":
            entry = projects_map.setdefault(
                f.source_entry_id,
                {"name": f.context or "", "description": "", "bullets": [], "technologies": []},
            )
            entry.setdefault("technologies", []).append(f.original_text)
        elif f.source_section == "projects" and f.fact_type != "project":
            entry = projects_map.setdefault(
                f.source_entry_id,
                {"name": f.context or "", "description": "", "bullets": [], "technologies": []},
            )
            entry["bullets"].append(f.original_text)
        elif f.fact_type == "education":
            education.append({"institution": f.organization, "degree": f.original_text})
        elif f.fact_type == "certification":
            certifications.append(f.original_text)

    # Preserve role order by source_entry_id
    roles = [roles_map[k] for k in sorted(roles_map.keys())]
    projects = [projects_map[k] for k in sorted(projects_map.keys())]

    years = estimate_years_from_text(kb.raw_text)

    return {
        "raw_text": kb.raw_text,
        "candidate_payload": kb.raw_text,
        "contact": kb.candidate_identity,
        "skills": skills,
        "display_skills": skills,
        "experience_roles": roles,
        "projects": projects,
        "education": education,
        "certifications": certifications,
        "years_of_experience": years,
        "sparse": len(kb.facts) < 3 and len(kb.raw_text.strip()) < 120,
        "knowledge_base": kb.to_dict(),
        "fact_ids": [f.id for f in kb.facts],
    }


def score_facts_for_job(
    kb: ResumeKnowledgeBase,
    *,
    job_requirements: dict[str, Any],
    evidence_terms: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Score every resume fact 0-100 for relevance to the target job (universal)."""
    terms: list[str] = []
    for key in (
        "required_skills",
        "preferred_skills",
        "hard_requirements",
        "soft_requirements",
        "responsibilities",
        "tools_technologies",
        "ats_keywords",
        "industry_terminology",
        "soft_skills",
    ):
        vals = job_requirements.get(key) or []
        if isinstance(vals, list):
            terms.extend(str(v).lower() for v in vals)
    if evidence_terms:
        terms.extend(str(t).lower() for t in evidence_terms)
    terms = [t for t in terms if t.strip()]

    scored: list[dict[str, Any]] = []
    for f in kb.facts:
        text = f.original_text.lower()
        score = 15  # baseline: fact exists
        hits = sum(1 for t in terms if t and (t in text or text in t))
        score += min(50, hits * 10)
        # Recency / employment context boost
        if f.source_section == "experience":
            score += 8
        if f.source_section == "projects":
            score += 5
        if f.fact_type in ("achievement", "measurable_result"):
            score += 6
        if f.extraction_method == "fallback":
            score -= 5
        # Soft-skill evidence types get boost when soft skills are in JD
        soft_blob = " ".join(str(s).lower() for s in (job_requirements.get("soft_skills") or []))
        if soft_blob and f.fact_type in (
            "communication_activity",
            "customer_facing_activity",
            "leadership_activity",
            "training_activity",
        ):
            score += 10
        scored.append(
            {
                "fact_id": f.id,
                "fact_type": f.fact_type,
                "original_text": f.original_text,
                "source_section": f.source_section,
                "score": max(0, min(100, score)),
                "organization": f.organization,
                "role": f.role,
            }
        )
    scored.sort(key=lambda x: -x["score"])
    return scored
