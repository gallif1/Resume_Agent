"""Automatically send your CV to matched Drushim jobs.

Flow:
    1. Load matched jobs from the database (score >= threshold).
    2. Show the relevant jobs.
    3. Ask for confirmation (type Y to send).
    4. Open Drushim in a real browser. If a login is required, ask the user
       to sign in (using credentials from .env, or manually in the window).
    5. For each job: open the page, fill the application details, attach the
       CV, and send it. Each result is recorded in the `applications` table.

Usage (from project root):
    python src/apply_jobs.py
    python src/apply_jobs.py --min-score 60 --limit 5
    python src/apply_jobs.py --yes            # skip the confirmation prompt
    python src/apply_jobs.py --dry-run        # fill the form but do not send
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from config import (
    APPLY_HEADLESS,
    AUTO_SUBMIT,
    BROWSER_PROFILE_DIR,
    DRUSHIM_BASE_URL,
    DRUSHIM_EMAIL,
    DRUSHIM_PASSWORD,
    LOGS_DIR,
)
from cv_reader import find_resume_path
from db import get_handled_job_ids, get_jobs, init_db, record_application
from profile_utils import load_cv_contact, load_profile
from prompts import ask_yes_no

# --- Selectors / text markers (Drushim is a Hebrew site) ---------------------

APPLY_BUTTON_SELECTORS = [
    "#cv-send-btn button",
    "button.cv_btn",
]
APPLY_BUTTON_TEXTS = [
    "שלח/י קורות חיים",
    "שליחת קורות חיים",
    "שלח קורות חיים",
    "הגשת מועמדות",
    "הגש מועמדות",
]

SUBMIT_BUTTON_TEXTS = [
    "שליחת קורות חיים",
    "שלח קורות חיים",
    "שליחה",
    "שלח/י",
    "הגשה",
    "הגש",
    "אישור ושליחה",
    "שלח",
]

SUCCESS_TEXTS = [
    "קורות החיים נשלחו",
    "קו\"ח נשלחו",
    "המועמדות נשלחה",
    "המועמדות הוגשה",
    "הפנייה נשלחה",
    "נשלח בהצלחה",
    "תודה שפנית",
    "תודה על פנייתך",
    "שלחת קורות חיים",
    "כבר שלחת",
]

# Text/elements that indicate a signed-in session. "אזור אישי" is intentionally
# excluded because Drushim shows it to anonymous visitors too.
LOGGED_IN_TEXTS = ["התנתקות", "התנתק", "החשבון שלי", "logout", "המשרות שלי"]

# Login dialog (Drushim has no dedicated /login page; it's a header pop-up menu).
LOGIN_OPEN_SELECTORS = ["button.login-btn", ".desktop-login", "#user-menu .login-text"]
LOGIN_EMAIL_SELECTOR = "#email-login-field"
LOGIN_PASSWORD_SELECTOR = "#password-login-field"
LOGIN_SUBMIT_SELECTORS = ["button.orange_btn_large"]
LOGIN_SUBMIT_TEXTS = ["כניסה"]
# Logged-out state markers shown in the header.
LOGGED_OUT_SELECTORS = ["button.login-btn", ".user-header-info.logged-out"]
# Cookie / promo overlays that can intercept clicks.
COOKIE_ACCEPT_TEXTS = ["הבנתי", "סגור הודעת עוגיות", "אישור"]

PLACEHOLDER_VALUES = {
    "",
    "your_name",
    "your_email@gmail.com",
    "your_email",
    "your_phone",
    "your phone",
}


# --- Contact details ---------------------------------------------------------

def _looks_placeholder(value: str | None) -> bool:
    if not value:
        return True
    low = value.strip().lower()
    return low in PLACEHOLDER_VALUES or low.startswith("your_")


def resolve_contact() -> dict:
    """Contact details from the parsed resume (never from a saved global profile)."""
    cv_contact = load_cv_contact()

    def pick(value: str | None) -> str:
        if _looks_placeholder(value):
            return ""
        return value.strip()

    return {
        "name": pick(cv_contact.get("name")),
        "email": pick(cv_contact.get("email")),
        "phone": pick(cv_contact.get("phone")),
    }


# --- Login -------------------------------------------------------------------

def _page_text(page: Page) -> str:
    try:
        return (page.inner_text("body") or "").lower()
    except Exception:
        return ""


def _dismiss_overlays(page: Page) -> None:
    """Close cookie/promo banners that can intercept clicks."""
    for text in COOKIE_ACCEPT_TEXTS:
        try:
            locator = page.get_by_text(text, exact=False).first
            if locator.count() and locator.is_visible():
                locator.click(timeout=2000)
                page.wait_for_timeout(500)
        except Exception:
            continue


def is_logged_in(page: Page) -> bool:
    """Detect a signed-in Drushim session via header state."""
    try:
        page.wait_for_selector(".user-header-info", timeout=6000)
    except Exception:
        pass

    # Strong negative signal: the header still shows the logged-out login button.
    for selector in LOGGED_OUT_SELECTORS:
        try:
            if page.locator(selector).first.is_visible():
                return False
        except Exception:
            continue

    try:
        if page.locator(".user-header-info.logged-in").count():
            return True
    except Exception:
        pass

    text = _page_text(page)
    return any(signal.lower() in text for signal in LOGGED_IN_TEXTS)


def _login_fields_visible(page: Page) -> bool:
    """True if the Drushim login dialog (email + password) is currently visible."""
    try:
        return page.locator(LOGIN_PASSWORD_SELECTOR).first.is_visible()
    except Exception:
        return False


def open_login_dialog(page: Page) -> bool:
    """Open Drushim's login pop-up by clicking the header 'התחברות' button.

    Drushim is a single-page app with no /login route, so navigating to a URL
    does nothing useful; the login form is a header menu. Returns True if the
    login fields became visible.
    """
    if page.url.rstrip("/") != DRUSHIM_BASE_URL.rstrip("/"):
        try:
            page.goto(DRUSHIM_BASE_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
        except Exception:
            pass

    _dismiss_overlays(page)

    if _login_fields_visible(page):
        return True

    for selector in LOGIN_OPEN_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                locator.click()
                page.wait_for_timeout(2000)
                if _login_fields_visible(page):
                    return True
        except Exception:
            continue

    return _login_fields_visible(page)


def attempt_credential_login(page: Page, email: str, password: str) -> bool:
    """Best-effort automated login with email + password."""
    print("Trying to sign in to Drushim with the provided credentials...")

    if not open_login_dialog(page):
        print("Could not open the login dialog automatically.")
        return False

    try:
        page.locator(LOGIN_EMAIL_SELECTOR).first.fill(email)
        page.locator(LOGIN_PASSWORD_SELECTOR).first.fill(password)
    except Exception as error:
        print(f"Could not fill the login fields: {error}")
        return False

    if not _click_first(page, LOGIN_SUBMIT_SELECTORS, LOGIN_SUBMIT_TEXTS):
        try:
            page.locator(LOGIN_PASSWORD_SELECTOR).first.press("Enter")
        except Exception:
            pass
    page.wait_for_timeout(4000)

    if is_logged_in(page):
        print("Signed in successfully.")
        return True

    print("Automated sign-in did not complete (wrong credentials or an SMS code is required).")
    return False


def wait_for_manual_login(page: Page) -> bool:
    """Open the login dialog and let the user sign in manually in the browser window."""
    open_login_dialog(page)

    print("\n" + "=" * 60)
    print("Drushim sign-in required.")
    print("Please sign in now in the browser window that just opened")
    print("(SMS verification codes are supported).")
    print("When you are signed in, return here and press Enter to continue.")
    print("=" * 60)
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return is_logged_in(page)


def ensure_logged_in(page: Page) -> bool:
    """Make sure we have a Drushim session. Returns True if logged in."""
    try:
        page.goto(DRUSHIM_BASE_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)
    except Exception:
        pass

    if is_logged_in(page):
        print("Already signed in to Drushim (existing session).")
        return True

    email = DRUSHIM_EMAIL
    password = DRUSHIM_PASSWORD

    if not (email and password):
        print("\nSigning in to Drushim may be required to send your CV.")
        if ask_yes_no(
            "Enter login credentials now? (If not, you can sign in manually in the browser)",
            default=True,
        ):
            try:
                email = input("Email / phone: ").strip()
                password = getpass.getpass("Password: ")
            except (EOFError, KeyboardInterrupt):
                print()
                email = password = ""

    if email and password and attempt_credential_login(page, email, password):
        return True

    return wait_for_manual_login(page)


# --- Applying ----------------------------------------------------------------

def _click_first(page: Page, selectors: list[str], texts: list[str]) -> bool:
    """Click the first visible element matching a CSS selector or button text."""
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                locator.click()
                return True
        except Exception:
            continue
    for text in texts:
        try:
            locator = page.get_by_role("button", name=text, exact=False).first
            if locator.count() and locator.is_visible():
                locator.click()
                return True
        except Exception:
            pass
        try:
            locator = page.get_by_text(text, exact=False).first
            if locator.count() and locator.is_visible():
                locator.click()
                return True
        except Exception:
            continue
    return False


def _page_shows_success(page: Page) -> bool:
    text = _page_text(page)
    return any(signal.lower() in text for signal in SUCCESS_TEXTS)


def _page_shows_login_wall(page: Page) -> bool:
    if "login" in page.url.lower():
        return True
    try:
        if page.locator("input[type='password']").first.is_visible():
            return True
    except Exception:
        pass
    return False


def fill_application_form(page: Page, contact: dict, cv_path: Path) -> None:
    """Best-effort fill of any visible application fields and attach the CV."""
    # Attach the CV to any file input that is present.
    try:
        file_inputs = page.locator("input[type='file']")
        if file_inputs.count():
            file_inputs.first.set_input_files(str(cv_path))
            page.wait_for_timeout(1000)
    except Exception:
        pass

    field_map = [
        (contact.get("name"), ["name", "שם", "fullname", "full_name", "שם מלא"], True),
        (contact.get("email"), ["email", "mail", "אימייל", "דוא", "מייל"], True),
        (contact.get("phone"), ["phone", "tel", "mobile", "טלפון", "נייד", "פלאפון"], True),
    ]

    try:
        inputs = page.locator("input:visible, textarea:visible")
        count = min(inputs.count(), 30)
    except Exception:
        count = 0

    for i in range(count):
        try:
            field = inputs.nth(i)
            input_type = (field.get_attribute("type") or "").lower()
            if input_type in {"file", "checkbox", "radio", "hidden", "submit", "button"}:
                continue

            attr_blob = " ".join(
                (field.get_attribute(attr) or "")
                for attr in ("name", "id", "placeholder", "aria-label")
            ).lower()

            for value, keywords, overwrite in field_map:
                if not value or not any(keyword in attr_blob for keyword in keywords):
                    continue
                current = field.input_value().strip()
                if overwrite or not current:
                    field.fill(value)
                break
        except Exception:
            continue


def _save_screenshot(page: Page, job_id: int, tag: str) -> None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        page.screenshot(path=str(LOGS_DIR / f"apply_{job_id}_{tag}_{stamp}.png"))
    except Exception:
        pass


def apply_to_job(
    page: Page,
    job: dict,
    contact: dict,
    cv_path: Path,
    auto_submit: bool,
) -> tuple[str, str]:
    """Apply to a single job. Returns (status, note)."""
    url = job.get("job_url", "")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
    except PlaywrightTimeoutError:
        return "failed", "Page load failed (timeout)"
    except Exception as error:
        return "failed", f"Navigation error: {error}"

    if _page_shows_success(page):
        return "sent", "Already applied to this job"

    if not _click_first(page, APPLY_BUTTON_SELECTORS, APPLY_BUTTON_TEXTS):
        _save_screenshot(page, job["id"], "no_button")
        return "failed", "Could not find the 'send CV' button on the page"

    page.wait_for_timeout(2500)

    if _page_shows_login_wall(page):
        return "needs_login", "Login required before sending"

    fill_application_form(page, contact, cv_path)

    # If clicking the apply button already sent it (logged-in one-click flow).
    if _page_shows_success(page):
        _save_screenshot(page, job["id"], "sent")
        return "sent", "Sent (one-click apply)"

    if not auto_submit:
        _save_screenshot(page, job["id"], "dryrun")
        return "skipped", "Dry run - form filled but not sent"

    if not _click_first(page, ["button[type='submit']"], SUBMIT_BUTTON_TEXTS):
        _save_screenshot(page, job["id"], "no_submit")
        return "failed", "Form filled but no final send button found"

    page.wait_for_timeout(3000)

    if _page_shows_success(page):
        _save_screenshot(page, job["id"], "sent")
        return "sent", "Sent successfully"

    _save_screenshot(page, job["id"], "unknown")
    return "sent", "Sent (success not confirmed - check screenshot in logs/)"


def select_jobs(min_score: int, limit: int | None, include_handled: bool) -> list[dict]:
    jobs = get_jobs(min_score=min_score, exclude_handled=not include_handled)
    jobs = [job for job in jobs if job.get("source") == "drushim" and job.get("job_url")]

    if limit is not None:
        jobs = jobs[:limit]
    return jobs


def print_jobs(jobs: list[dict]) -> None:
    print(f"\nFound {len(jobs)} relevant job(s) to send your CV to:\n")
    for i, job in enumerate(jobs, start=1):
        score = job.get("match_score")
        score_text = str(score) if score is not None else "-"
        print(f"  {i:>2}. [{score_text}] {job.get('title') or ''}")
        print(f"      Company: {job.get('company') or '-'} | Location: {job.get('location') or '-'}")
        print(f"      Link: {job.get('job_url')}")


def run_apply(
    jobs: list[dict],
    contact: dict,
    cv_path: Path,
    headless: bool,
    auto_submit: bool,
) -> dict:
    """Drive the browser through all jobs. Returns a summary dict."""
    summary = {"sent": 0, "failed": 0, "skipped": 0}
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=headless,
            accept_downloads=True,
            viewport={"width": 1366, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            logged_in = ensure_logged_in(page)
            if not logged_in:
                print("\nDrushim sign-in not completed. Some submissions may fail.")

            for i, job in enumerate(jobs, start=1):
                print(f"\n[{i}/{len(jobs)}] {job.get('title') or ''} - {job.get('company') or ''}")
                status, note = apply_to_job(page, job, contact, cv_path, auto_submit)

                if status == "needs_login":
                    if ensure_logged_in(page):
                        status, note = apply_to_job(page, job, contact, cv_path, auto_submit)
                    else:
                        status = "failed"

                record_status = status if status in summary else "failed"
                # Dry-run must not permanently hide jobs from future suggestions.
                if auto_submit or record_status in ("sent", "failed"):
                    record_application(job["id"], record_status, note)
                summary[record_status] = summary.get(record_status, 0) + 1

                icon = {"sent": "[OK]", "skipped": "[--]"}.get(record_status, "[XX]")
                print(f"    {icon} {note}")

                page.wait_for_timeout(1500)
        finally:
            context.close()

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Send your CV to matched Drushim jobs")
    parser.add_argument("--min-score", type=int, default=None, help="Minimum match score")
    parser.add_argument("--limit", type=int, default=None, help="Max jobs to apply to")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip the confirmation prompt")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fill the application form but do not click the final send button",
    )
    parser.add_argument(
        "--include-handled",
        action="store_true",
        help="Also offer jobs already sent/declined/skipped (legacy: --include-applied)",
    )
    parser.add_argument(
        "--include-applied",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser without a visible window (not recommended)",
    )
    args = parser.parse_args()

    # Hebrew job data may be printed; force UTF-8 to avoid console mangling.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    init_db()
    profile = load_profile()
    min_score = args.min_score
    if min_score is None:
        min_score = profile.get("min_match_score", 60)

    include_handled = args.include_handled or args.include_applied
    jobs = select_jobs(min_score, args.limit, include_handled)
    if not jobs:
        handled = len(get_handled_job_ids())
        print(f"No new actionable jobs with match score >= {min_score}.")
        if handled:
            print(
                f"({handled} job(s) already handled — sent/declined/skipped — "
                "use --include-handled to see them again)"
            )
        print("Run the full pipeline first: python src/run_all.py")
        return

    print_jobs(jobs)

    contact = resolve_contact()
    if not contact["email"] and not contact["phone"]:
        print("\nWarning: no email/phone found in the parsed resume (cv_profile.json).")
    print(
        f"\nApplicant details: {contact['name'] or '(no name)'} | "
        f"{contact['email'] or '(no email)'} | {contact['phone'] or '(no phone)'}"
    )

    cv_path = find_resume_path()
    if cv_path is None:
        print("\nError: no CV file found in resumes/. Add a cv.pdf file.")
        sys.exit(1)
    print(f"CV file to send: {cv_path.name}")

    auto_submit = AUTO_SUBMIT and not args.dry_run
    if not auto_submit:
        print("\n(Dry run - the form will be filled but not actually sent)")

    if not args.yes:
        if not ask_yes_no(
            f"\nSend your CV to these {len(jobs)} job(s)? (press Y to confirm)",
            default=False,
        ):
            print("Cancelled. No CVs were sent.")
            return

    headless = args.headless or APPLY_HEADLESS
    summary = run_apply(jobs, contact, cv_path, headless, auto_submit)

    print(f"\n{'=' * 60}")
    print("CV submission summary:")
    print(f"  Sent:    {summary.get('sent', 0)}")
    print(f"  Failed:  {summary.get('failed', 0)}")
    print(f"  Skipped: {summary.get('skipped', 0)}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
