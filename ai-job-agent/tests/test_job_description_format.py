"""Tests for structured HTML → job-description text conversion."""

from __future__ import annotations

from bs4 import BeautifulSoup

from enrich_jobs import html_to_structured_text


def test_html_to_structured_text_preserves_paragraphs_and_bullets():
    html = """
    <div class="show-more-less-html__markup">
      <p>We are looking for a Software Engineer to join our Services team.</p>
      <p>You'll work directly with customers and ship solutions.</p>
      <h3>Requirements</h3>
      <ul>
        <li>Python experience</li>
        <li>Salesforce knowledge</li>
      </ul>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    text = html_to_structured_text(soup.select_one("div"))

    assert "Services team." in text
    assert "You'll work directly" in text
    assert "Requirements" in text
    assert "• Python experience" in text
    assert "• Salesforce knowledge" in text
    # Paragraphs should not be glued into one line.
    assert "\n" in text
    services_idx = text.index("Services team.")
    youll_idx = text.index("You'll work directly")
    assert youll_idx > services_idx


def test_html_to_structured_text_collapses_excess_blank_lines():
    html = "<div><p>One</p><p></p><p></p><p>Two</p></div>"
    soup = BeautifulSoup(html, "html.parser")
    text = html_to_structured_text(soup.div)
    assert text == "One\n\nTwo"
