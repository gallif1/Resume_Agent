#!/usr/bin/env python3
"""Expand skill_ontology.json with universal cross-industry competency mappings."""

from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "data" / "skill_ontology.json"

NEW_RELATIONSHIPS = [
    {
        "id": "lesson-teaching",
        "source": [
            "teaching students",
            "taught lessons",
            "delivered lessons",
            "tutored",
            "classroom instruction",
            "lesson delivery",
            "הוראה",
            "לימוד תלמידים",
        ],
        "target": "teaching and presentation",
        "relation": "task_to_transferable_skill",
        "confidence": 0.92,
        "also_implies": [
            "explaining complex information",
            "mentoring",
            "subject knowledge communication",
            "patience",
        ],
        "hedged_statement": {
            "en": "Experience teaching, presenting, and explaining complex information",
            "he": "ניסיון בהוראה, הצגה והסברת מידע מורכב",
        },
        "supported_domains": ["education", "training", "customer_service", "general"],
    },
    {
        "id": "patient-records",
        "source": [
            "patient records",
            "medical records",
            "appointment scheduling",
            "patient appointments",
            "EMR",
            "EHR",
        ],
        "target": "healthcare administration",
        "relation": "responsibility_to_job_function",
        "confidence": 0.9,
        "also_implies": ["documentation", "confidentiality", "accuracy", "compliance"],
        "hedged_statement": {
            "en": "Experience with healthcare administration, documentation, and confidentiality",
            "he": "ניסיון במנהל רפואי, תיעוד ושמירה על סודיות",
        },
        "supported_domains": ["healthcare", "administration"],
    },
    {
        "id": "cash-recon",
        "source": [
            "cash reconciliation",
            "reconciled cash",
            "cash drawer",
            "end-of-day balancing",
            "POS reconciliation",
        ],
        "target": "financial accuracy and cash management",
        "relation": "task_to_transferable_skill",
        "confidence": 0.91,
        "also_implies": ["attention to detail", "retail operations"],
        "hedged_statement": {
            "en": "Experience with cash management and financial accuracy",
            "he": "ניסיון בניהול מזומן ודיוק פיננסי",
        },
        "supported_domains": ["finance", "retail", "hospitality"],
    },
    {
        "id": "contract-review",
        "source": [
            "contract review",
            "reviewed contracts",
            "legal documents",
            "document review support",
        ],
        "target": "legal document review",
        "relation": "activity_to_demonstrated_experience",
        "confidence": 0.88,
        "also_implies": ["attention to detail", "compliance awareness"],
        "hedged_statement": {
            "en": "Experience supporting legal document review and contract administration",
            "he": "ניסיון בתמיכה בסקירת מסמכים משפטיים וניהול חוזים",
        },
        "supported_domains": ["legal", "administration"],
    },
    {
        "id": "route-logistics",
        "source": [
            "route planning",
            "delivery routes",
            "dispatch",
            "fleet coordination",
            "shipping logistics",
        ],
        "target": "logistics coordination",
        "relation": "responsibility_to_job_function",
        "confidence": 0.9,
        "also_implies": ["planning", "operational efficiency"],
        "hedged_statement": {
            "en": "Experience with logistics coordination and route/dispatch planning",
            "he": "ניסיון בתיאום לוגיסטיקה ותכנון מסלולים/שיגור",
        },
        "supported_domains": ["logistics", "operations", "manufacturing"],
    },
    {
        "id": "equipment-safety",
        "source": [
            "equipment inspection",
            "machinery operation",
            "operated machinery",
            "preventive maintenance",
            "safety checks",
            "quality inspection",
        ],
        "target": "equipment operation and safety procedures",
        "relation": "activity_to_demonstrated_experience",
        "confidence": 0.9,
        "also_implies": ["quality control", "preventive maintenance"],
        "hedged_statement": {
            "en": "Experience operating equipment with attention to safety and quality checks",
            "he": "ניסיון בהפעלת ציוד תוך הקפדה על בטיחות ובקרת איכות",
        },
        "supported_domains": ["manufacturing", "construction", "skilled_trades"],
    },
    {
        "id": "hr-onboarding",
        "source": [
            "employee onboarding",
            "HR onboarding",
            "new hire orientation",
            "recruitment support",
            "interview scheduling",
        ],
        "target": "human resources administration",
        "relation": "responsibility_to_job_function",
        "confidence": 0.89,
        "also_implies": ["training and knowledge transfer", "organizational coordination"],
        "hedged_statement": {
            "en": "Experience with HR administration, onboarding, and recruitment support",
            "he": "ניסיון במנהל משאבי אנוש, קליטת עובדים ותמיכה בגיוס",
        },
        "supported_domains": ["hr", "administration", "management"],
    },
    {
        "id": "bookkeeping",
        "source": [
            "bookkeeping",
            "accounts payable",
            "accounts receivable",
            "general ledger",
            "QuickBooks",
            "journal entries",
        ],
        "target": "accounting and bookkeeping",
        "relation": "tool_to_competency",
        "confidence": 0.92,
        "also_implies": ["financial accuracy", "documentation"],
        "hedged_statement": {
            "en": "Experience with accounting, bookkeeping, and financial record management",
            "he": "ניסיון בהנהלת חשבונות, רישום פיננסי וניהול רשומות",
        },
        "supported_domains": ["finance", "accounting", "administration"],
    },
    {
        "id": "hospitality-guest",
        "source": [
            "guest services",
            "hotel front desk",
            "check-in",
            "check-out",
            "hospitality",
            "restaurant service",
        ],
        "target": "hospitality and guest service",
        "relation": "responsibility_to_job_function",
        "confidence": 0.9,
        "also_implies": ["customer service", "communication"],
        "hedged_statement": {
            "en": "Experience in hospitality and guest service operations",
            "he": "ניסיון בתפעול אירוח ושירות אורחים",
        },
        "supported_domains": ["hospitality", "retail", "customer_service"],
    },
    {
        "id": "sales-targets",
        "source": [
            "sales targets",
            "quota",
            "closed deals",
            "upselling",
            "cross-selling",
            "lead generation",
            "cold calling",
        ],
        "target": "sales performance and customer acquisition",
        "relation": "responsibility_to_job_function",
        "confidence": 0.91,
        "also_implies": ["negotiation", "relationship building", "communication"],
        "hedged_statement": {
            "en": "Experience driving sales performance, customer acquisition, and relationship building",
            "he": "ניסיון בביצועי מכירות, רכישת לקוחות ובניית קשרים",
        },
        "supported_domains": ["sales", "retail", "marketing"],
    },
    {
        "id": "marketing-content",
        "source": [
            "content calendar",
            "copywriting",
            "email marketing",
            "SEO",
            "brand messaging",
            "campaign performance",
        ],
        "target": "marketing content and campaign execution",
        "relation": "responsibility_to_job_function",
        "confidence": 0.9,
        "also_implies": ["audience engagement", "performance tracking"],
        "hedged_statement": {
            "en": "Experience creating marketing content and executing campaigns with performance tracking",
            "he": "ניסיון ביצירת תוכן שיווקי וביצוע קמפיינים עם מעקב ביצועים",
        },
        "supported_domains": ["marketing", "sales"],
    },
    {
        "id": "office-admin",
        "source": [
            "office administration",
            "calendar management",
            "travel arrangements",
            "meeting coordination",
            "office supplies",
            "front office",
        ],
        "target": "office and administrative coordination",
        "relation": "responsibility_to_job_function",
        "confidence": 0.9,
        "also_implies": ["organization", "time management", "communication"],
        "hedged_statement": {
            "en": "Experience with office administration and operational coordination",
            "he": "ניסיון במנהל משרד ותיאום תפעולי",
        },
        "supported_domains": ["administration", "operations", "public_sector"],
    },
    {
        "id": "construction-site",
        "source": [
            "construction site",
            "blueprint reading",
            "site safety",
            "trades work",
            "carpentry",
            "electrical installation",
            "plumbing",
        ],
        "target": "skilled trades and site operations",
        "relation": "activity_to_demonstrated_experience",
        "confidence": 0.88,
        "also_implies": ["safety procedures", "quality workmanship"],
        "hedged_statement": {
            "en": "Experience in skilled trades and site operations with safety awareness",
            "he": "ניסיון במקצועות טכניים ותפעול אתר עם מודעות לבטיחות",
        },
        "supported_domains": ["construction", "skilled_trades", "manufacturing"],
    },
    {
        "id": "design-creative",
        "source": [
            "graphic design",
            "Adobe Photoshop",
            "Illustrator",
            "Figma",
            "UI design",
            "brand design",
            "layout design",
        ],
        "target": "design and visual communication",
        "relation": "tool_to_competency",
        "confidence": 0.9,
        "also_implies": ["creative problem solving", "attention to detail"],
        "hedged_statement": {
            "en": "Experience with design tools and visual communication",
            "he": "ניסיון בכלי עיצוב ותקשורת חזותית",
        },
        "supported_domains": ["design", "marketing"],
    },
    {
        "id": "public-sector-service",
        "source": [
            "public service",
            "citizen inquiries",
            "government office",
            "municipal",
            "case management",
            "benefits processing",
        ],
        "target": "public-sector service and case administration",
        "relation": "responsibility_to_job_function",
        "confidence": 0.88,
        "also_implies": ["documentation", "compliance", "customer service"],
        "hedged_statement": {
            "en": "Experience in public-sector service, case administration, and citizen support",
            "he": "ניסיון בשירות ציבורי, ניהול תיקים ותמיכה באזרחים",
        },
        "supported_domains": ["public_sector", "administration", "customer_service"],
    },
    {
        "id": "negotiation",
        "source": [
            "negotiated",
            "negotiation",
            "vendor negotiation",
            "contract negotiation",
            "price negotiation",
        ],
        "target": "negotiation and stakeholder management",
        "relation": "task_to_transferable_skill",
        "confidence": 0.88,
        "also_implies": ["communication", "relationship building"],
        "hedged_statement": {
            "en": "Experience with negotiation and stakeholder management",
            "he": "ניסיון במשא ומתן וניהול בעלי עניין",
        },
        "supported_domains": ["sales", "management", "procurement", "legal"],
    },
    {
        "id": "process-improvement",
        "source": [
            "process improvement",
            "streamlined processes",
            "SOP",
            "standard operating procedures",
            "workflow optimization",
            "lean",
            "kaizen",
        ],
        "target": "process improvement and operational efficiency",
        "relation": "responsibility_to_job_function",
        "confidence": 0.9,
        "also_implies": ["process ownership", "organizational skills"],
        "hedged_statement": {
            "en": "Experience improving processes and operational efficiency",
            "he": "ניסיון בשיפור תהליכים ויעילות תפעולית",
        },
        "supported_domains": ["operations", "manufacturing", "management", "administration"],
    },
    {
        "id": "he-education",
        "source": ["הוראה", "מורה", "שיעורים פרטיים", "הדרכת תלמידים"],
        "target": "teaching and presentation",
        "relation": "terminology_equivalence",
        "confidence": 0.9,
        "hedged_statement": {
            "en": "Teaching and instructional experience",
            "he": "ניסיון בהוראה והדרכה",
        },
        "supported_domains": ["education"],
    },
    {
        "id": "he-finance",
        "source": ["הנהלת חשבונות", "חשבוניות", "גבייה", "תזרים מזומנים"],
        "target": "accounting and bookkeeping",
        "relation": "terminology_equivalence",
        "confidence": 0.9,
        "hedged_statement": {
            "en": "Accounting and financial administration experience",
            "he": "ניסיון בהנהלת חשבונות ומנהל פיננסי",
        },
        "supported_domains": ["finance", "accounting"],
    },
    {
        "id": "he-ops",
        "source": ["תפעול", "לוגיסטיקה", "מלאי", "מחסן"],
        "target": "operations coordination",
        "relation": "terminology_equivalence",
        "confidence": 0.9,
        "hedged_statement": {
            "en": "Operations and logistics coordination experience",
            "he": "ניסיון בתיאום תפעול ולוגיסטיקה",
        },
        "supported_domains": ["operations", "logistics"],
    },
]


def main() -> None:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    existing_ids = {r.get("id") for r in data.get("relationships") or []}
    added = 0
    for rel in NEW_RELATIONSHIPS:
        if rel["id"] in existing_ids:
            continue
        data["relationships"].append(rel)
        existing_ids.add(rel["id"])
        added += 1
    data["version"] = 2
    data["description"] = (
        "Configurable universal competency ontology for Intelligent Resume Tailoring. "
        "Supports software, sales, marketing, finance, operations, healthcare, education, "
        "legal, logistics, manufacturing, hospitality, retail, construction, design, HR, "
        "public sector, and other professions. Add mappings here without changing generation logic."
    )
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Added {added} relationships; total={len(data['relationships'])}")


if __name__ == "__main__":
    main()
