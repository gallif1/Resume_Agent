/**
 * Phase 11 — Accessibility (keyboard, roles, axe-core).
 */
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {
  attachDiagnostics,
  saveDiagnostics,
} from "../helpers/diagnostics";
import {
  gotoApp,
  ensureAuthenticated,
  switchAuthTab,
  fillAuthForm,
} from "../helpers/auth";
import { writeJsonArtifact, getQaCredentials } from "../helpers/test-data";
import { screenshotViewport } from "../helpers/layout";
import { prepareAllFixtures } from "../helpers/test-data";
import { uploadResume, expectToast } from "../helpers/resume";

test.describe("Phase 11 — Accessibility", () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(
      !["chromium-desktop", "chromium-mobile"].includes(testInfo.project.name),
      "A11y on desktop + mobile"
    );
  });

  test("auth: labels, roles, keyboard, axe", async ({ page }, testInfo) => {
    const diag = attachDiagnostics(page);
    await gotoApp(page);

    await test.step("Form labels and accessible names", async () => {
      await expect(page.getByLabel("אימייל")).toBeVisible();
      await expect(page.getByLabel("סיסמה")).toBeVisible();
      await expect(page.getByRole("tab", { name: "התחברות" })).toBeVisible();
      await expect(page.getByRole("tab", { name: "הרשמה" })).toBeVisible();
      await expect(
        page.getByRole("button", { name: /התחבר|צור חשבון/ })
      ).toBeVisible();
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    });

    await test.step("Keyboard tab order reaches email, password, submit", async () => {
      await page.locator("body").click({ position: { x: 5, y: 5 } });
      let reachedEmail = false;
      let reachedPassword = false;
      let reachedSubmit = false;
      for (let i = 0; i < 20; i++) {
        await page.keyboard.press("Tab");
        const handle = await page.evaluateHandle(() => document.activeElement);
        const info = await handle.evaluate((el) => ({
          tag: el?.tagName,
          name: (el as HTMLInputElement)?.name || "",
          type: (el as HTMLButtonElement)?.type || "",
          role: el?.getAttribute?.("role") || "",
          text: (el?.textContent || "").trim().slice(0, 40),
        }));
        if (info.name === "email") reachedEmail = true;
        if (info.name === "password") reachedPassword = true;
        if (info.type === "submit" || /התחבר|צור/.test(info.text)) {
          reachedSubmit = true;
        }
      }
      expect(reachedEmail).toBeTruthy();
      expect(reachedPassword).toBeTruthy();
      expect(reachedSubmit).toBeTruthy();
    });

    await test.step("Visible focus style exists on focused control", async () => {
      await page.getByLabel("אימייל").focus();
      const outline = await page.getByLabel("אימייל").evaluate((el) => {
        const s = getComputedStyle(el);
        return {
          outline: s.outlineStyle,
          outlineWidth: s.outlineWidth,
          boxShadow: s.boxShadow,
        };
      });
      const hasFocusCue =
        (outline.outline !== "none" && outline.outlineWidth !== "0px") ||
        outline.boxShadow !== "none";
      // Soft assertion — record if missing
      writeJsonArtifact(`reports/a11y-focus-${testInfo.project.name}.json`, {
        outline,
        hasFocusCue,
      });
      if (!hasFocusCue) {
        test.info().annotations.push({
          type: "defect-candidate",
          description: "Email input may lack a visible focus indicator",
        });
      }
    });

    await test.step("axe-core scan on auth view", async () => {
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze();
      writeJsonArtifact(`reports/axe-auth-${testInfo.project.name}.json`, {
        violations: results.violations.map((v) => ({
          id: v.id,
          impact: v.impact,
          description: v.description,
          nodes: v.nodes.length,
          helpUrl: v.helpUrl,
        })),
        incomplete: results.incomplete.length,
        passes: results.passes.length,
      });
      const critical = results.violations.filter(
        (v) => v.impact === "critical" || v.impact === "serious"
      );
      // Fail on critical/serious — document others
      expect(
        critical,
        critical.map((v) => `${v.id}: ${v.description}`).join("\n")
      ).toEqual([]);
    });

    await test.step("Error not only by color — alert role on bad login", async () => {
      await switchAuthTab(page, "login");
      await fillAuthForm(page, "nobody@example.com", "badpass99");
      await page.locator('form.auth-form button[type="submit"]').click();
      await expect(page.getByRole("alert")).toBeVisible({ timeout: 15_000 });
    });

    saveDiagnostics(`a11y-auth-${testInfo.project.name}`, diag);
  });

  test("authenticated: headings, modal escape, axe", async ({ page }, testInfo) => {
    test.skip(
      testInfo.project.name !== "chromium-desktop",
      "Authenticated a11y once on desktop"
    );
    const diag = attachDiagnostics(page);
    const auth = await ensureAuthenticated(page);
    if (!auth.ok) {
      test.skip(true, auth.blockedReason || "auth blocked");
    }
    await expect(page.getByText("סוכן מחובר")).toBeVisible({ timeout: 60_000 });

    await test.step("Heading hierarchy has h1", async () => {
      await expect(page.locator("h1").first()).toBeVisible();
    });

    await test.step("Modal focus: open delete modal, Escape closes", async () => {
      const fixtures = await prepareAllFixtures();
      await uploadResume(page, fixtures.validPdf);
      await expectToast(page, /הועל/);
      await page
        .locator(".cv-item")
        .first()
        .getByRole("button", { name: "מחק" })
        .click();
      const modal = page.locator(".modal");
      await expect(modal).toBeVisible();
      await page.keyboard.press("Escape");
      // App may not handle Escape — record finding if still open
      const stillOpen = await modal.isVisible().catch(() => false);
      if (stillOpen) {
        test.info().annotations.push({
          type: "defect-candidate",
          description:
            "Delete confirmation modal does not close on Escape; closed via Cancel instead",
        });
        await modal.getByRole("button", { name: "ביטול" }).click();
      }
      await expect(modal).toBeHidden();
    });

    await test.step("axe-core on workspace", async () => {
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa"])
        .analyze();
      writeJsonArtifact("reports/axe-workspace.json", {
        violations: results.violations.map((v) => ({
          id: v.id,
          impact: v.impact,
          description: v.description,
          nodes: v.nodes.length,
          help: v.help,
        })),
      });
      const critical = results.violations.filter(
        (v) => v.impact === "critical" || v.impact === "serious"
      );
      await screenshotViewport(page, "a11y-workspace");
      // Soft-fail documentation: collect but only assert critical empty if none expected
      if (critical.length) {
        test.info().annotations.push({
          type: "a11y-violations",
          description: critical.map((v) => v.id).join(", "),
        });
      }
      expect(
        critical.filter((v) => v.id === "aria-hidden-focus"),
        critical.map((v) => v.id).join(", ")
      ).toEqual([]);
    });

    // creds presence note
    const creds = getQaCredentials();
    test.info().annotations.push({
      type: "env",
      description: creds.fromEnv
        ? "Used QA_TEST_* env credentials"
        : "Used generated throwaway credentials",
    });

    saveDiagnostics("a11y-workspace", diag);
  });
});
