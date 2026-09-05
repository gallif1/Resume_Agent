"""Elbit-style form: full name + privacy checkbox gates submit."""

from __future__ import annotations

from pathlib import Path

from job_apply.fields import match_field_key
from job_apply.form_filler import SUBMIT_TEXTS, accept_consent_checkboxes, click_submit
from playwright.sync_api import sync_playwright

FIXTURE = """<!doctype html>
<html lang="he" dir="rtl">
<body>
  <form id="f">
    <label>שם מלא *<input id="name" required /></label>
    <label>מספר טלפון *<input id="phone" type="tel" required /></label>
    <label>אימייל *<input id="email" type="email" required /></label>
    <label><input id="privacy" type="checkbox" /> מדיניות פרטיות</label>
    <button id="submit" type="submit" disabled>הגש מועמדות</button>
  </form>
  <script>
    const sync = () => {
      const ok = ['name','phone','email'].every(id => document.getElementById(id).value.trim())
        && document.getElementById('privacy').checked;
      document.getElementById('submit').disabled = !ok;
    };
    document.querySelectorAll('input').forEach(el => el.addEventListener('input', sync));
    document.getElementById('privacy').addEventListener('change', sync);
    document.getElementById('f').addEventListener('submit', e => {
      e.preventDefault();
      document.body.dataset.submitted = '1';
    });
  </script>
</body>
</html>
"""


def test_full_name_label_maps():
    assert match_field_key("שם מלא") == "full_name"
    assert "הגש מועמדות" in SUBMIT_TEXTS


def test_elbit_style_consent_enables_submit(tmp_path: Path):
    html = tmp_path / "elbit.html"
    html.write_text(FIXTURE, encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.goto(html.as_uri())
        page.fill("#name", "Gal Test")
        page.fill("#phone", "0523527293")
        page.fill("#email", "gal@example.com")
        assert page.locator("#submit").is_disabled()
        assert accept_consent_checkboxes(page) >= 1
        assert page.locator("#privacy").is_checked()
        assert not page.locator("#submit").is_disabled()
        assert click_submit(page)
        assert page.evaluate("() => document.body.dataset.submitted") == "1"
        browser.close()
