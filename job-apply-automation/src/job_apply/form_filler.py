"""Detect, fill, and submit job application forms."""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import Locator, Page

from job_apply.fields import (
    build_profile_values,
    field_blob_from_element,
    match_field_key,
)

APPLY_TEXTS = [
    "apply now",
    "apply for this job",
    "submit application",
    "easy apply",
    "הגש מועמדות",
    "הגשת מועמדות",
    "שלח קורות חיים",
    "הגש קו\"ח",
    "הגש קוח",
]

SUBMIT_TEXTS = [
    "submit application",
    "submit",
    "send application",
    "apply for this job",
    "apply",
    "שליחה",
    "שלח",
    "הגשה",
    "אישור ושליחה",
    "שלח קורות חיים",
]

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

LOGIN_MARKERS = (
    "sign in",
    "log in",
    "login",
    "התחברות",
    "כניסה",
)


def page_text(page: Page, limit: int = 8000) -> str:
    try:
        return (page.evaluate("() => document.body.innerText || ''") or "")[:limit]
    except Exception:
        return ""


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


def detect_captcha(page: Page) -> bool:
    selectors = (
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        ".g-recaptcha:visible",
        ".h-captcha:visible",
        "#cf-turnstile:visible",
    )
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                return True
        except Exception:
            continue
    visible = page_text(page).lower()
    phrases = (
        "verify you are human",
        "complete the captcha",
        "please complete the security check",
        "אימות שאינך רובוט",
    )
    return any(phrase in visible for phrase in phrases)


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
            if page.locator(
                "input[type='email']:visible, input[type='password']:visible"
            ).count() > 0:
                return True
        except Exception:
            pass
    return False


def detect_submission_success(page: Page) -> tuple[bool, str]:
    text = page_text(page)
    lowered = text.lower()
    for marker in SUCCESS_MARKERS:
        if marker.lower() in lowered:
            idx = lowered.find(marker.lower())
            snippet = text[max(0, idx - 20) : idx + len(marker) + 40].strip()
            return True, snippet[:200]
    url = (page.url or "").lower()
    if any(token in url for token in ("/thank", "/confirmation", "/success", "/applied")):
        return True, f"Redirected to success URL: {page.url}"
    return False, ""


def page_has_application_form(page: Page) -> bool:
    """True when a visible application form is present (ignore hidden templates)."""
    try:
        visible_files = page.locator("input[type='file']:visible")
        if visible_files.count() > 0:
            return True
        inputs = page.locator(
            "input:visible:not([type='hidden']):not([type='submit']):not([type='button']):not([type='file'])"
        )
        textareas = page.locator("textarea:visible")
        # Need at least two visible contact-like fields, or a textarea.
        return inputs.count() >= 2 or textareas.count() >= 1
    except Exception:
        return False


def click_by_texts(page: Page, texts: list[str], *, roles: tuple[str, ...] = ("button", "link")) -> bool:
    for text in texts:
        for role in roles:
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


def click_apply_entry(page: Page) -> bool:
    return click_by_texts(page, APPLY_TEXTS)


def click_submit(page: Page) -> bool:
    if click_by_texts(page, SUBMIT_TEXTS, roles=("button",)):
        return True
    try:
        loc = page.locator("button[type='submit']:visible, input[type='submit']:visible").first
        if loc.count() and loc.is_visible():
            loc.click()
            page.wait_for_timeout(2000)
            return True
    except Exception:
        pass
    return False


def open_application_page(page: Page, *, wait_ms: int = 2500) -> Page:
    """If needed, click Apply and follow popup / same-tab navigation."""
    if page_has_application_form(page):
        return page

    context = page.context
    start_url = page.url
    try:
        with context.expect_page(timeout=8000) as popup_info:
            if not click_apply_entry(page):
                return page
        new_page = popup_info.value
        new_page.wait_for_load_state("domcontentloaded", timeout=30000)
        new_page.wait_for_timeout(wait_ms)
        return new_page
    except Exception:
        if page.url != start_url or page_has_application_form(page):
            page.wait_for_timeout(wait_ms)
            return page

    if not click_apply_entry(page):
        return page
    page.wait_for_timeout(wait_ms)
    return page


def fill_mapped_fields(
    page: Page,
    profile: dict[str, Any],
    *,
    max_fields: int = 40,
) -> tuple[list[str], list[str]]:
    values = build_profile_values(profile)
    filled: list[str] = []
    skipped: list[str] = []

    try:
        inputs = page.locator("input:visible, textarea:visible, select:visible")
        count = min(inputs.count(), max_fields)
    except Exception:
        return filled, skipped

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
            if key in used_keys:
                continue
            if key == "full_name" and ("first_name" in used_keys or "last_name" in used_keys):
                continue

            value = values.get(key, "")
            if not value:
                skipped.append(key)
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
                    skipped.append(key)
                    continue
            elif not current:
                field.fill(value)
            elif current != value:
                continue

            filled.append(key)
            used_keys.add(key)
        except Exception:
            skipped.append(f"field_{i}")
            continue

    return filled, skipped


def upload_cv_file(page: Page, cv_file_path: str) -> bool:
    try:
        # Prefer visible inputs; fall back to any file input inside a visible form.
        file_inputs = page.locator("input[type='file']:visible")
        if file_inputs.count() == 0:
            file_inputs = page.locator("form:visible input[type='file']")
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


def validate_required(page: Page) -> list[str]:
    errors: list[str] = []
    try:
        required = page.locator(
            "input:required:visible, textarea:required:visible, select:required:visible"
        )
        for i in range(required.count()):
            field = required.nth(i)
            input_type = (field.get_attribute("type") or "").lower()
            if input_type == "file":
                files = field.evaluate(
                    "el => el.files ? Array.from(el.files).map(f => f.name) : []"
                )
                if not files:
                    errors.append("cv_file")
                continue
            value = (field.input_value() or "").strip()
            if not value:
                attrs = get_field_attrs(field)
                label = find_label_for(page, field)
                key = match_field_key(field_blob_from_element(attrs, label)) or "required_field"
                errors.append(key)
    except Exception:
        pass
    return errors
