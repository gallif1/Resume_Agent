"""Regression: first tailor-CV on Postgres used to 500 after saving the file.

INSERT into cv_tailor_versions was missing from the PgConnection RETURNING-id
allowlist, so cursor.lastrowid was None and ``int(None)`` crashed — while the
markdown file was already written. The second click then hit the cache and
appeared to "work".
"""

from __future__ import annotations

import db


class _FakeMapping(dict):
    pass


class _FakeResult:
    def __init__(self, rows: list[dict], *, rowcount: int = 1):
        self.returns_rows = True
        self.rowcount = rowcount
        self._rows = rows

    def mappings(self):
        return [_FakeMapping(r) for r in self._rows]


class _FakeSAConn:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement, params=None):  # noqa: ANN001
        sql = getattr(statement, "text", str(statement))
        self.statements.append(sql)
        assert "RETURNING" in sql.upper(), f"expected RETURNING id, got: {sql}"
        return _FakeResult([{"id": 99}])

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_pg_insert_cv_tailor_versions_sets_lastrowid():
    sa = _FakeSAConn()
    conn = db._PgConnection(sa)
    cursor = conn.execute(
        """
        INSERT INTO cv_tailor_versions (
            cv_id, job_id, score_before, score_after, tailored_cv_path, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("cv-x", 7, 67, 80, "/tmp/x.md", "2026-01-01T00:00:00+00:00"),
    )
    assert cursor.lastrowid == 99
    assert int(cursor.lastrowid) == 99


def test_score_line_is_human_hebrew():
    import tailor_cv_service as svc

    line = svc._score_line_for_display(
        score=80,
        label="Good Match",
        score_before=67,
        initial_match_score=67,
    )
    assert "שיפרנו את ההתאמה למשרה מ־67 ל־80" in line
    assert "התאמה טובה" in line
    assert "ציון בסיס:" not in line
    assert "Good Match" not in line
    assert "/100" not in line
