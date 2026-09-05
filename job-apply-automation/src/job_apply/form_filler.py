"""Detect, fill, and submit job application forms."""

from __future__ import annotations

import re
import time
from typing import Any

from playwright.sync_api import Frame, Locator, Page

from job_apply.fields import (
    build_profile_values,
    field_blob_from_element,
    match_field_key,
)

# Comeet and similar boards host the form inside an iframe.
FormTarget = Page | Frame

APPLY_TEXTS = [
    "apply for this job",
    "apply now",
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
    "thanks for applying",
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


def _host_page(target: FormTarget) -> Page:
    return target if isinstance(target, Page) else target.page


def target_text(target: FormTarget, limit: int = 8000) -> str:
    try:
        return (target.evaluate("() => document.body.innerText || ''") or "")[:limit]
    except Exception:
        return ""


def page_text(page: Page, limit: int = 8000) -> str:
    return target_text(page, limit=limit)


def get_field_attrs(locator: Locator) -> dict[str, str | None]:
    attrs: dict[str, str | None] = {}
    for name in ("name", "id", "placeholder", "aria-label", "type", "autocomplete"):
        try:
            attrs[name] = locator.get_attribute(name)
        except Exception:
            attrs[name] = None
    return attrs


def find_label_for(target: FormTarget, locator: Locator) -> str:
    try:
        field_id = locator.get_attribute("id")
        if field_id:
            label = target.locator(f"label[for='{field_id}']").first
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


def detect_captcha(target: FormTarget) -> bool:
    """True only for an active CAPTCHA challenge — not the idle reCAPTCHA badge."""
    challenge_selectors = (
        "iframe[src*='recaptcha/api2/bframe']",
        "iframe[src*='hcaptcha.com'][src*='frame=challenge']",
        "iframe[title*='recaptcha challenge' i]",
        "iframe[title*='hCaptcha challenge' i]",
    )
    for selector in challenge_selectors:
        try:
            locator = target.locator(selector).first
            if locator.count() and locator.is_visible():
                return True
        except Exception:
            continue

    if isinstance(target, Page):
        for frame in target.frames:
            url = (frame.url or "").lower()
            if "recaptcha/api2/bframe" in url:
                return True
            if "hcaptcha.com" in url and "challenge" in url:
                return True

    visible = target_text(target).lower()
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
            if (
                page.locator(
                    "input[type='email']:visible, input[type='password']:visible"
                ).count()
                > 0
            ):
                return True
        except Exception:
            pass
    return False


def detect_submission_success(target: FormTarget) -> tuple[bool, str]:
    text = target_text(target)
    lowered = text.lower()
    for marker in SUCCESS_MARKERS:
        if marker.lower() in lowered:
            idx = lowered.find(marker.lower())
            snippet = text[max(0, idx - 20) : idx + len(marker) + 40].strip()
            return True, snippet[:200]
    if isinstance(target, Page):
        url = (target.url or "").lower()
        if any(token in url for token in ("/thank", "/confirmation", "/success", "/applied")):
            return True, f"Redirected to success URL: {target.url}"
        for frame in target.frames:
            if frame == target.main_frame:
                continue
            ok, snippet = detect_submission_success(frame)
            if ok:
                return True, snippet
    return False, ""


def target_has_application_form(target: FormTarget) -> bool:
    try:
        text_inputs = target.locator(
            "input:visible:not([type='hidden']):not([type='submit']):not([type='button']):not([type='file'])"
        )
        textareas = target.locator("textarea:visible")
        file_inputs = target.locator("input[type='file']")
        if text_inputs.count() >= 2 and file_inputs.count() > 0:
            return True
        if text_inputs.count() >= 2 or textareas.count() >= 1:
            return True
        if file_inputs.count() > 0 and text_inputs.count() >= 1:
            return True
        return False
    except Exception:
        return False


def page_has_application_form(page: Page) -> bool:
    if target_has_application_form(page):
        return True
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        url = (frame.url or "").lower()
        if "recaptcha" in url or "hcaptcha" in url:
            continue
        try:
            if target_has_application_form(frame):
                return True
        except Exception:
            continue
    return False


def find_form_target(page: Page, *, wait_ms: int = 12000) -> FormTarget:
    """Return the page or iframe that contains the application form."""
    deadline = time.monotonic() + (wait_ms / 1000.0)
    while True:
        if target_has_application_form(page):
            return page
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            url = (frame.url or "").lower()
            if "recaptcha" in url or "hcaptcha" in url:
                continue
            try:
                if "/apply" in url or "comeet" in url or target_has_application_form(frame):
                    if target_has_application_form(frame):
                        return frame
            except Exception:
                continue
        if time.monotonic() >= deadline:
            break
        page.wait_for_timeout(400)
    return page


def click_by_texts(
    target: FormTarget,
    texts: list[str],
    *,
    roles: tuple[str, ...] = ("button", "link"),
) -> bool:
    host = _host_page(target)
    for text in texts:
        for role in roles:
            try:
                loc = target.get_by_role(role, name=re.compile(re.escape(text), re.I)).first
                if loc.count() and loc.is_visible():
                    loc.click()
                    host.wait_for_timeout(1500)
                    return True
            except Exception:
                pass
        try:
            loc = target.get_by_text(re.compile(text, re.I)).first
            if loc.count() and loc.is_visible():
                loc.click()
                host.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


def click_apply_entry(page: Page) -> bool:
    return click_by_texts(page, APPLY_TEXTS)


def click_submit(target: FormTarget) -> bool:
    if click_by_texts(target, SUBMIT_TEXTS, roles=("button",)):
        return True
    try:
        loc = target.locator(
            "button[type='submit']:visible, input[type='submit']:visible"
        ).first
        if loc.count() and loc.is_visible():
            loc.click()
            _host_page(target).wait_for_timeout(2000)
            return True
    except Exception:
        pass
    return False


def open_application_page(page: Page, *, wait_ms: int = 3500) -> Page:
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
        find_form_target(new_page, wait_ms=max(wait_ms, 8000))
        return new_page
    except Exception:
        if page.url != start_url or page_has_application_form(page):
            page.wait_for_timeout(wait_ms)
            return page

    if not click_apply_entry(page):
        return page
    page.wait_for_timeout(wait_ms)
    find_form_target(page, wait_ms=max(wait_ms, 8000))
    return page


def fill_mapped_fields(
    target: FormTarget,
    profile: dict[str, Any],
    *,
    max_fields: int = 40,
) -> tuple[list[str], list[str]]:
    values = build_profile_values(profile)
    filled: list[str] = []
    skipped: list[str] = []

    try:
        inputs = target.locator("input:visible, textarea:visible, select:visible")
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

            label_text = find_label_for(target, field)
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

    _maybe_select_israel_dial_code(target, values.get("phone", ""))
    return filled, skipped


def _maybe_select_israel_dial_code(target: FormTarget, phone: str) -> None:
    normalized = re.sub(r"\D", "", phone or "")
    if not (
        normalized.startswith("972")
        or normalized.startswith("05")
        or (len(normalized) == 9 and normalized.startswith("5"))
    ):
        return
    try:
        btn = target.get_by_role("button", name=re.compile(r"\+\d+")).first
        if not (btn.count() and btn.is_visible()):
            return
        btn.click()
        _host_page(target).wait_for_timeout(600)
        option = target.get_by_text(re.compile(r"Israel|\+972|ישראל"), exact=False).first
        if option.count() and option.is_visible():
            option.click()
            _host_page(target).wait_for_timeout(400)
    except Exception:
        pass


def upload_cv_file(target: FormTarget, cv_file_path: str) -> bool:
    try:
        file_inputs = target.locator("input[type='file']")
        if file_inputs.count() == 0:
            return False
        for i in range(min(file_inputs.count(), 5)):
            inp = file_inputs.nth(i)
            name = (inp.get_attribute("name") or "").lower()
            aria = (inp.get_attribute("aria-label") or "").lower()
            if name in {"coverletter", "portfolio"} or "cover" in aria or "portfolio" in aria:
                continue
            blob = field_blob_from_element(get_field_attrs(inp))
            key = match_field_key(blob)
            if key == "cv_file" or name in {"cv", "resume", ""} or i == 0:
                inp.set_input_files(cv_file_path)
                _host_page(target).wait_for_timeout(800)
                return True
        return False
    except Exception:
        return False


def validate_required(target: FormTarget) -> list[str]:
    errors: list[str] = []
    try:
        required = target.locator(
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
                label = find_label_for(target, field)
                key = match_field_key(field_blob_from_element(attrs, label)) or "required_field"
                errors.append(key)
    except Exception:
        pass

    try:
        cv_input = target.locator("input[type='file'][name='cv'], input#cv").first
        if cv_input.count():
            files = cv_input.evaluate(
                "el => el.files ? Array.from(el.files).map(f => f.name) : []"
            )
            if not files and "cv_file" not in errors:
                body = target_text(target).lower()
                if "resume" in body or "קורות" in body:
                    errors.append("cv_file")
    except Exception:
        pass
    return errors
