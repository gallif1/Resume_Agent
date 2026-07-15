"""Multi-CV isolation tests.

These cover the guarantees from the multi-CV spec:
  * uploading multiple CVs
  * running the agent for one CV does not change another CV's data
  * deleting one CV does not delete another CV (or shared, still-used jobs)
  * the same job can carry different scores/statuses per CV
  * a job attaches only once per CV (no duplicate matches within a scan)
  * duplicate CV files are prevented unless uploaded as a new version
"""

from __future__ import annotations

import cv_service
import db
import pytest
from conftest import insert_job


def _match_fields(score: int, category: str = "backend") -> dict:
    return {
        "match_score": score,
        "match_reason": "reason",
        "match_method": "local",
        "match_category": category,
        "matched_skills": '["python"]',
        "missing_skills": '["docker"]',
        "candidate_strategy_hash": "hash-v1",
    }


# --- Multiple CV upload -----------------------------------------------------


def test_upload_multiple_cvs(db_path, cvs_dir):
    cv_a = cv_service.upload_cv("alice.pdf", b"alice-resume-bytes", db_path=db_path)
    cv_b = cv_service.upload_cv("bob.pdf", b"bob-resume-bytes", db_path=db_path)

    assert cv_a["id"] != cv_b["id"]
    all_cvs = db.list_cvs(db_path=db_path)
    assert len(all_cvs) == 2

    # Each CV stored its own file under its own directory.
    assert (cvs_dir / cv_a["id"] / "resume.pdf").exists()
    assert (cvs_dir / cv_b["id"] / "resume.pdf").exists()


def test_duplicate_cv_rejected_unless_new_version(db_path, cvs_dir):
    data = b"identical-resume"
    cv_service.upload_cv("cv.pdf", data, db_path=db_path)

    with pytest.raises(cv_service.DuplicateCvError):
        cv_service.upload_cv("cv-copy.pdf", data, db_path=db_path)

    # Explicit override creates a separate version.
    versioned = cv_service.upload_cv(
        "cv-copy.pdf", data, as_new_version=True, db_path=db_path
    )
    assert versioned["id"]
    assert len(db.list_cvs(db_path=db_path)) == 2


# --- Running a scan for one CV does not touch another -----------------------


def test_scan_for_one_cv_does_not_change_another(db_path):
    cv_a = db.create_cv("cv-a", file_name="a.pdf", stored_path="a", db_path=db_path)
    cv_b = db.create_cv("cv-b", file_name="b.pdf", stored_path="b", db_path=db_path)
    job = insert_job(db_path, title="Backend Dev", url="https://x/1")

    # CV B is scanned first and gets a match.
    scan_b = db.create_scan(cv_b["id"], db_path=db_path)
    db.upsert_cv_job_match(cv_b["id"], job, _match_fields(72), scan_id=scan_b, db_path=db_path)
    db.update_cv_match_status(
        cv_b["id"],
        db.get_cv_job_match(cv_b["id"], job, db_path=db_path)["id"],
        db.CV_APP_SENT,
        db_path=db_path,
    )
    b_before = db.get_cv_job_match(cv_b["id"], job, db_path=db_path)

    # Now CV A is scanned for the same job with a different score.
    scan_a = db.create_scan(cv_a["id"], db_path=db_path)
    db.upsert_cv_job_match(cv_a["id"], job, _match_fields(40), scan_id=scan_a, db_path=db_path)

    b_after = db.get_cv_job_match(cv_b["id"], job, db_path=db_path)
    assert b_after["match_score"] == b_before["match_score"] == 72
    assert b_after["application_status"] == db.CV_APP_SENT
    assert db.get_cv_job_match(cv_a["id"], job, db_path=db_path)["match_score"] == 40


# --- Same job, different scores/statuses per CV -----------------------------


def test_same_job_different_scores_and_statuses(db_path):
    db.create_cv("cv-a", file_name="a.pdf", stored_path="a", db_path=db_path)
    db.create_cv("cv-b", file_name="b.pdf", stored_path="b", db_path=db_path)
    job = insert_job(db_path, title="Dev", url="https://x/2")

    db.upsert_cv_job_match("cv-a", job, _match_fields(90), db_path=db_path)
    db.upsert_cv_job_match("cv-b", job, _match_fields(50), db_path=db_path)

    match_a = db.get_cv_job_match("cv-a", job, db_path=db_path)
    match_b = db.get_cv_job_match("cv-b", job, db_path=db_path)

    db.update_cv_match_status("cv-a", match_a["id"], db.CV_APP_SENT, db_path=db_path)
    db.update_cv_match_status("cv-b", match_b["id"], db.CV_APP_NOT_SENT, db_path=db_path)

    match_a = db.get_cv_job_match("cv-a", job, db_path=db_path)
    match_b = db.get_cv_job_match("cv-b", job, db_path=db_path)
    assert (match_a["match_score"], match_a["application_status"]) == (90, db.CV_APP_SENT)
    assert (match_b["match_score"], match_b["application_status"]) == (50, db.CV_APP_NOT_SENT)


# --- Duplicate jobs within one scan attach only once ------------------------


def test_job_attaches_only_once_per_cv(db_path):
    db.create_cv("cv-a", file_name="a.pdf", stored_path="a", db_path=db_path)
    job = insert_job(db_path, title="Dev", url="https://x/3")

    # The same job discovered through several categories in one scan.
    db.upsert_cv_job_match("cv-a", job, _match_fields(60), db_path=db_path)
    db.upsert_cv_job_match("cv-a", job, _match_fields(80), db_path=db_path)

    matches = db.get_cv_matches("cv-a", db_path=db_path)
    assert len(matches) == 1
    assert matches[0]["match_score"] == 80  # latest upsert wins


def test_status_preserved_across_rescan(db_path):
    db.create_cv("cv-a", file_name="a.pdf", stored_path="a", db_path=db_path)
    job = insert_job(db_path, title="Dev", url="https://x/4")

    match_id = db.upsert_cv_job_match("cv-a", job, _match_fields(60), db_path=db_path)
    db.update_cv_match_status("cv-a", match_id, db.CV_APP_SENT, db_path=db_path)

    # Re-scan updates the score but must keep the user's application status.
    db.upsert_cv_job_match("cv-a", job, _match_fields(85), db_path=db_path)
    match = db.get_cv_job_match("cv-a", job, db_path=db_path)
    assert match["match_score"] == 85
    assert match["application_status"] == db.CV_APP_SENT


# --- Deleting one CV does not delete another --------------------------------


def test_delete_cv_keeps_other_cv_and_shared_jobs(db_path, cvs_dir):
    cv_a = cv_service.upload_cv("a.pdf", b"aaa", db_path=db_path)
    cv_b = cv_service.upload_cv("b.pdf", b"bbb", db_path=db_path)

    shared_job = insert_job(db_path, title="Shared", url="https://x/shared")
    only_a_job = insert_job(db_path, title="OnlyA", url="https://x/onlya")

    db.upsert_cv_job_match(cv_a["id"], shared_job, _match_fields(70), db_path=db_path)
    db.upsert_cv_job_match(cv_b["id"], shared_job, _match_fields(65), db_path=db_path)
    db.upsert_cv_job_match(cv_a["id"], only_a_job, _match_fields(55), db_path=db_path)

    summary = cv_service.delete_cv(cv_a["id"], db_path=db_path)

    # CV A gone, CV B intact.
    assert db.get_cv(cv_a["id"], db_path=db_path) is None
    assert db.get_cv(cv_b["id"], db_path=db_path) is not None
    assert not (cvs_dir / cv_a["id"]).exists()
    assert (cvs_dir / cv_b["id"]).exists()

    # CV B still has its match on the shared job.
    assert db.get_cv_job_match(cv_b["id"], shared_job, db_path=db_path) is not None

    # Shared job kept (still referenced by B); the A-only job removed.
    all_jobs = {j["id"] for j in db.get_all_jobs(db_path=db_path)}
    assert shared_job in all_jobs
    assert only_a_job not in all_jobs
    assert summary["deleted_jobs"] == 1
    assert only_a_job in summary["orphaned_job_ids"]


def test_delete_cv_removes_scans_and_matches(db_path):
    db.create_cv("cv-a", file_name="a.pdf", stored_path="a", db_path=db_path)
    job = insert_job(db_path, title="Dev", url="https://x/5")
    scan = db.create_scan("cv-a", db_path=db_path)
    db.upsert_cv_job_match("cv-a", job, _match_fields(70), scan_id=scan, db_path=db_path)

    summary = db.delete_cv("cv-a", db_path=db_path)
    assert summary["deleted_matches"] == 1
    assert summary["deleted_scans"] == 1
    assert db.list_scans("cv-a", db_path=db_path) == []
    assert db.get_cv_matches("cv-a", db_path=db_path) == []


# --- Latest-scan filtering + sorting ----------------------------------------


def test_matches_latest_scan_only_and_sorted(db_path):
    db.create_cv("cv-a", file_name="a.pdf", stored_path="a", db_path=db_path)
    job1 = insert_job(db_path, title="J1", url="https://x/j1")
    job2 = insert_job(db_path, title="J2", url="https://x/j2")

    old_scan = db.create_scan("cv-a", db_path=db_path)
    db.upsert_cv_job_match("cv-a", job1, _match_fields(30), scan_id=old_scan, db_path=db_path)

    new_scan = db.create_scan("cv-a", db_path=db_path)
    db.upsert_cv_job_match("cv-a", job1, _match_fields(60), scan_id=new_scan, db_path=db_path)
    db.upsert_cv_job_match("cv-a", job2, _match_fields(90), scan_id=new_scan, db_path=db_path)

    latest = db.get_cv_matches("cv-a", latest_only=True, db_path=db_path)
    assert [m["job_id"] for m in latest] == [job2, job1]  # sorted by score desc
    assert all(m["scan_id"] == new_scan for m in latest)


def test_refresh_cv_job_match_scan_keeps_prior_score_on_new_scan(db_path):
    """Already-matched jobs must reappear on the latest scan without rescoring."""
    db.create_cv("cv-a", file_name="a.pdf", stored_path="a", db_path=db_path)
    job = insert_job(db_path, title="Kept", url="https://x/kept")
    old_scan = db.create_scan("cv-a", db_path=db_path)
    db.upsert_cv_job_match("cv-a", job, _match_fields(72), scan_id=old_scan, db_path=db_path)

    new_scan = db.create_scan("cv-a", db_path=db_path)
    assert db.refresh_cv_job_match_scan("cv-a", job, new_scan, db_path=db_path)

    latest = db.get_cv_matches("cv-a", latest_only=True, db_path=db_path)
    assert len(latest) == 1
    assert latest[0]["scan_id"] == new_scan
    assert latest[0]["match_score"] == 72


def test_invalid_status_rejected(db_path):
    db.create_cv("cv-a", file_name="a.pdf", stored_path="a", db_path=db_path)
    job = insert_job(db_path, title="Dev", url="https://x/6")
    match_id = db.upsert_cv_job_match("cv-a", job, _match_fields(70), db_path=db_path)
    with pytest.raises(ValueError):
        db.update_cv_match_status("cv-a", match_id, "bogus_status", db_path=db_path)
