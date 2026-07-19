/**
 * Phase 9–10 — Responsive viewports + Hebrew RTL checks.
 * Each Playwright project supplies a different viewport (see playwright.config.ts).
 */
import { test, expect } from "@playwright/test";
import {
  attachDiagnostics,
  saveDiagnostics,
} from "../helpers/diagnostics";
import { gotoApp, ensureAuthenticated, expectAuthScreen } from "../helpers/auth";
import {
  collectLayoutIssues,
  screenshotViewport,
  saveLayoutReport,
} from "../helpers/layout";
import { prepareAllFixtures } from "../helpers/test-data";
import { uploadResume, cleanupTestResumes } from "../helpers/resume";

test.describe("Phase 9 — Responsive UI", () => {
  test("auth + workspace layout at current viewport", async ({ page }, testInfo) => {
    const project = testInfo.project.name;
    const diag = attachDiagnostics(page);
    await gotoApp(page);
    await expectAuthScreen(page);
    await screenshotViewport(page, `responsive-auth-${project}`);

    let issues = await collectLayoutIssues(page);
    const isNarrow = /mobile|tablet/.test(project);
    if (!isNarrow) {
      issues = issues.filter((i) => i.type !== "small-touch-target");
    }
    saveLayoutReport(`responsive-auth-${project}`, issues);

    const overflow = issues.filter((i) =>
      ["horizontal-overflow", "element-overflows-viewport", "modal-taller-than-viewport"].includes(
        i.type
      )
    );
    expect(overflow, JSON.stringify(overflow, null, 2)).toEqual([]);

    const auth = await ensureAuthenticated(page);
    if (auth.ok) {
      await expect(page.getByText("סוכן מחובר")).toBeVisible({
        timeout: 60_000,
      });
      await screenshotViewport(page, `responsive-app-${project}`);
      let appIssues = await collectLayoutIssues(page);
      if (!isNarrow) {
        appIssues = appIssues.filter((i) => i.type !== "small-touch-target");
      }
      saveLayoutReport(`responsive-app-${project}`, appIssues);
      const appOverflow = appIssues.filter((i) =>
        [
          "horizontal-overflow",
          "element-overflows-viewport",
          "modal-taller-than-viewport",
        ].includes(i.type)
      );
      expect(appOverflow, JSON.stringify(appOverflow, null, 2)).toEqual([]);

      const fixtures = await prepareAllFixtures();
      const input = page.locator('input[type="file"]');
      if (await input.count()) {
        await uploadResume(page, fixtures.validPdf).catch(() => {});
      }
      const item = page.locator(".cv-item").first();
      if (await item.isVisible().catch(() => false)) {
        await item.getByRole("button", { name: "מחק" }).click();
        await expect(page.locator(".modal")).toBeVisible();
        await screenshotViewport(page, `responsive-modal-${project}`);
        const modalIssues = await collectLayoutIssues(page);
        saveLayoutReport(`responsive-modal-${project}`, modalIssues);
        await page.locator(".modal").getByRole("button", { name: "ביטול" }).click();
      }
      if (project === "chromium-desktop") {
        await cleanupTestResumes(page);
      }
    } else {
      test.info().annotations.push({
        type: "blocked",
        description: auth.blockedReason || "auth unavailable for workspace layout",
      });
    }

    saveDiagnostics(`responsive-${project}`, diag);
    expect(diag.pageErrors).toEqual([]);
  });
});

test.describe("Phase 10 — Hebrew RTL", () => {
  test("RTL direction, alignment, mixed content, no raw i18n keys on auth", async ({
    page,
  }, testInfo) => {
    test.skip(
      testInfo.project.name !== "chromium-desktop",
      "RTL deep check on desktop"
    );
    const diag = attachDiagnostics(page);
    await gotoApp(page);

    await test.step("html dir/lang", async () => {
      await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
      await expect(page.locator("html")).toHaveAttribute("lang", "he");
    });

    await test.step("Auth form direction", async () => {
      await expect(page.locator("form.auth-form")).toHaveAttribute("dir", "rtl");
      await expect(page.getByLabel("אימייל")).toHaveAttribute("dir", "ltr");
      await expect(page.getByLabel("סיסמה")).toHaveAttribute("dir", "ltr");
    });

    await test.step("Hebrew labels present; brand English ok", async () => {
      await expect(page.getByText("התחברות")).toBeVisible();
      await expect(page.getByText("הרשמה")).toBeVisible();
      await expect(page.getByText("אימייל")).toBeVisible();
      await expect(page.getByText("סיסמה")).toBeVisible();
      await expect(page.locator(".logo-text")).toContainText("Resume");
    });

    await test.step("No obvious untranslated keys on auth screen", async () => {
      const text = await page.locator(".auth-view").innerText();
      expect(text).not.toMatch(/\bauth\.[a-z_]+\b/);
      expect(text).not.toMatch(/\blogin_[a-z]+\b/);
    });

    const auth = await ensureAuthenticated(page);
    if (auth.ok) {
      await expect(page.getByText("סוכן מחובר")).toBeVisible({ timeout: 60_000 });
      await expect(page.getByRole("button", { name: "התנתק" })).toBeVisible();
      const body = await page.locator("main").innerText();
      expect(body).toMatch(/קורות|משרה|סריק/);
      await expect(page.locator(".logo-text")).toBeVisible();
      await screenshotViewport(page, "rtl-authenticated");
    }

    saveDiagnostics("rtl", diag);
  });
});
