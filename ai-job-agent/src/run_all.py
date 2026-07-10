"""Run the full AI Job Agent pipeline in one command.

The pipeline is incremental by default: collection only inserts new jobs,
enrichment only processes jobs that were never attempted before, and matching
only scores new or changed jobs. Re-running this command is therefore fast and
does not rescan jobs that were already handled.

Usage (from project root):
    python src/run_all.py

Optional flags:
    python src/run_all.py --min-score 55
    python src/run_all.py --skip-collect      # skip Drushim scraping
    python src/run_all.py --skip-enrich       # skip fetching full job pages
    python src/run_all.py --retry-failed-enrich  # retry previously failed enrichments
    python src/run_all.py --skip-apply        # don't offer to send CVs
    python src/run_all.py --dry-run-apply     # fill the form but don't send
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Project root is one level above src/
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PYTHON = sys.executable


def run_step(name: str, script: str, extra_args: list[str] | None = None) -> bool:
    """Run a pipeline script and return True if it succeeded."""
    cmd = [PYTHON, str(SRC / script), *(extra_args or [])]
    print(f"\n{'=' * 60}")
    print(f"  {name}")
    print(f"{'=' * 60}\n")

    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"\nWarning: {name} exited with code {result.returncode}")
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full AI Job Agent pipeline")
    parser.add_argument(
        "--min-score",
        type=int,
        default=55,
        help="Minimum match score for final list (default: 55)",
    )
    parser.add_argument(
        "--skip-collect",
        action="store_true",
        help="Skip job collection from Drushim, LinkedIn, and GotFriends",
    )
    parser.add_argument(
        "--skip-enrich",
        action="store_true",
        help="Skip fetching full job descriptions",
    )
    parser.add_argument(
        "--retry-failed-enrich",
        action="store_true",
        help="Retry jobs whose enrichment previously failed/timed out/was blocked",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Auto-continue if OpenAI CV analysis fails",
    )
    parser.add_argument(
        "--skip-apply",
        action="store_true",
        help="Do not offer to send CVs at the end",
    )
    parser.add_argument(
        "--dry-run-apply",
        action="store_true",
        help="When applying, fill the form but do not click the final send button",
    )
    args = parser.parse_args()

    print("AI Job Agent — full pipeline")
    print(f"Project: {ROOT}")

    apply_args = ["--min-score", str(args.min_score)]
    if args.dry_run_apply:
        apply_args.append("--dry-run")

    steps: list[tuple[str, str, list[str] | None, bool]] = [
        (
            "1/7  Parse resume (CV)",
            "parse_cv.py",
            ["--yes"] if args.yes else None,
            True,
        ),
        ("2/7  AI CV analysis + role strategy", "analyze_roles.py", None, True),
        ("3/7  Collect jobs from Drushim + LinkedIn + GotFriends", "collect_jobs.py", None, not args.skip_collect),
        (
            "4/7  Enrich job descriptions",
            "enrich_jobs.py",
            ["--retry-failed"] if args.retry_failed_enrich else None,
            not args.skip_enrich,
        ),
        ("5/7  Local job matching (AI strategy)", "match_jobs.py", None, True),
        (
            "6/7  Show results",
            "list_jobs.py",
            ["--min-score", str(args.min_score), "--why", "--url"],
            True,
        ),
        (
            "7/7  Send CV to matched jobs",
            "apply_jobs.py",
            apply_args,
            not args.skip_apply,
        ),
    ]

    warnings = 0
    for name, script, extra, enabled in steps:
        if not enabled:
            print(f"\nSkipping: {name}")
            continue
        if not run_step(name, script, extra):
            warnings += 1
            if script in ("parse_cv.py", "analyze_roles.py", "match_jobs.py"):
                print(f"\nPipeline stopped — {name} failed.")
                sys.exit(1)

    print(f"\n{'=' * 60}")
    if warnings:
        print(f"Done with {warnings} warning(s). Check output above.")
    else:
        print("Done! All steps completed successfully.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
