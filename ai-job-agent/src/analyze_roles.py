"""STEP 1 — Build matching strategy from universal candidate profile (no AI).

Reads universal_profile from cv_profile.json (created during parse_cv) and writes
reusable local matching rules for job collection and scoring.

Saves:
  - data/ai_matching_strategy.json
  - data/ai_roles.json
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import AI_MATCHING_STRATEGY_PATH
from job_identity import compute_candidate_input_hash
from parse_cv import load_cv_profile
from pipeline_state import load_pipeline_state, save_pipeline_state
from profile_utils import load_profile
from role_analyzer import (
    fallback_matching_strategy,
    load_matching_strategy,
    save_ai_roles,
    save_matching_strategy,
)
from universal_profile import (
    build_matching_strategy_from_profile,
    get_universal_profile,
)


def print_roles_report(data: dict) -> None:
    print(f"Source: {data.get('source', 'unknown')}")
    print(f"Analyzed at: {data.get('analyzed_at', 'n/a')}")
    if data.get("career_notes"):
        print(f"\nCareer notes:\n  {data['career_notes']}")

    roles = data.get("best_fit_roles", [])
    if not roles:
        print("\nNo roles identified.")
        return

    print(f"\nBest-fit roles ({len(roles)}):\n")
    for index, entry in enumerate(roles, start=1):
        realistic = "yes" if entry.get("realistic_for_application", True) else "no"
        gaps = ", ".join(entry.get("missing_skills", [])) or "none"
        print(f"  {index}. {entry['role']} — score {entry['score']}")
        print(f"     Reason: {entry.get('reason', '')}")
        print(f"     Missing skills: {gaps}")
        print(f"     Realistic to apply now: {realistic}")
        print()

    categories = data.get("job_categories", [])
    if categories:
        print(f"Job categories for local matching ({len(categories)}):")
        for cat in categories:
            must = ", ".join(cat.get("must_have_keywords", [])[:6]) or "none"
            print(f"  - {cat['category']}: must-have [{must}]")
        print()

    collection_queries = data.get("collection_queries", [])
    if collection_queries:
        print(f"Collection search queries ({len(collection_queries)} categories):")
        for entry in collection_queries:
            queries = ", ".join(entry.get("queries", [])[:8]) or "none"
            print(f"  - {entry.get('category')} (priority {entry.get('priority', 0)}): {queries}")
        print()

    reject_rules = data.get("global_reject_rules", [])
    if reject_rules:
        print("Global reject rules:")
        for rule in reject_rules:
            print(f"  - {rule}")
        print()


def strategy_needs_refresh(profile: dict, cv_profile: dict) -> bool:
    """True when CV/profile changed or no saved strategy file exists."""
    if not AI_MATCHING_STRATEGY_PATH.exists():
        return True

    current_input_hash = compute_candidate_input_hash(profile, cv_profile)
    stored_input_hash = load_pipeline_state().get("candidate_input_hash")
    if stored_input_hash != current_input_hash:
        return True

    strategy = load_matching_strategy()
    return not strategy or not strategy.get("job_categories")


def build_strategy(profile: dict, cv_profile: dict) -> dict:
    """Build matching strategy from universal profile or fallback."""
    universal = get_universal_profile(cv_profile)
    if universal and (
        universal.get("preferred_role_titles")
        or universal.get("canonical_roles")
        or universal.get("collection_queries")
    ):
        return build_matching_strategy_from_profile(universal, profile, cv_profile)
    return fallback_matching_strategy(profile, cv_profile)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build local matching strategy from universal CV profile (no per-job OpenAI)"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-build strategy even if strategy files exist",
    )
    parser.add_argument(
        "--fallback-only",
        action="store_true",
        help="Skip universal profile and use profile/CV fallback strategy",
    )
    args = parser.parse_args()

    print("AI Job Agent — search strategy from universal profile")

    profile = load_profile()
    cv_profile = load_cv_profile()

    if not args.refresh and not strategy_needs_refresh(profile, cv_profile):
        existing_strategy = load_matching_strategy()
        if existing_strategy:
            print(
                f"\nUsing existing matching strategy "
                f"({AI_MATCHING_STRATEGY_PATH.as_posix()}) — "
                "CV/profile unchanged; pass --refresh to re-build\n"
            )
            print_roles_report(existing_strategy)
            return

    if not args.refresh and strategy_needs_refresh(profile, cv_profile):
        print("CV or profile changed — rebuilding matching strategy...\n")

    if args.fallback_only:
        result = fallback_matching_strategy(profile, cv_profile)
    else:
        result = build_strategy(profile, cv_profile)

    save_matching_strategy(result)
    save_ai_roles({
        "analyzed_at": result.get("analyzed_at"),
        "source": result.get("source"),
        "candidate_summary": result.get("candidate_summary"),
        "career_notes": result.get("career_notes"),
        "best_fit_roles": result.get("best_fit_roles", []),
    })
    save_pipeline_state({
        "candidate_input_hash": compute_candidate_input_hash(profile, cv_profile),
    })

    print(f"\nSaved: {AI_MATCHING_STRATEGY_PATH.as_posix()}")
    print("Saved: data/ai_roles.json\n")
    print_roles_report(result)


if __name__ == "__main__":
    main()
