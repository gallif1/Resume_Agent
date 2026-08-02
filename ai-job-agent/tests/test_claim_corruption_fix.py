"""Regression tests for token-deletion corruption and claim-level repair."""

from __future__ import annotations

from intelligent_tailoring.linguistic_integrity import (
    detect_broken_patterns,
    validate_resume_linguistics,
)
from intelligent_tailoring.safe_claim_rewriter import rebuild_claim_from_facts
from intelligent_tailoring.scope_validator import (
    strip_leaked_tech_from_bullet,
    validate_resume_tech_scope,
)
from intelligent_tailoring.skill_taxonomy import (
    assert_skill_classification_examples,
    categorize_skill,
    normalize_skill_lines,
)
from intelligent_tailoring.summary_builder import (
    build_professional_summary,
    summary_passes_checks,
)


CAPSTONE_FACTS = [
    {
        "source_entry_id": "project_0",
        "source_section": "projects",
        "original_text": "Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL",
        "explicit_skills": ["FastAPI", "SQLAlchemy", "PostgreSQL"],
        "context": "Capstone Project",
    },
    {
        "source_entry_id": "project_0",
        "source_section": "projects",
        "original_text": "Deployed to AWS EC2, RDS and S3 with basic CI/CD",
        "explicit_skills": ["AWS", "EC2", "RDS", "S3", "CI/CD"],
        "context": "Capstone Project",
    },
    {
        "source_entry_id": "project_0",
        "source_section": "projects",
        "original_text": "Added pytest integration testing and reusable testing utilities",
        "explicit_skills": ["pytest"],
        "context": "Capstone Project",
    },
    {
        "source_entry_id": "skill_node",
        "source_section": "skills",
        "original_text": "Node.js",
        "explicit_skills": ["Node.js"],
    },
    {
        "source_entry_id": "skill_docker",
        "source_section": "skills",
        "original_text": "Docker",
        "explicit_skills": ["Docker"],
    },
]

CAPSTONE_BULLETS = [
    "Designed backend architecture using FastAPI, SQLAlchemy and PostgreSQL",
    "Deployed to AWS EC2, RDS and S3 with basic CI/CD",
    "Added pytest integration testing and reusable testing utilities",
    "Integrated WebSockets for real-time updates",
]

ENTRY_TEXT = " ".join(CAPSTONE_BULLETS) + " FastAPI SQLAlchemy PostgreSQL AWS EC2 RDS S3 pytest"


class TestNoTokenDeletion:
    def test_strip_leaked_is_noop(self):
        original = "Designed backend architecture using Docker, FastAPI and PostgreSQL."
        result = strip_leaked_tech_from_bullet(original, {"docker"}, replacement_tech=None)
        assert result == original.strip()
        assert "using and" not in result

    def test_unsupported_tech_safe_rewrite(self):
        claim = rebuild_claim_from_facts(
            original_claim=(
                "Designed backend architecture using Docker, FastAPI and PostgreSQL."
            ),
            source_entry_id="project_0",
            facts=CAPSTONE_FACTS,
            entry_source_text=ENTRY_TEXT,
            original_bullets=CAPSTONE_BULLETS,
            section="projects",
            claim_id="t1",
        )
        assert claim.validation_status in ("safely_rewritten", "accepted")
        assert claim.final_text
        assert "docker" not in claim.final_text.lower()
        assert "fastapi" in claim.final_text.lower()
        assert "postgresql" in claim.final_text.lower()
        assert "sqlalchemy" in claim.final_text.lower()
        assert "using and" not in claim.final_text.lower()
        assert not detect_broken_patterns(claim.final_text)

    def test_aws_parent_entity_rewrite(self):
        claim = rebuild_claim_from_facts(
            original_claim="Deployed backend infrastructure using (EC2, RDS, S3).",
            source_entry_id="project_0",
            facts=CAPSTONE_FACTS,
            entry_source_text=ENTRY_TEXT,
            original_bullets=CAPSTONE_BULLETS,
            section="projects",
            claim_id="t2",
        )
        assert claim.final_text
        low = claim.final_text.lower()
        assert "using (" not in low
        assert "ec2" in low and "rds" in low and "s3" in low
        assert "aws" in low
        assert not detect_broken_patterns(claim.final_text)

    def test_testing_framework_recovery(self):
        claim = rebuild_claim_from_facts(
            original_claim=(
                "Implemented automated testing using Jest, including integration "
                "tests and reusable testing utilities."
            ),
            source_entry_id="project_0",
            facts=CAPSTONE_FACTS,
            entry_source_text=ENTRY_TEXT,
            original_bullets=CAPSTONE_BULLETS,
            section="projects",
            claim_id="t3",
        )
        assert claim.final_text
        low = claim.final_text.lower()
        assert "using," not in low
        assert "using and" not in low
        assert "pytest" in low or "integration" in low
        assert not detect_broken_patterns(claim.final_text)

    def test_sqlite_firebase_recovery(self):
        facts = [
            {
                "source_entry_id": "project_2",
                "source_section": "projects",
                "original_text": "Created FastAPI backend with SQLite and Firebase",
                "explicit_skills": ["FastAPI", "SQLite", "Firebase"],
                "context": "Restaurant App",
            }
        ]
        bullets = [
            "Built React Native mobile UI",
            "Created FastAPI backend with SQLite and Firebase",
            "Implemented offline storage with SQLite and synchronized orders to Firebase",
        ]
        claim = rebuild_claim_from_facts(
            original_claim=(
                "Implemented offline storage with Docker and synchronized orders to Kafka."
            ),
            source_entry_id="project_2",
            facts=facts,
            entry_source_text=" ".join(bullets),
            original_bullets=bullets,
            section="projects",
            claim_id="t4",
        )
        assert claim.final_text
        low = claim.final_text.lower()
        assert "with and" not in low
        assert "to." not in low
        assert "sqlite" in low
        assert "firebase" in low
        assert not detect_broken_patterns(claim.final_text)


class TestSummaryCorruption:
    def test_rejects_duplicated_keyword_soup(self):
        bad = (
            "Professional with Knowledge Dockers Web experience Professional with "
            "Knowledge Dockers Web experience."
        )
        ok, errors = summary_passes_checks(
            bad, resume_text="Docker FastAPI React Python experience"
        )
        assert ok is False
        assert errors

    def test_structured_summary_is_cohesive(self):
        result = build_professional_summary(
            strategy={
                "honest_title": "Full Stack Developer",
                "skills_to_emphasize": ["FastAPI", "React", "PostgreSQL", "AWS"],
            },
            resume_facts={
                "skills": ["FastAPI", "React", "PostgreSQL", "AWS"],
                "projects": [
                    {
                        "name": "Capstone",
                        "bullets": [
                            "Designed backend architecture using FastAPI and PostgreSQL"
                        ],
                    }
                ],
            },
            resume_text=(
                "Full Stack Developer. FastAPI React PostgreSQL AWS. "
                "Designed backend architecture using FastAPI and PostgreSQL."
            ),
            existing_summary=bad_summary() if False else (
                "Professional with Knowledge Dockers Web experience "
                "Professional with Knowledge Dockers Web experience."
            ),
        )
        summary = result["summary"]
        assert summary
        assert "knowledge dockers" not in summary.lower()
        assert summary.lower().count("professional with") <= 1
        ok, errors = summary_passes_checks(
            summary,
            resume_text="FastAPI React PostgreSQL AWS Designed backend",
        )
        assert ok, errors


class TestSkillClassification:
    def test_expected_categories(self):
        got = assert_skill_classification_examples()
        assert got["Laravel"] == "Backend"
        assert got["REST API"] == "Backend"
        assert got["Git"] == "Version Control"
        assert got["React"] == "Frontend"
        assert got["Angular"] == "Frontend"
        assert got["HTML"] == "Frontend"
        assert got["CSS"] == "Frontend"
        assert got["FastAPI"] == "Backend"
        assert got["PostgreSQL"] == "Databases"
        assert categorize_skill("Python") == "Languages"

    def test_normalize_skill_lines(self):
        lines = normalize_skill_lines(
            [
                "Languages: Python, Laravel, REST API",
                "Cloud & Infrastructure: Git, AWS",
                "Backend: React",
                "Tools: Angular, HTML, CSS",
            ]
        )
        joined = "\n".join(lines)
        # Laravel / REST not under Languages
        lang_line = next(l for l in lines if l.startswith("Languages:"))
        assert "Laravel" not in lang_line
        assert "REST" not in lang_line
        assert "Python" in lang_line
        assert "Frontend:" in joined
        assert "React" in joined and "Angular" in joined
        assert "HTML" in joined and "CSS" in joined
        tools = next((l for l in lines if l.startswith("Tools")), "")
        assert "Git" in joined
        assert "Git" not in (next(l for l in lines if "Cloud" in l) if any("Cloud" in l for l in lines) else "")


class TestCrossEntryNoLeakage:
    def test_nodejs_not_attached_to_fastapi_project(self):
        claim = rebuild_claim_from_facts(
            original_claim="Built the capstone backend with Node.js and PostgreSQL.",
            source_entry_id="project_0",
            facts=CAPSTONE_FACTS,
            entry_source_text=ENTRY_TEXT,
            original_bullets=CAPSTONE_BULLETS,
            section="projects",
            claim_id="t7",
        )
        assert "node" not in claim.final_text.lower()
        if claim.final_text:
            assert "fastapi" in claim.final_text.lower() or "postgresql" in claim.final_text.lower()


class TestNonTechnicalRepair:
    def test_customer_service_no_invented_metric(self):
        facts = [
            {
                "source_entry_id": "role_0",
                "source_section": "experience",
                "original_text": "Handled customer complaints by phone",
                "explicit_skills": [],
            }
        ]
        bullets = ["Handled customer complaints by phone"]
        claim = rebuild_claim_from_facts(
            original_claim="Increased retention by 25% through complaint handling.",
            source_entry_id="role_0",
            facts=facts,
            entry_source_text=bullets[0],
            original_bullets=bullets,
            section="experience",
            claim_id="nt1",
        )
        assert "25%" not in claim.final_text
        assert "increased" not in claim.final_text.lower()
        assert claim.final_text  # restored original
        assert "complaint" in claim.final_text.lower()

    def test_admin_scheduling_not_team_management(self):
        facts = [
            {
                "source_entry_id": "role_0",
                "source_section": "experience",
                "original_text": "Scheduled patient appointments",
                "explicit_skills": [],
            }
        ]
        bullets = ["Scheduled patient appointments"]
        claim = rebuild_claim_from_facts(
            original_claim="Managed a clinical team and optimized patient outcomes.",
            source_entry_id="role_0",
            facts=facts,
            entry_source_text=bullets[0],
            original_bullets=bullets,
            section="experience",
            claim_id="nt2",
        )
        assert "clinical team" not in claim.final_text.lower()
        assert "optimized" not in claim.final_text.lower()
        assert "appointment" in claim.final_text.lower()

    def test_ops_excel_not_powerbi(self):
        facts = [
            {
                "source_entry_id": "role_0",
                "source_section": "experience",
                "original_text": "Used Excel to prepare monthly reports",
                "explicit_skills": ["Excel"],
            }
        ]
        bullets = ["Used Excel to prepare monthly reports"]
        claim = rebuild_claim_from_facts(
            original_claim="Automated financial forecasting with Power BI.",
            source_entry_id="role_0",
            facts=facts,
            entry_source_text=bullets[0],
            original_bullets=bullets,
            section="experience",
            claim_id="nt3",
        )
        assert "power" not in claim.final_text.lower()
        assert "excel" in claim.final_text.lower()


class TestScopeValidatorNoCorruption:
    def test_resume_scope_never_produces_using_and(self):
        resume = {
            "professional_summary": "Full Stack developer with FastAPI and React.",
            "skills": ["Frontend: React", "Backend: FastAPI, Node.js", "Cloud: AWS"],
            "experience": [],
            "projects": [
                {
                    "name": "Capstone Project",
                    "description": "Built with Node.js improving user engagement",
                    "bullets": [
                        "Designed backend architecture using Docker, FastAPI and PostgreSQL.",
                        "Deployed backend infrastructure using (EC2, RDS, S3).",
                        "Implemented automated testing using Jest, including integration tests.",
                    ],
                }
            ],
        }
        result = validate_resume_tech_scope(
            resume,
            facts=CAPSTONE_FACTS,
            original_projects=[
                {
                    "name": "Capstone Project",
                    "technologies": [
                        "FastAPI",
                        "SQLAlchemy",
                        "PostgreSQL",
                        "AWS",
                        "EC2",
                        "RDS",
                        "S3",
                        "pytest",
                    ],
                    "bullets": CAPSTONE_BULLETS,
                    "description": "Full-stack backend architecture",
                }
            ],
        )
        cleaned = result["cleaned_resume"]
        blob = str(cleaned).lower()
        for bad in (
            "using and",
            "using,",
            "using (",
            "with and",
            "to.",
            "using .",
        ):
            assert bad not in blob, bad
        ling = validate_resume_linguistics(cleaned)
        # Summary may be blanked for rebuild; bullets must pass
        for entry in cleaned.get("projects") or []:
            for b in entry.get("bullets") or []:
                assert not detect_broken_patterns(str(b)), b


def bad_summary() -> str:
    return "x"
