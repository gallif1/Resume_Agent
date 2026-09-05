"""CLI entry point for standalone job apply automation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from job_apply.engine import apply_to_job
from job_apply.models import Applicant, ApplyRequest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-apply",
        description=(
            "Fill a job application form from a URL + CV + contact details, "
            "then click Submit. Fully separate from the main Resume Agent."
        ),
    )
    parser.add_argument("--url", required=True, help="Job posting URL")
    parser.add_argument("--cv", required=True, help="Path to CV / resume file (PDF/DOCX)")
    parser.add_argument("--first-name", required=True, help="Applicant first name")
    parser.add_argument("--last-name", required=True, help="Applicant last name")
    parser.add_argument("--email", required=True, help="Applicant email")
    parser.add_argument("--phone", required=True, help="Applicant phone")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fill the form but do not click Submit",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window (default is headless)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=60_000,
        help="Navigation timeout in milliseconds (default: 60000)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print result as JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    request = ApplyRequest(
        job_url=args.url,
        cv_path=Path(args.cv),
        applicant=Applicant(
            first_name=args.first_name,
            last_name=args.last_name,
            email=args.email,
            phone=args.phone,
        ),
        dry_run=args.dry_run,
        headless=not args.headed,
        timeout_ms=args.timeout_ms,
    )
    result = apply_to_job(request)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"status:  {result.status}")
        print(f"success: {result.success}")
        print(f"message: {result.message}")
        if result.filled_fields:
            print(f"filled:  {', '.join(result.filled_fields)}")
        if result.final_url:
            print(f"url:     {result.final_url}")
        if result.screenshot_path:
            print(f"shot:    {result.screenshot_path}")
        if result.confirmation_text:
            print(f"confirm: {result.confirmation_text}")
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
