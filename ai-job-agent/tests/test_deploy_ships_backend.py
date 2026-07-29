"""Guards against a deploy that reports success while running older backend code.

The EC2 deploy copies source into a long-lived container instead of rebuilding
the image. It used to copy a hand-picked list of two modules, so backend fixes
(including the tailored-CV renderer) stayed on master for days while the
workflow kept passing. These tests fail if that shape comes back, and if the
health payload the deploy verifies against stops carrying a revision.
"""

from __future__ import annotations

import re
from pathlib import Path

import build_info
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

# Modules the tailoring/rendering path needs at runtime. A deploy that ships
# only some of them leaves the container running a mix of old and new code.
TAILORING_MODULES = (
    "api_server.py",
    "match_tailor_prompt.py",
    "match_tailor_service.py",
    "pdf_generator_service.py",
    "tailor_cv_service.py",
)


@pytest.fixture(scope="module")
def workflow() -> str:
    assert DEPLOY_WORKFLOW.is_file(), f"missing {DEPLOY_WORKFLOW}"
    return DEPLOY_WORKFLOW.read_text(encoding="utf-8")


def test_deploy_ships_the_whole_backend_source_tree(workflow: str):
    inject = re.search(
        r"inject_backend\(\)\s*\{(.*?)\n\s{10}\}", workflow, re.DOTALL
    )
    assert inject is not None, "inject_backend() not found in the deploy workflow"
    body = inject.group(1)

    tar_lines = [line for line in body.splitlines() if "ra-backend-src.tgz" in line and "tar " in line]
    assert tar_lines, "inject_backend() no longer builds a backend archive"
    archive_cmd = tar_lines[0]

    assert re.search(r"\bsrc\b", archive_cmd), "the whole src/ tree must be archived"
    named_modules = re.findall(r"src/\w+\.py", body)
    assert not named_modules, (
        "the deploy must not cherry-pick backend modules "
        f"(found {named_modules}) — ship the whole src/ tree instead"
    )


def test_deploy_verifies_the_live_backend_revision(workflow: str):
    assert "BUILD_REVISION" in workflow, "the deploy must stamp the shipped revision"
    assert "backend_revision" in workflow, (
        "the deploy must read the live revision back from /api/health"
    )
    check = re.search(
        r'if \[ "\$\{LIVE_REVISION:-\}" != "\$BACKEND_REVISION" \];(.*?)fi',
        workflow,
        re.DOTALL,
    )
    assert check is not None, "no shipped-vs-live revision comparison in the deploy"
    assert "exit 1" in check.group(1), (
        "a container serving older code than we shipped must fail the deploy"
    )


def test_deploy_recovers_when_the_injected_backend_cannot_import(workflow: str):
    """Shipping the full tree is only safe if a bad injection rolls back."""
    assert "backend_imports_ok" in workflow
    assert "restore_backend" in workflow
    assert "install_backend_deps" in workflow


def test_tailoring_modules_live_in_the_shipped_tree():
    src = REPO_ROOT / "ai-job-agent" / "src"
    for module in TAILORING_MODULES:
        assert (src / module).is_file(), f"{module} is not under ai-job-agent/src"


def test_backend_revision_falls_back_to_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("BACKEND_REVISION", raising=False)
    monkeypatch.setattr(build_info, "REVISION_FILE", tmp_path / "BUILD_REVISION")
    assert build_info.backend_revision() == build_info.UNKNOWN_REVISION

    (tmp_path / "BUILD_REVISION").write_text("abc123\n", encoding="utf-8")
    assert build_info.backend_revision() == "abc123"

    monkeypatch.setenv("BACKEND_REVISION", "def456")
    assert build_info.backend_revision() == "def456"


def test_health_reports_what_code_is_running(monkeypatch: pytest.MonkeyPatch, db_path: Path):
    import api_server
    import db as db_mod

    monkeypatch.setattr(db_mod, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setattr(api_server.db, "REGISTRY_DB_PATH", db_path)
    monkeypatch.setenv("BACKEND_REVISION", "deadbeef")

    from conftest import authed_client

    with authed_client() as client:
        payload = client.get("/api/health").json()

    assert payload["backend_revision"] == "deadbeef"
    assert payload["tailor_prompt_version"] == build_info.MATCH_TAILOR_PROMPT_VERSION
