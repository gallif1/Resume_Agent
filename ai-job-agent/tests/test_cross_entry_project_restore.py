"""Regression: project restore must not leak tech/bullets across entries."""

from __future__ import annotations

from intelligent_tailoring.structured_resume import assign_stable_ids, stamp_ids_on_resume
from intelligent_tailoring.structured_validation import _restore_full_source_bullets


def test_restore_full_source_bullets_matches_by_name_not_stale_index_id():
    """After reordering, stale project_{idx} ids must not pull the wrong bullets."""
    source_facts = assign_stable_ids(
        {
            "projects": [
                {
                    "name": "Capstone Project",
                    "description": "Backend",
                    "bullets": ["Designed FastAPI backend"],
                },
                {
                    "name": "Server Monitor",
                    "description": "Infrastructure monitoring",
                    "bullets": [
                        "Implemented FastAPI service with ThreadPoolExecutor",
                        "Deployed monitoring service to AWS",
                    ],
                    "technologies": ["FastAPI", "ThreadPoolExecutor", "AWS"],
                },
                {
                    "name": "Restaurant App",
                    "description": "Ordering application",
                    "bullets": [
                        "Built React Native mobile UI",
                        "Created FastAPI backend with SQLite and Firebase",
                    ],
                    "technologies": ["React Native", "SQLite", "Firebase"],
                },
            ]
        }
    )

    # Reordered vs source, with intentionally wrong index-based ids.
    tailored = {
        "projects": [
            {
                "name": "Capstone Project",
                "id": "project_0",
                "source_entry_id": "project_0",
                "description": "Backend",
                "bullets": ["Designed FastAPI backend"],
            },
            {
                "name": "Restaurant App",
                "id": "project_1",  # stale — this id belongs to Server Monitor
                "source_entry_id": "project_1",
                "description": "Ordering application",
                "bullets": ["Built React Native mobile UI"],
            },
            {
                "name": "Server Monitor",
                "id": "project_2",  # stale — this id belongs to Restaurant App
                "source_entry_id": "project_2",
                "description": "Infrastructure monitoring",
                "bullets": ["Implemented FastAPI service with ThreadPoolExecutor"],
            },
        ]
    }

    restored = _restore_full_source_bullets(tailored, source_facts=source_facts)
    by_name = {str(p.get("name")): p for p in restored["projects"]}

    restaurant = " ".join(by_name["Restaurant App"].get("bullets") or []).lower()
    server = " ".join(by_name["Server Monitor"].get("bullets") or []).lower()

    assert "threadpool" not in restaurant
    assert "aws" not in restaurant or "deployed monitoring" not in restaurant
    assert "react native" not in server
    assert "firebase" not in server
    assert "sqlite" not in server
    assert "react native" in restaurant
    assert "threadpool" in server

    # Ids should be corrected to the real source identities.
    assert by_name["Restaurant App"].get("source_entry_id") == "project_2"
    assert by_name["Server Monitor"].get("source_entry_id") == "project_1"


def test_stamp_ids_remaps_stale_project_ids_after_reorder():
    source_facts = assign_stable_ids(
        {
            "projects": [
                {"name": "Server Monitor", "bullets": ["A"]},
                {"name": "Restaurant App", "bullets": ["B"]},
            ]
        }
    )
    resume = {
        "projects": [
            {
                "name": "Restaurant App",
                "id": "project_0",  # wrong — belongs to Server Monitor
                "source_entry_id": "project_0",
                "bullets": ["B"],
            },
            {
                "name": "Server Monitor",
                "id": "project_1",
                "source_entry_id": "project_1",
                "bullets": ["A"],
            },
        ]
    }
    stamped = stamp_ids_on_resume(resume, source_facts=source_facts)
    by_name = {p["name"]: p for p in stamped["projects"]}
    assert by_name["Restaurant App"]["source_entry_id"] == "project_1"
    assert by_name["Server Monitor"]["source_entry_id"] == "project_0"
