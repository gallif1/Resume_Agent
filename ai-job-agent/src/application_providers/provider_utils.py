"""Shared helpers for application provider adapters."""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import Locator, Page

from browser_utils import page_looks_blocked
from field_mapper import FIELD_SYNONYMS, build_profile_values, field_blob_from_element, match_field_key

LOGIN_MARKERS = (
    "sign in",
    "log in",
    "login",
    "התחברות",
    "כניסה",
)

SUCCESS_MARKERS = (
    "thank you for applying",
    "application submitted",
    "application received",
    "your application has been",
    "successfully submitted",
    "we received your application",
    "קורות החיים נשלחו",
    "המועמדות נשלחה",
    "המועמדות הוגשה",
    "תודה על פנייתך",
    "נשלח בהצלחה",
)


def page_text(page: Page, limit: int = 8000) -> str:
    try:
        return (page.evaluate("() => document.body.innerText || ''") or "")[:limit]
    except Exception:
        return ""


def detect_captcha(page: Page) -> bool:
    """True only when a visible CAPTCHA challenge blocks progress.

    Many job sites embed reCAPTCHA/hCaptcha scripts in every page for spam
    protection. Matching those script tags causes false positives on every job.
    """
    if page_looks_blocked(page):
        return True

    captcha_selectors = (
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "iframe[title*='recaptcha' i]",
        "iframe[title*='hcaptcha' i]",
        ".g-recaptcha:visible",
        ".h-captcha:visible",
        "#cf-turnstile:visible",
        "[class*='captcha']:visible",
    )
    for selector in captcha_selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                return True
        except Exception:
            continue

    visible = page_text(page).lower()
    if visible.strip():
        challenge_phrases = (
            "verify you are human",
            "complete the captcha",
            "please complete the security check",
            "אימות שאינך רובוט",
            "אנא השלם את האימות",
            "השלם את ה-captcha",
        )
        if any(phrase in visible for phrase in challenge_phrases):
            return True
        # Short pages with explicit captcha instructions (not script references).
        if len(visible.strip()) < 800 and "captcha" in visible:
            return True

    return False


def detect_login_required(page: Page) -> bool:
    url = (page.url or "").lower()
    if "login" in url or "signin" in url or "sign-in" in url:
        return True
    try:
        if page.locator("input[type='password']:visible").count() > 0:
            return True
    except Exception:
        pass
    text = page_text(page).lower()
    if any(marker in text for marker in LOGIN_MARKERS):
        try:
            if page.locator("input[type='email']:visible, input[type='password']:visible").count() > 0:
                return True
        except Exception:
            pass
    return False


def detect_submission_success(page: Page) -> tuple[bool, str]:
    text = page_text(page)
    lowered = text.lower()
    for marker in SUCCESS_MARKERS:
        if marker.lower() in lowered:
            # Extract a short confirmation snippet.
            idx = lowered.find(marker.lower())
            snippet = text[max(0, idx - 20) : idx + len(marker) + 40].strip()
            return True, snippet[:200]
    url = (page.url or "").lower()
    if any(token in url for token in ("/thank", "/confirmation", "/success", "/applied")):
        return True, f"Redirected to success URL: {page.url}"
    return False, ""


def url_matches(url: str, *patterns: str) -> bool:
    lowered = (url or "").lower()
    return any(pattern in lowered for pattern in patterns)


def get_field_attrs(locator: Locator) -> dict[str, str | None]:
    attrs: dict[str, str | None] = {}
    for name in ("name", "id", "placeholder", "aria-label", "type", "autocomplete"):
        try:
            attrs[name] = locator.get_attribute(name)
        except Exception:
            attrs[name] = None
    return attrs


def find_label_for(page: Page, locator: Locator) -> str:
    try:
        field_id = locator.get_attribute("id")
        if field_id:
            label = page.locator(f"label[for='{field_id}']").first
            if label.count() and label.is_visible():
                return (label.inner_text() or "").strip()
    except Exception:
        pass
    try:
        parent = locator.locator("xpath=ancestor::label[1]")
        if parent.count():
            return (parent.first.inner_text() or "").strip()
    except Exception:
        pass
    return ""


def fill_mapped_fields(
    page: Page,
    profile: dict[str, Any],
    *,
    max_fields: int = 40,
) -> tuple[list[str], list[str], list[str]]:
    """Fill visible inputs using normalized field mapping. Returns filled, skipped, uncertain."""
    values = build_profile_values(profile)
    filled: list[str] = []
    skipped: list[str] = []
    uncertain: list[str] = []

    try:
        inputs = page.locator(
            "input:visible, textarea:visible, select:visible"
        )
        count = min(inputs.count(), max_fields)
    except Exception:
        return filled, skipped, uncertain

    used_keys: set[str] = set()

    for i in range(count):
        field = inputs.nth(i)
        try:
            attrs = get_field_attrs(field)
            input_type = (attrs.get("type") or "").lower()
            if input_type in {"file", "checkbox", "radio", "hidden", "submit", "button"}:
                continue

            label_text = find_label_for(page, field)
            blob = field_blob_from_element(attrs, label_text)
            key = match_field_key(blob)

            if not key:
                skipped.append(blob[:60] or f"field_{i}")
                continue

            if key in used_keys and key not in {"experience", "education", "skills", "cover_letter"}:
                continue

            value = values.get(key, "")
            if not value:
                skipped.append(key)
                continue

            if key == "full_name" and ("first_name" in used_keys or "last_name" in used_keys):
                continue

            current = ""
            try:
                current = (field.input_value() or "").strip()
            except Exception:
                pass

            tag_name = field.evaluate("el => el.tagName.toLowerCase()")
            if tag_name == "select":
                try:
                    field.select_option(label=value)
                except Exception:
                    uncertain.append(key)
                    continue
            elif not current:
                field.fill(value)
            elif current != value:
                uncertain.append(key)
                continue

            filled.append(key)
            used_keys.add(key)
        except Exception:
            uncertain.append(f"field_{i}")
            continue

    return filled, skipped, uncertain


def upload_cv_file(page: Page, cv_file_path: str) -> bool:
    try:
        file_inputs = page.locator("input[type='file']")
        if file_inputs.count() == 0:
            return False
        for i in range(min(file_inputs.count(), 5)):
            inp = file_inputs.nth(i)
            blob = field_blob_from_element(get_field_attrs(inp))
            key = match_field_key(blob)
            if key == "cv_file" or i == 0:
                inp.set_input_files(cv_file_path)
                page.wait_for_timeout(800)
                return True
        return False
    except Exception:
        return False


def fill_cover_letter(page: Page, cover_letter: str | None) -> bool:
    if not cover_letter:
        return False
    try:
        textareas = page.locator("textarea:visible")
        for i in range(min(textareas.count(), 10)):
            field = textareas.nth(i)
            attrs = get_field_attrs(field)
            label = find_label_for(page, field)
            blob = field_blob_from_element(attrs, label)
            key = match_field_key(blob)
            if key == "cover_letter" or "cover" in blob.lower() or "מכתב" in blob:
                current = (field.input_value() or "").strip()
                if not current:
                    field.fill(cover_letter[:8000])
                    return True
    except Exception:
        pass
    return False


def page_has_application_form(page: Page) -> bool:
    """True when the page has a plausible job application form."""
    try:
        if page.locator("input[type='file']").count() > 0:
            return True
        inputs = page.locator(
            "input:visible:not([type='hidden']):not([type='submit']):not([type='button'])"
        )
        textareas = page.locator("textarea:visible")
        return inputs.count() >= 2 or textareas.count() >= 1
    except Exception:
        return False


def open_application_page(
    page: Page,
    apply_texts: list[str],
    *,
    selectors: list[str] | None = None,
    wait_ms: int = 3000,
) -> Page | None:
    """Click an apply button and return the page that contains the application form.

    Handles same-tab navigation and new-tab popups.
    """
    context = page.context
    start_url = page.url

    def _try_click(target: Page) -> bool:
        return click_apply_entry(target, apply_texts, selectors)

    # Attempt 1: apply opens a new tab.
    try:
        with context.expect_page(timeout=8000) as popup_info:
            if not _try_click(page):
                return None
        new_page = popup_info.value
        new_page.wait_for_load_state("domcontentloaded", timeout=30000)
        new_page.wait_for_timeout(wait_ms)
        return new_page
    except Exception:
        # Same-tab navigation during the popup wait is common.
        if page.url != start_url or page_has_application_form(page):
            page.wait_for_timeout(wait_ms)
            return page

    # Attempt 2: same-tab navigation.
    if not _try_click(page):
        if page_has_application_form(page):
            return page
        return None
    page.wait_for_timeout(wait_ms)
    try:
        page.wait_for_function(
            "(start) => window.location.href !== start",
            start_url,
            timeout=15000,
        )
    except Exception:
        pass
    if page.url != start_url or page_has_application_form(page):
        return page

    return page if page_has_application_form(page) else None


def click_apply_entry(page: Page, texts: list[str], selectors: list[str] | None = None) -> bool:
    for selector in selectors or []:
        try:
            loc = page.locator(selector).first
            if loc.count() and loc.is_visible():
                loc.click()
                page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    for text in texts:
        for role in ("button", "link"):
            try:
                loc = page.get_by_role(role, name=re.compile(re.escape(text), re.I)).first
                if loc.count() and loc.is_visible():
                    loc.click()
                    page.wait_for_timeout(1500)
                    return True
            except Exception:
                pass
        try:
            loc = page.get_by_text(text, exact=False).first
            if loc.count() and loc.is_visible():
                loc.click()
                page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


def click_submit(page: Page, texts: list[str]) -> bool:
    for text in texts:
        try:
            loc = page.get_by_role("button", name=re.compile(re.escape(text), re.I)).first
            if loc.count() and loc.is_visible():
                loc.click()
                page.wait_for_timeout(2000)
                return True
        except Exception:
            pass
    try:
        loc = page.locator("button[type='submit']:visible, input[type='submit']:visible").first
        if loc.count() and loc.is_visible():
            loc.click()
            page.wait_for_timeout(2000)
            return True
    except Exception:
        pass
    return False


def validate_form(page: Page, expected_cv_attached: bool = True) -> tuple[bool, list[str], bool]:
    errors: list[str] = []
    cv_attached = False
    file_input_count = 0

    try:
        required = page.locator(
            "input:required:visible, textarea:required:visible, select:required:visible"
        )
        for i in range(required.count()):
            field = required.nth(i)
            value = (field.input_value() or "").strip()
            if not value:
                attrs = get_field_attrs(field)
                label = find_label_for(page, field)
                key = match_field_key(field_blob_from_element(attrs, label)) or "required_field"
                errors.append(key)
    except Exception:
        pass

    try:
        file_inputs = page.locator("input[type='file']")
        file_input_count = file_inputs.count()
        for i in range(file_input_count):
            files = file_inputs.nth(i).evaluate(
                "el => el.files ? Array.from(el.files).map(f => f.name) : []"
            )
            if files:
                cv_attached = True
                break
    except Exception:
        pass

    if expected_cv_attached and file_input_count > 0 and not cv_attached:
        errors.append("cv_file")

    return len(errors) == 0, errors, cv_attached


def hebrew_failure_message(category: str | None, default: str = "") -> str:
    messages = {
        "job_page_unavailable": "לא ניתן לטעון את עמוד המשרה. ייתכן שהמשרה הוסרה או שהאתר אינו זמין.",
        "application_form_not_found": "המערכת לא מצאה טופס הגשת מועמדות במשרה הזו.",
        "unsupported_provider": "מערכת הגשת המועמדות באתר זה אינה נתמכת כרגע. ניתן להמשיך ידנית.",
        "required_field_missing": "שדות חובה לא מולאו בטופס. יש להשלים ידנית.",
        "cv_upload_failed": "לא ניתן היה להעלות את קובץ קורות החיים.",
        "form_validation_failed": "הטופס לא עבר אימות לפני השליחה.",
        "captcha_detected": "האתר דורש אימות CAPTCHA ולכן לא ניתן להשלים את ההגשה אוטומטית.",
        "login_required": "האתר דורש התחברות. יש לפתוח את העמוד ולהמשיך ידנית.",
        "user_action_required": "נדרשת פעולה ידנית כדי להשלים את ההגשה.",
        "submission_confirmation_not_found": (
            "קורות החיים הועלו, אך לא נמצאה הודעת אישור להגשה. יש לבדוק את העמוד ידנית."
        ),
        "website_blocked_automation": "האתר חסם גישה אוטומטית. יש להשלים את ההגשה ידנית.",
        "network_error": "שגיאת רשת בעת טעינת עמוד המשרה.",
        "unexpected_error": "אירעה שגיאה בלתי צפויה במהלך ההגשה.",
    }
    return messages.get(category or "", default or messages["unexpected_error"])
