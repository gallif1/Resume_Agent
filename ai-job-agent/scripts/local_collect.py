#!/usr/bin/env python3
"""Collect jobs on your PC and upload results to a Resume Agent server.

The web UI cannot scrape job boards directly (browser security / CORS).
Run this script on your computer — it uses your network and optional local
Playwright, then uploads jobs to the cloud server for matching.

Example:
  python scripts/local_collect.py \\
    --cv-id abc123 \\
    --api-url https://resume-agent-xxxx.onrender.com \\
    --sites drushim,linkedin
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC))

from local_agent import export_jobs_for_upload, write_local_agent_bundle  # noqa: E402

PYTHON = sys.executable


def _api_json(method: str, url: str, payload: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def _run_step(script: str, cv_id: str, extra: list[str] | None = None) -> None:
    env = {**os.environ, "AGENT_CV_ID": cv_id}
    cmd = [PYTHON, str(SRC / script), *(extra or [])]
    print(f"\n>> Running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{script} failed with exit code {proc.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local job collection for Resume Agent")
    parser.add_argument("--cv-id", required=True, help="CV id from the web UI")
    parser.add_argument("--api-url", required=True, help="Server base URL (no trailing slash)")
    parser.add_argument(
        "--sites",
        default="drushim,linkedin,gotfriends",
        help="Comma-separated job boards",
    )
    parser.add_argument(
        "--skip-enrich",
        action="store_true",
        help="Skip fetching full job descriptions (faster)",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Only download strategy bundle (no collection)",
    )
    args = parser.parse_args()

    base = args.api_url.rstrip("/")
    cv_id = args.cv_id.strip()

    print(f"Downloading local agent bundle for CV {cv_id}...")
    bundle = _api_json("GET", f"{base}/cvs/{cv_id}/local-agent-bundle")
    write_local_agent_bundle(cv_id, bundle)
    print(f"Saved strategy to {PROJECT_ROOT / 'data' / 'cvs' / cv_id}")

    if args.prepare_only:
        print("Prepare-only mode — done.")
        return

    _run_step("collect_jobs.py", cv_id, extra=["--sites", args.sites])
    if not args.skip_enrich:
        _run_step("enrich_jobs.py", cv_id)

    jobs = export_jobs_for_upload(cv_id)
    if not jobs:
        print("No jobs collected — nothing to upload.")
        sys.exit(1)

    print(f"\nUploading {len(jobs)} job(s) to server...")
    result = _api_json(
        "POST",
        f"{base}/cvs/{cv_id}/jobs/ingest",
        {"jobs": jobs, "reset_pool": True},
    )
    print(f"Upload summary: {result}")

    print("Starting match scoring on server...")
    _api_json("POST", f"{base}/cvs/{cv_id}/complete-local-scan")
    print("Done. Refresh the web UI to see matches.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
