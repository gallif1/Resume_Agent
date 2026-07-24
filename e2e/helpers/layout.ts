/**
 * Responsive / RTL / layout assertion helpers.
 */
import type { Page } from "@playwright/test";
import fs from "fs";
import path from "path";
import { ARTIFACTS_DIR, ensureDirs } from "./test-data";

export type LayoutIssue = {
  type: string;
  detail: string;
  selector?: string;
};

export async function collectLayoutIssues(page: Page): Promise<LayoutIssue[]> {
  return page.evaluate(() => {
    const issues: { type: string; detail: string; selector?: string }[] = [];
    const docWidth = document.documentElement.clientWidth;

    // Horizontal overflow of the document
    if (document.documentElement.scrollWidth > docWidth + 2) {
      issues.push({
        type: "horizontal-overflow",
        detail: `document.scrollWidth=${document.documentElement.scrollWidth} > clientWidth=${docWidth}`,
      });
    }

    const candidates = Array.from(
      document.querySelectorAll(
        "button, a, input, select, .btn, .modal, .cv-item, .dropzone, .auth-panel"
      )
    );

    for (const el of candidates) {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      if (rect.right > docWidth + 4) {
        issues.push({
          type: "element-overflows-viewport",
          detail: `right=${Math.round(rect.right)} > viewport=${docWidth}`,
          selector:
            el.tagName.toLowerCase() +
            (el.className ? `.${String(el.className).split(" ").join(".")}` : ""),
        });
      }
      // Touch targets on narrow viewports
      if (docWidth <= 768 && el.matches("button, a.btn, .btn")) {
        if (rect.height > 0 && rect.height < 36) {
          issues.push({
            type: "small-touch-target",
            detail: `height=${Math.round(rect.height)}px`,
            selector: el.tagName.toLowerCase() + (el.textContent || "").slice(0, 40),
          });
        }
      }
    }

    const modal = document.querySelector(".modal");
    if (modal) {
      const r = modal.getBoundingClientRect();
      if (r.height > window.innerHeight + 4) {
        issues.push({
          type: "modal-taller-than-viewport",
          detail: `modalHeight=${Math.round(r.height)} viewport=${window.innerHeight}`,
        });
      }
    }

    // Untranslated-looking keys (snake_case tokens in visible text)
    const bodyText = document.body?.innerText || "";
    const keyHits = bodyText.match(/\b[a-z]+_[a-z0-9_]+\b/g) || [];
    const suspicious = [...new Set(keyHits)].filter(
      (k) =>
        !["match_id", "job_id", "cv_id"].includes(k) &&
        /_(status|error|count|name|label)/.test(k)
    );
    for (const k of suspicious.slice(0, 10)) {
      issues.push({ type: "possible-untranslated-key", detail: k });
    }

    return issues;
  });
}

export async function screenshotViewport(
  page: Page,
  name: string
): Promise<string> {
  ensureDirs();
  const file = path.join(ARTIFACTS_DIR, "screenshots", `${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

export function saveLayoutReport(name: string, issues: LayoutIssue[]) {
  ensureDirs();
  const file = path.join(ARTIFACTS_DIR, "reports", `${name}-layout.json`);
  fs.writeFileSync(file, JSON.stringify(issues, null, 2), "utf8");
  return file;
}
