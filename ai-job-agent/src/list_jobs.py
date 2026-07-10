import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import get_jobs, init_db, mark_jobs_declined
from profile_utils import load_profile

COLUMNS = ["score", "decision", "action", "apply", "title", "company", "location", "source"]
MAX_WIDTH = {
    "score": 5,
    "decision": 12,
    "action": 18,
    "apply": 10,
    "title": 36,
    "company": 22,
    "location": 14,
    "source": 8,
}

APPLICATION_LABELS = {
    "sent": "נשלח",
    "declined": "לא להגיש",
    "skipped": "דולג",
    "failed": "נכשל",
    "pending": "ממתין",
    "dry_run": "בדיקה",
}


def _application_label(status: str | None) -> str:
    if not status:
        return "חדש"
    return APPLICATION_LABELS.get(status, status)


def _parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def format_cell(column: str, value: str, width: int) -> str:
    text = str(value or "")
    if len(text) > width:
        text = text[: width - 3] + "..."
    return text.ljust(width)


def print_job_why(job: dict) -> None:
    score = job.get("match_score")
    score_text = str(score) if score is not None else "-"
    method = job.get("match_method") or "unknown"
    print(f"[{score_text}] {job.get('title') or ''} @ {job.get('company') or ''}")
    print(f"  Method: {method}")

    if job.get("match_category"):
        print(f"  Category: {job['match_category']}")

    if job.get("ai_decision"):
        print(f"  Decision: {job['ai_decision']}")
    if job.get("ai_recommended_action"):
        print(f"  Action: {job['ai_recommended_action']}")

    matched_kw = _parse_json_list(job.get("matched_keywords")) or _parse_json_list(
        job.get("ai_strengths")
    )
    if matched_kw:
        print(f"  Matched keywords: {', '.join(matched_kw)}")

    missing_kw = _parse_json_list(job.get("missing_keywords")) or _parse_json_list(
        job.get("ai_missing_skills")
    )
    if missing_kw:
        print(f"  Missing keywords: {', '.join(missing_kw)}")

    if job.get("rejection_reason"):
        print(f"  Rejection reason: {job['rejection_reason']}")

    if job.get("ai_explanation") and method in ("ai", "ai_review"):
        print(f"  AI explanation: {job['ai_explanation']}")
    elif job.get("ai_explanation") and method == "local":
        print(f"  Scoring detail: {job['ai_explanation']}")

    if job.get("fallback_score") is not None and method in ("ai", "ai_review", "local"):
        print(f"  Reference/fallback score: {job['fallback_score']}")

    print(f"  Summary: {job.get('match_reason') or '(not scored yet)'}")

    app_status = job.get("application_status")
    if app_status:
        print(f"  Application: {_application_label(app_status)}")
        if job.get("application_applied_at"):
            print(f"  Applied at: {job['application_applied_at']}")
        if job.get("application_notes"):
            print(f"  Application notes: {job['application_notes']}")

    if job.get("job_url"):
        print(f"  URL: {job['job_url']}")
    print()


def print_jobs_table(jobs: list[dict], show_url: bool = False) -> None:
    if not jobs:
        print("No jobs found.")
        return

    columns = list(COLUMNS)
    if show_url:
        columns.append("job_url")

    widths = {col: MAX_WIDTH.get(col, 20) for col in columns}

    for job in jobs:
        for col in columns:
            if col == "score":
                cell = str(job.get("match_score") if job.get("match_score") is not None else "-")
            elif col == "decision":
                cell = str(job.get("ai_decision") or "-")
            elif col == "action":
                cell = str(job.get("ai_recommended_action") or "-")
            elif col == "apply":
                cell = _application_label(job.get("application_status"))
            else:
                cell = str(job.get(col) or "")
            widths[col] = max(widths[col], min(len(cell), MAX_WIDTH.get(col, 50)))

    header = " | ".join(col.ljust(widths[col]) for col in columns)
    separator = "-+-".join("-" * widths[col] for col in columns)
    print(header)
    print(separator)

    for job in jobs:
        row = []
        for col in columns:
            if col == "score":
                value = job.get("match_score")
                cell = str(value) if value is not None else "-"
            elif col == "decision":
                cell = str(job.get("ai_decision") or "-")
            elif col == "action":
                cell = str(job.get("ai_recommended_action") or "-")
            elif col == "apply":
                cell = _application_label(job.get("application_status"))
            else:
                cell = str(job.get(col) or "")
            row.append(format_cell(col, cell, widths[col]))
        print(" | ".join(row))


def main() -> None:
    parser = argparse.ArgumentParser(description="List saved jobs from the database")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show all jobs (ignore min_match_score)",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="Minimum match score (default: from profile.json)",
    )
    parser.add_argument(
        "--include-handled",
        action="store_true",
        help="Include jobs already sent/declined/skipped (default: only new actionable)",
    )
    parser.add_argument(
        "--decline",
        type=int,
        nargs="+",
        metavar="JOB_ID",
        help="Mark job id(s) as declined (will not be suggested again)",
    )
    parser.add_argument(
        "--url",
        action="store_true",
        help="Include job_url column",
    )
    parser.add_argument(
        "--why",
        action="store_true",
        help="Show AI reasoning and match details for each job",
    )
    args = parser.parse_args()

    init_db()

    if args.decline:
        count = mark_jobs_declined(args.decline)
        print(f"Marked {count} job(s) as declined (will not be suggested again).")
        if not args.all and args.min_score is None and not args.why:
            return

    profile = load_profile()
    exclude_handled = not args.include_handled

    if args.all:
        jobs = get_jobs(exclude_handled=exclude_handled)
        label = "All jobs"
    else:
        min_score = args.min_score
        if min_score is None:
            min_score = profile.get("min_match_score", 0)
        jobs = get_jobs(min_score=min_score, exclude_handled=exclude_handled)
        label = f"Jobs with score >= {min_score}"

    handled_hidden = 0
    if exclude_handled:
        if args.all:
            all_jobs = get_jobs(exclude_handled=False)
            handled_hidden = len(all_jobs) - len(jobs)
        else:
            min_score = args.min_score if args.min_score is not None else profile.get("min_match_score", 0)
            all_matched = get_jobs(min_score=min_score, exclude_handled=False)
            handled_hidden = len(all_matched) - len(jobs)

    print(f"{label} ({len(jobs)}):\n")
    if exclude_handled and handled_hidden:
        print(
            f"({handled_hidden} handled job(s) hidden — already sent/declined/skipped. "
            "Use --include-handled to show them.)\n"
        )

    if args.why:
        for job in jobs:
            print_job_why(job)
    else:
        print_jobs_table(jobs, show_url=args.url)


if __name__ == "__main__":
    main()
