"""Generic resume parser.

Reads a PDF resume and produces a structured JSON profile that works across
fields (tech, finance, marketing, sales, operations, HR, design, healthcare,
logistics, ...). Nothing about a specific CV is hardcoded; everything is driven
by the keyword dictionaries in skills.py and the heading lists below.

When OPENAI_API_KEY is set in .env, OpenAI enriches the profile with smarter
extraction and career insights (see cv_analyzer.py).

Run with:  python src/parse_cv.py
           python src/parse_cv.py --no-ai   # rule-based only
"""

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dateutil import parser as date_parser

from config import CV_PATH, CV_PROFILE_PATH
from console_utils import configure_console, safe_print
from cv_domain import refine_profile
from cv_reader import (
    IMAGE_EXTENSIONS,
    diagnose_pdf,
    extract_text_from_resume,
    find_resume_path,
    load_image_bytes,
    page_images_have_content,
    render_pdf_pages,
)
from prompts import ask_yes_no
from skills import detect_skills_by_category
from universal_profile import (
    apply_universal_profile_to_cv,
    build_universal_profile_fallback,
    extract_universal_profile,
    extract_universal_profile_vision,
)
from cv_analyzer import is_ai_available

# ---------------------------------------------------------------------------
# Section headings
# ---------------------------------------------------------------------------
# Each canonical section maps to the headings (English + Hebrew) that may
# introduce it in a resume. Matching is case-insensitive.
SECTION_HEADINGS: dict[str, list[str]] = {
    "summary": [
        "summary", "profile", "objective", "about", "about me",
        "professional summary", "career objective",
        "תקציר", "פרופיל", "תמצית", "אודות",
    ],
    "experience": [
        "experience", "work experience", "employment history",
        "professional experience", "work history", "career history",
        "ניסיון", "ניסיון תעסוקתי", "ניסיון מקצועי", "ניסיון עבודה",
    ],
    "education": [
        "education", "academic background", "academic qualifications",
        "השכלה", "השכלה אקדמית",
    ],
    "skills": [
        "skills", "technical skills", "core skills", "key skills",
        "core competencies", "technical proficiencies", "areas of expertise",
        "כישורים", "מיומנויות", "כישורים טכניים", "מיומנויות טכניות",
    ],
    "projects": [
        "projects", "personal projects", "selected projects", "key projects",
        "פרויקטים", "פרוייקטים", "פרויקטים נבחרים",
    ],
    "certifications": [
        "certifications", "certificates", "licenses", "certification",
        "licenses & certifications", "הסמכות", "תעודות", "רישיונות",
    ],
    "languages": [
        "languages", "language proficiency", "שפות",
    ],
    "military_service": [
        "military service", "military", "army service", "idf",
        "שירות צבאי", "צבא", "שירות לאומי",
    ],
    "volunteering": [
        "volunteer experience", "volunteering", "volunteer work", "volunteer",
        "community service", "התנדבות", "פעילות התנדבותית",
    ],
    "awards": [
        "awards", "honors", "achievements", "awards & honors",
        "accomplishments", "פרסים", "הישגים", "הצטיינות",
    ],
}

# All section keys that appear in the output schema.
SECTION_KEYS = list(SECTION_HEADINGS.keys()) + ["other"]

# ---------------------------------------------------------------------------
# Keyword lists for experience / education heuristics
# ---------------------------------------------------------------------------
SENIOR_KEYWORDS = ["senior", "sr.", "principal", "expert", "בכיר"]
LEAD_KEYWORDS = ["team lead", "tech lead", "lead ", "lead,", "leader"]
MANAGER_KEYWORDS = [
    "manager", "head of", "director", "vp ", "chief", "מנהל", "ראש צוות",
]
JUNIOR_KEYWORDS = ["junior", "jr.", "entry level", "entry-level", "זוטר"]
INTERN_KEYWORDS = ["intern", "internship", "trainee", "apprentice", "מתמחה"]
STUDENT_KEYWORDS = [
    "student", "capstone", "tutor", "teaching assistant", "סטודנט", "פרויקט גמר",
]
MANAGEMENT_SIGNALS = [
    "manager", "managed", "managing", "lead", "led ", "led a team", "head of",
    "director", "supervised", "ניהל", "מנהל", "ניהלתי", "ראש צוות",
]

DEGREE_PATTERNS: dict[str, list[str]] = {
    "B.Sc": [r"b\.?\s?sc", r"bachelor of science"],
    "B.A": [r"b\.?\s?a\b", r"bachelor of arts"],
    "B.Eng": [r"b\.?\s?eng"],
    "Bachelor": [r"bachelor", r"תואר ראשון", r"בוגר"],
    "M.Sc": [r"m\.?\s?sc", r"master of science"],
    "M.A": [r"m\.?\s?a\b", r"master of arts"],
    "MBA": [r"mba"],
    "Master": [r"master", r"תואר שני", r"מוסמך"],
    "PhD": [r"ph\.?\s?d", r"doctorate", r"דוקטורט"],
    "Diploma": [r"diploma", r"דיפלומה"],
    "Certificate": [r"certificate", r"תעודה"],
}

# Common fields of study (extend as needed).
FIELDS_OF_STUDY = [
    "Computer Science", "Software Engineering", "Information Systems",
    "Information Technology", "Electrical Engineering", "Mechanical Engineering",
    "Industrial Engineering", "Civil Engineering", "Data Science", "Mathematics",
    "Statistics", "Physics", "Business Administration", "Economics", "Finance",
    "Accounting", "Marketing", "Communications", "Psychology", "Sociology",
    "Political Science", "Law", "Biology", "Chemistry", "Nursing", "Medicine",
    "Graphic Design", "Industrial Design", "Education", "Management",
    "Human Resources",
]

INSTITUTION_KEYWORDS = ["university", "college", "institute", "school of",
                        "academy", "אוניברסיט", "מכללה", "מכון"]


# ---------------------------------------------------------------------------
# Step 1: text extraction and normalization
# ---------------------------------------------------------------------------
def extract_text_from_pdf(cv_path: Path = CV_PATH) -> str:
    """Read all text from a PDF (tries pymupdf, pdfplumber, then pypdf)."""
    if not cv_path.exists():
        print(f"CV not found at {cv_path}")
        return ""

    text, _source = extract_text_from_resume(cv_path)
    return text


def normalize_text(text: str) -> str:
    """Collapse extra whitespace while keeping a line-per-entry structure."""
    # PDF text often contains ligatures (e.g. U+FB00) that break email regex and console output.
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = []
    for line in text.split("\n"):
        # Collapse runs of spaces/tabs and trim the ends.
        clean = re.sub(r"[ \t]+", " ", line).strip()
        lines.append(clean)

    # Collapse 3+ blank lines into a single blank line.
    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


# ---------------------------------------------------------------------------
# Step 2: section detection
# ---------------------------------------------------------------------------
def _match_heading(line: str) -> str | None:
    """If a line is a known section heading, return its canonical key."""
    # A heading is short and contains little besides the title itself.
    candidate = line.strip().strip(":").strip().lower()
    if not candidate or len(candidate) > 40:
        return None

    for key, headings in SECTION_HEADINGS.items():
        for heading in headings:
            # Exact match, or the line starts with the heading word(s).
            if candidate == heading or candidate.startswith(heading + " "):
                return key
    return None


def detect_sections(text: str) -> tuple[dict[str, str], str]:
    """Split resume text into sections.

    Returns (sections, header) where `header` is the text before the first
    detected heading (useful for name/contact extraction).
    """
    sections: dict[str, str] = {key: "" for key in SECTION_KEYS}

    lines = text.split("\n")
    header_lines: list[str] = []
    current: str | None = None
    buffers: dict[str, list[str]] = {key: [] for key in SECTION_KEYS}

    for line in lines:
        heading_key = _match_heading(line)
        if heading_key is not None:
            current = heading_key
            continue

        if current is None:
            header_lines.append(line)
        else:
            buffers[current].append(line)

    for key, collected in buffers.items():
        sections[key] = "\n".join(collected).strip()

    # Any header leftovers that are not contact info land in "other".
    header = "\n".join(header_lines).strip()
    return sections, header


# ---------------------------------------------------------------------------
# Step 3: contact extraction
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(\+?\d[\d\-\s().]{7,}\d)")
LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/[A-Za-z0-9_/\-]+", re.I)
GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_/\-]+", re.I)
URL_RE = re.compile(r"(?:https?://)?(?:www\.)?[A-Za-z0-9\-]+\.[A-Za-z]{2,}(?:/[^\s|]*)?", re.I)

LOCATION_HINTS = [
    "israel", "tel aviv", "jerusalem", "haifa", "beer sheva", "netanya",
    "rishon", "petah tikva", "ramat gan", "herzliya", "remote",
    "ישראל", "תל אביב", "ירושלים", "חיפה", "באר שבע", "נתניה", "רעננה",
    "כפר סבא", "פתח תקווה", "רמת גן", "הרצליה", "מהבית",
]


def _looks_like_name(line: str) -> bool:
    """A name line is short, has no digits/@, and is 1-4 words."""
    if not line or "@" in line or any(ch.isdigit() for ch in line):
        return False
    words = line.split()
    if not (1 <= len(words) <= 4):
        return False
    # Mostly letters (allow Hebrew, Latin, dots, hyphens).
    letters = sum(ch.isalpha() for ch in line)
    return letters >= max(2, len(line) - len(words) - 2)


def extract_contact(text: str, header: str) -> dict:
    """Pull name, email, phone, location and profile links from the resume."""
    contact = {
        "name": "", "email": "", "phone": "", "location": "",
        "linkedin": "", "github": "", "portfolio": "",
    }

    email = EMAIL_RE.search(text)
    if email:
        contact["email"] = email.group(0)

    # Phone: pick the first candidate with at least 9 digits.
    for candidate in PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", candidate)
        if 9 <= len(digits) <= 15:
            contact["phone"] = candidate.strip()
            break

    linkedin = LINKEDIN_RE.search(text)
    if linkedin:
        contact["linkedin"] = linkedin.group(0)

    github = GITHUB_RE.search(text)
    if github:
        contact["github"] = github.group(0)

    # Portfolio: a real URL (http/www) that is not linkedin/github. Emails are
    # removed first so their domains are not mistaken for a portfolio link.
    text_without_emails = EMAIL_RE.sub(" ", text)
    for url in URL_RE.findall(text_without_emails):
        low = url.lower()
        if "linkedin.com" in low or "github.com" in low:
            continue
        if not (low.startswith("http") or low.startswith("www.")):
            continue
        contact["portfolio"] = url
        break

    # Name: first plausible line from the header.
    for line in header.split("\n"):
        if _looks_like_name(line):
            contact["name"] = line.strip()
            break

    # Location: first hint found in the header (fallback to whole text).
    search_space = (header + "\n" + text).lower()
    for hint in LOCATION_HINTS:
        if hint in search_space:
            contact["location"] = hint.title() if hint.isascii() else hint
            break

    return contact


# ---------------------------------------------------------------------------
# Step 4: skills
# ---------------------------------------------------------------------------
def extract_skills(text: str) -> dict[str, list[str]]:
    """Detect skills across the whole resume, grouped by category."""
    # Scanning the full text (not just the skills section) catches skills that
    # are only mentioned inside experience or project descriptions.
    return detect_skills_by_category(text)


# ---------------------------------------------------------------------------
# Step 5: experience
# ---------------------------------------------------------------------------
def _parse_years(text: str) -> list[int]:
    """Return all 4-digit years (1900-current) mentioned in the text."""
    current_year = datetime.now().year
    years = []
    for match in re.findall(r"(?:19|20)\d{2}", text):
        year = int(match)
        if 1950 <= year <= current_year:
            years.append(year)
    return years


def _estimate_years_of_experience(experience_text: str) -> int | None:
    """Estimate total years from date ranges in the experience section.

    Tries dateutil for month-level precision, then falls back to a simple
    span between the earliest and latest year mentioned.
    """
    if not experience_text:
        return None

    current_year = datetime.now().year
    # Treat "present"/"current"/Hebrew "היום" as today for ranges.
    normalized = re.sub(r"present|current|now|today|היום|כיום", str(current_year),
                        experience_text, flags=re.I)

    # Collect month-year tokens (e.g. "Jul 2022") for precise parsing.
    date_tokens = re.findall(
        r"[A-Za-z]{3,9}\.?\s+(?:19|20)\d{2}", normalized
    )
    parsed_dates = []
    for token in date_tokens:
        try:
            parsed_dates.append(date_parser.parse(token, default=datetime(2000, 1, 1)))
        except (ValueError, OverflowError):
            continue

    if len(parsed_dates) >= 2:
        span_days = (max(parsed_dates) - min(parsed_dates)).days
        return max(0, round(span_days / 365.25))

    years = _parse_years(normalized)
    if len(years) >= 2:
        return max(0, max(years) - min(years))
    return None


def _has_keyword(text_l: str, keywords: list[str]) -> bool:
    return any(keyword in text_l for keyword in keywords)


def extract_experience(experience_text: str, full_text: str) -> dict:
    """Extract job titles, companies, seniority and experience flags."""
    text_l = full_text.lower()
    exp_l = experience_text.lower()

    job_titles: list[str] = []
    companies: list[str] = []

    for line in experience_text.split("\n"):
        clean = line.strip().lstrip("•-*").strip()
        if not clean:
            continue

        # Title / company usually live on lines with a "|" or " at " separator,
        # or a date range. Skip pure bullet descriptions.
        has_separator = "|" in clean or " at " in clean.lower()
        has_year = re.search(r"(?:19|20)\d{2}", clean) is not None
        if not (has_separator or has_year):
            continue

        # Company.
        company = ""
        if " at " in clean.lower():
            after = re.split(r"\s+at\s+", clean, flags=re.I)[1]
            company = re.split(r"[|,]", after)[0].strip()
            title_chunk = re.split(r"\s+at\s+", clean, flags=re.I)[0]
        elif "|" in clean:
            parts = [p.strip() for p in clean.split("|")]
            title_chunk = parts[0]
            if len(parts) >= 2:
                company = parts[1]
        else:
            title_chunk = clean

        # Title: drop a trailing "– Project" style suffix and any dates.
        title = re.split(r"\s[–-]\s", title_chunk)[0].strip()
        title = re.sub(r"\(?(?:19|20)\d{2}.*$", "", title).strip(" ,–-")

        if title and 2 <= len(title) <= 60 and title not in job_titles:
            job_titles.append(title)
        if company and len(company) <= 60:
            company = re.sub(r"\(?(?:19|20)\d{2}.*$", "", company).strip(" ,–-")
            if company and company not in companies:
                companies.append(company)

    management = _has_keyword(text_l, MANAGEMENT_SIGNALS)
    internship_or_student = (
        _has_keyword(text_l, INTERN_KEYWORDS)
        or _has_keyword(text_l, STUDENT_KEYWORDS)
    )

    years_estimate = _estimate_years_of_experience(experience_text)

    # Seniority: explicit signals first, then fall back to years of experience.
    if _has_keyword(text_l, INTERN_KEYWORDS):
        seniority = "intern"
    elif _has_keyword(text_l, STUDENT_KEYWORDS):
        seniority = "student"
    elif _has_keyword(exp_l, SENIOR_KEYWORDS):
        seniority = "senior"
    elif _has_keyword(exp_l, MANAGER_KEYWORDS):
        seniority = "manager"
    elif _has_keyword(exp_l, LEAD_KEYWORDS):
        seniority = "lead"
    elif _has_keyword(exp_l, JUNIOR_KEYWORDS):
        seniority = "junior"
    elif years_estimate is not None and years_estimate >= 6:
        seniority = "senior"
    elif years_estimate is not None and years_estimate >= 3:
        seniority = "mid"
    elif years_estimate is not None and years_estimate >= 1:
        seniority = "junior"
    else:
        seniority = "unknown"

    return {
        "job_titles": job_titles[:10],
        "companies": companies[:10],
        "years_of_experience_estimate": years_estimate,
        "seniority_level": seniority,
        "management_experience": management,
        "internship_or_student_experience": internship_or_student,
    }


# ---------------------------------------------------------------------------
# Step 6: education
# ---------------------------------------------------------------------------
def extract_education(education_text: str, full_text: str) -> dict:
    """Extract degrees, institutions and fields of study."""
    search_text = education_text or full_text
    search_l = search_text.lower()

    degrees: list[str] = []
    for canonical, patterns in DEGREE_PATTERNS.items():
        if any(re.search(pattern, search_l) for pattern in patterns):
            degrees.append(canonical)

    institutions: list[str] = []
    # English: "<Name> University/College/Institute".
    for match in re.findall(
        r"([A-Z][\w.&'\-]+(?:\s+[A-Z][\w.&'\-]+){0,3}\s+"
        r"(?:University|College|Institute|Academy))",
        search_text,
    ):
        name = match.strip()
        if name not in institutions:
            institutions.append(name)
    # Hebrew: "אוניברסיטת <Name>" / "מכללת <Name>".
    for match in re.findall(r"(אוניברסיט\S*\s+\S+(?:\s+\S+)?|מכלל\S*\s+\S+)", search_text):
        name = match.strip()
        if name not in institutions:
            institutions.append(name)

    fields: list[str] = []
    for field in FIELDS_OF_STUDY:
        if field.lower() in search_l and field not in fields:
            fields.append(field)

    return {
        "degrees": degrees,
        "institutions": institutions[:10],
        "fields_of_study": fields,
    }


# ---------------------------------------------------------------------------
# Step 7: projects & certifications (simple list extraction)
# ---------------------------------------------------------------------------
def extract_list_items(section_text: str, max_items: int = 10) -> list[str]:
    """Return likely 'title' lines from a section.

    Titles tend to be short, are not bullet/detail lines, and do not end with a
    period (which usually marks a wrapped description sentence).
    """
    items: list[str] = []
    for line in section_text.split("\n"):
        clean = line.strip()
        if not clean:
            continue
        # Skip bullet detail lines.
        if clean[0] in "•-*●◦":
            continue
        # Skip long lines and full sentences (likely descriptions, not titles).
        if len(clean) > 80 or len(clean.split()) > 8 or clean.endswith("."):
            continue
        if clean not in items:
            items.append(clean)
        if len(items) >= max_items:
            break
    return items


# ---------------------------------------------------------------------------
# Step 8: best-fit roles and strengths
# ---------------------------------------------------------------------------
def suggest_best_fit_roles(skills: dict[str, list[str]], experience: dict) -> list[str]:
    """Suggest up to 12 generic role families from skills + seniority + job titles."""
    def cat(name: str) -> set[str]:
        return set(skills.get(name, []))

    prog = cat("programming_languages")
    frameworks = cat("frameworks_libraries")
    databases = cat("databases")
    cloud = cat("cloud_devops_tools")
    data_ai = cat("data_ai")
    cyber = cat("cyber_security")
    design = cat("design_creative")
    marketing = cat("marketing_sales")
    finance = cat("finance_accounting")
    operations = cat("operations_logistics")
    hr = cat("hr_admin")
    healthcare = cat("healthcare")
    soft = cat("soft_skills")

    backend = frameworks & {"FastAPI", "Django", "Flask", "Laravel", "Spring",
                            "Express", "Node.js", "REST API"}
    frontend = frameworks & {"React", "Angular", "Vue", "Next.js", "Bootstrap"}
    it_support_signals = (
        cat("cloud_devops_tools") & {"Linux", "Windows"}
    ) | {s for s in cyber if s in {"Networking", "TCP/IP"}}

    # Past titles are strong evidence for secondary career tracks.
    job_titles = [str(t).strip() for t in (experience.get("job_titles") or []) if str(t).strip()]
    title_blob = " ".join(job_titles).casefold()

    # role -> signal strength (count of supporting evidence)
    scores: dict[str, int] = {}

    def add(role: str, strength: int) -> None:
        if strength > 0:
            scores[role] = scores.get(role, 0) + strength

    add("Software Developer", len(prog) + len(frameworks))
    add("Backend Developer", len(backend) + len(databases))
    if "Python" in prog:
        add("Python Developer", 2 + (1 if backend else 0))
    if "FastAPI" in frameworks:
        add("FastAPI Developer", 3)
    add("Frontend Developer", len(frontend))
    if backend and frontend:
        add("Full Stack Developer", len(backend) + len(frontend))
    add("Data Analyst", len(data_ai & {"Pandas", "NumPy", "Power BI", "Tableau",
                                        "Data Analysis", "Excel"}))
    add("Data Scientist", len(data_ai & {"Machine Learning", "Deep Learning",
                                         "LLM", "NLP", "Computer Vision",
                                         "Data Science"}))
    add("SOC Analyst", len(cyber))
    if cyber:
        add("Cybersecurity Analyst", len(cyber))
    add("DevOps Engineer", len(cloud & {"Docker", "Kubernetes", "CI/CD",
                                        "Terraform", "Jenkins", "AWS", "Azure"}))
    add("IT Support", len(it_support_signals))
    # Title/keyword boosts for support & cyber tracks that skills alone may miss.
    if any(token in title_blob for token in (
        "support", "help desk", "helpdesk", "service desk", "תמיכה", "טכני"
    )):
        add("Technical Support Specialist", 4)
        add("IT Support", 3)
    if any(token in title_blob for token in (
        "soc", "cyber", "security", "סייבר", "אבטחת"
    )):
        add("SOC Analyst", 3)
        add("Cybersecurity Analyst", 2)
    if any(token in title_blob for token in ("backend", "python", "fastapi", "צד שרת")):
        add("Backend Developer", 2)
        if "python" in title_blob or "Python" in prog:
            add("Python Developer", 2)
    add("UX/UI Designer", len(design & {"Figma", "UX", "UI"}))
    add("Graphic Designer", len(design & {"Photoshop", "Illustrator", "InDesign",
                                          "Canva", "Branding"}))
    add("Marketing Specialist", len(marketing & {"SEO", "PPC", "Google Ads",
                                                 "Meta Ads", "Email Marketing",
                                                 "Social Media", "Copywriting"}))
    add("Sales Representative", len(marketing & {"Sales", "CRM", "B2B", "B2C",
                                                "Lead Generation"}))
    add("Financial Analyst", len(finance))
    add("Operations Coordinator", len(operations))
    add("HR Coordinator", len(hr))
    add("Healthcare Assistant", len(healthcare))
    if healthcare:
        add("Physician", len(healthcare) + 2)
        add("Medical Consultant", len(healthcare) + 1)
        add("Surgeon", len(healthcare))
    add("Customer Support", len(soft & {"Customer Service"}))

    # Management roles when there is leadership/management evidence.
    if experience.get("management_experience"):
        add("Project Manager", 1)
        add("Product Manager", 1)

    # Prefer concrete past titles when they look like searchable roles.
    for title in job_titles[:6]:
        if 3 <= len(title) <= 50:
            add(title, 5)

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [role for role, _ in ranked][:12]


def generate_strengths(skills: dict[str, list[str]], experience: dict) -> list[str]:
    """Build short, human-readable strengths from the detected signals."""
    strengths: list[str] = []

    def has(category: str, names: set[str] | None = None) -> bool:
        items = set(skills.get(category, []))
        return bool(items & names) if names else bool(items)

    if has("frameworks_libraries", {"FastAPI", "Django", "Flask", "Express",
                                    "Node.js", "REST API"}):
        strengths.append("Backend API development")
    if has("frameworks_libraries", {"React", "Angular", "Vue", "Next.js"}):
        strengths.append("Frontend development")
    if has("cloud_devops_tools", {"AWS", "Azure", "GCP", "Docker", "Kubernetes",
                                  "CI/CD"}):
        strengths.append("Cloud deployment")
    if has("data_ai"):
        strengths.append("Data analysis")
    if has("cyber_security"):
        strengths.append("Cybersecurity / SOC monitoring")
    if has("design_creative"):
        strengths.append("Design and creative work")
    if has("marketing_sales", {"SEO", "PPC", "Google Ads", "Meta Ads",
                               "Email Marketing", "Social Media"}):
        strengths.append("Digital marketing")
    if has("marketing_sales", {"Sales", "CRM", "B2B", "B2C", "Lead Generation"}):
        strengths.append("Sales pipeline management")
    if has("finance_accounting"):
        strengths.append("Financial reporting")
    if has("operations_logistics"):
        strengths.append("Operations coordination")
    if has("hr_admin"):
        strengths.append("HR and administration")
    if has("healthcare"):
        strengths.append("Healthcare support")
    if has("soft_skills", {"Customer Service"}):
        strengths.append("Customer service")
    if experience.get("management_experience"):
        strengths.append("Team leadership")
    if has("soft_skills", {"Problem Solving"}):
        strengths.append("Problem solving")

    return strengths[:8]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def _print_cv_diagnosis(cv_path: Path) -> None:
    if cv_path.suffix.lower() != ".pdf":
        return
    issues = diagnose_pdf(cv_path)
    if issues:
        print("\nCV file diagnosis:")
        for issue in issues:
            print(f"  - {issue}")
        print(
            "\nPossible fixes:\n"
            "  1. Open the file in Word/Google Docs and re-save as PDF\n"
            "  2. Save as resumes/cv.docx or resumes/cv.txt\n"
            "  3. If it is scanned, save as resumes/cv.png or cv.jpg"
        )


def _try_vision_analysis(cv_path: Path, rule_based: dict) -> dict | None:
    """Use OpenAI Vision when local text extraction failed."""
    ext = cv_path.suffix.lower()
    if ext == ".pdf":
        image_pages = render_pdf_pages(cv_path)
    elif ext in IMAGE_EXTENSIONS:
        image_pages = load_image_bytes(cv_path)
    else:
        return None

    if not image_pages:
        return None
    if not page_images_have_content(image_pages):
        print("OpenAI Vision: the page looks blank - the file cannot be read.")
        return None

    print(f"OpenAI Vision: analyzing {len(image_pages)} image page(s)...")
    universal = extract_universal_profile_vision(image_pages, rule_based)
    merged = apply_universal_profile_to_cv(rule_based, universal)
    merged["parsed_with"] = "openai+vision"
    return refine_profile(merged)


def parse_resume(
    cv_path: Path | None = None,
    *,
    use_ai: bool = True,
) -> tuple[dict, str, str]:
    """Parse a resume file into the structured profile schema.

    Returns (profile, status, reason) where status is one of:
      - openai_ok      — OpenAI analysis succeeded
      - rules_only     — AI was not requested (--no-ai)
      - needs_confirm  — AI was requested but unavailable or failed; caller should ask user
    """
    resolved = find_resume_path(cv_path or CV_PATH)
    if resolved is None:
        print(f"No CV file found in {CV_PATH.parent}")
        empty = empty_profile()
        empty["parsed_with"] = "none"
        return empty, "needs_confirm", "No CV file found"

    if resolved != (cv_path or CV_PATH):
        print(f"Using resume file: {resolved}")

    if use_ai and not is_ai_available():
        print("OpenAI: API key is not set in .env")

    raw_text = normalize_text(extract_text_from_resume(resolved)[0])
    sections, header = detect_sections(raw_text)

    contact = extract_contact(raw_text, header)
    skills = extract_skills(raw_text)
    experience = extract_experience(sections.get("experience", ""), raw_text)
    education = extract_education(sections.get("education", ""), raw_text)
    projects = extract_list_items(sections.get("projects", ""))
    certifications = extract_list_items(sections.get("certifications", ""))
    best_fit_roles = suggest_best_fit_roles(skills, experience)
    strengths = generate_strengths(skills, experience)

    rule_based = {
        "raw_text": raw_text,
        "sections": sections,
        "contact": contact,
        "skills": skills,
        "experience": experience,
        "education": education,
        "projects": projects,
        "certifications": certifications,
        "best_fit_roles": best_fit_roles,
        "strengths": strengths,
        "char_count": len(raw_text),
    }

    if not use_ai:
        rule_based["parsed_with"] = "rules"
        rule_based["ai_insights"] = _empty_ai_insights()
        universal = build_universal_profile_fallback(rule_based)
        return refine_profile(apply_universal_profile_to_cv(rule_based, universal)), "rules_only", ""

    if not is_ai_available():
        rule_based["parsed_with"] = "rules"
        rule_based["ai_insights"] = _empty_ai_insights()
        universal = build_universal_profile_fallback(rule_based)
        return (
            refine_profile(apply_universal_profile_to_cv(rule_based, universal)),
            "needs_confirm",
            "OPENAI_API_KEY is not set in .env",
        )

    ai_failure_reason = ""

    try:
        if raw_text.strip():
            universal = extract_universal_profile(raw_text, rule_based, use_ai=True)
            merged = refine_profile(apply_universal_profile_to_cv(rule_based, universal))
            merged["parsed_with"] = "openai+rules"
            return merged, "openai_ok", ""
        vision_result = _try_vision_analysis(resolved, rule_based)
        if vision_result is not None:
            return refine_profile(vision_result), "openai_ok", ""
        ai_failure_reason = "Could not extract text from the file and Vision failed"
        _print_cv_diagnosis(resolved)
    except Exception as exc:
        ai_failure_reason = f"Universal profile extraction failed: {exc}"
        print(f"OpenAI analysis failed ({exc}).")

    rule_based["parsed_with"] = "rules"
    rule_based["ai_insights"] = _empty_ai_insights()
    universal = build_universal_profile_fallback(rule_based)
    return (
        refine_profile(apply_universal_profile_to_cv(rule_based, universal)),
        "needs_confirm",
        ai_failure_reason,
    )


def _empty_ai_insights() -> dict:
    return {
        "professional_summary": "",
        "key_achievements": [],
        "career_trajectory": "",
        "recommended_job_types": [],
        "skills_to_highlight": [],
        "potential_gaps": [],
        "improvement_suggestions": [],
    }


def save_json(data: dict, path: Path = CV_PROFILE_PATH) -> None:
    """Write the structured profile to disk as UTF-8 JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def empty_profile() -> dict:
    """A blank profile with the full schema (used when nothing is parsed yet)."""
    return {
        "raw_text": "",
        "sections": {key: "" for key in SECTION_KEYS},
        "contact": {
            "name": "", "email": "", "phone": "", "location": "",
            "linkedin": "", "github": "", "portfolio": "",
        },
        "skills": {category: [] for category in
                   detect_skills_by_category("").keys()},
        "experience": {
            "job_titles": [], "companies": [],
            "years_of_experience_estimate": None, "seniority_level": "",
            "management_experience": False,
            "internship_or_student_experience": False,
        },
        "education": {"degrees": [], "institutions": [], "fields_of_study": []},
        "projects": [],
        "certifications": [],
        "best_fit_roles": [],
        "strengths": [],
        "ai_insights": _empty_ai_insights(),
        "parsed_with": "none",
        "char_count": 0,
    }


def load_cv_profile(path: Path = CV_PROFILE_PATH) -> dict:
    """Load the saved CV profile, or a blank one if it does not exist yet."""
    if not path.exists():
        return empty_profile()
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _count_total_skills(skills: dict[str, list[str]]) -> int:
    return sum(len(items) for items in skills.values())


def main() -> None:
    configure_console()
    parser = argparse.ArgumentParser(description="Parse resume PDF into structured profile")
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Skip OpenAI analysis even if OPENAI_API_KEY is set",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Continue without OpenAI if it fails (non-interactive)",
    )
    args = parser.parse_args()

    use_ai = not args.no_ai
    safe_print("AI Job Agent — parsing resume")
    if use_ai and is_ai_available():
        safe_print("OpenAI analysis: enabled")
    elif use_ai:
        safe_print("OpenAI analysis: disabled (set OPENAI_API_KEY in .env to enable)")
    else:
        safe_print("OpenAI analysis: skipped (--no-ai)")

    profile, status, reason = parse_resume(use_ai=use_ai)

    if status == "needs_confirm":
        detail = f"\nReason: {reason}" if reason else ""
        if not (args.yes or ask_yes_no(
            f"OpenAI could not analyze the resume.{detail}\n"
            "Continue without OpenAI (local analysis only)?"
        )):
            safe_print("Stopped - no changes were saved.")
            sys.exit(1)

    save_json(profile)

    cv_file = find_resume_path(CV_PATH) or CV_PATH

    contact = profile["contact"]
    skills = profile["skills"]
    total_skills = _count_total_skills(skills)

    safe_print(f"\nCV file: {cv_file}")
    safe_print(f"Characters extracted: {profile['char_count']}")
    safe_print(f"Name:  {contact['name'] or '(not found)'}")
    safe_print(f"Email: {contact['email'] or '(not found)'}")
    safe_print(f"Phone: {contact['phone'] or '(not found)'}")
    safe_print(f"Location: {contact['location'] or '(not found)'}")

    safe_print(f"\nTotal skills found: {total_skills}")
    top_categories = sorted(
        ((cat, items) for cat, items in skills.items() if items),
        key=lambda pair: len(pair[1]),
        reverse=True,
    )[:5]
    if top_categories:
        safe_print("Top skill categories:")
        for category, items in top_categories:
            label = category.replace("_", " ").title()
            safe_print(f"  - {label}: {', '.join(items)}")

    safe_print(f"\nSeniority: {profile['experience']['seniority_level']}")
    safe_print(f"Best-fit roles: {', '.join(profile['best_fit_roles']) or '(none)'}")
    safe_print(f"Strengths: {', '.join(profile['strengths']) or '(none)'}")

    insights = profile.get("ai_insights") or {}
    if insights.get("professional_summary"):
        safe_print(f"\n--- AI analysis ({profile.get('parsed_with', 'unknown')}) ---")
        safe_print(f"Summary: {insights['professional_summary']}")
        if insights.get("key_achievements"):
            safe_print("Key achievements:")
            for item in insights["key_achievements"]:
                safe_print(f"  • {item}")
        if insights.get("skills_to_highlight"):
            safe_print(f"Highlight: {', '.join(insights['skills_to_highlight'])}")
        if insights.get("improvement_suggestions"):
            safe_print("Suggestions:")
            for item in insights["improvement_suggestions"]:
                safe_print(f"  • {item}")

    safe_print(f"\nSaved to: {CV_PROFILE_PATH}")

    if profile["char_count"] == 0:
        safe_print(
            "\nNote: no text was extracted. The PDF may be corrupted — re-export from"
            " Word/Google Docs, or save as resumes/cv.docx / cv.txt / cv.png"
        )


if __name__ == "__main__":
    main()
