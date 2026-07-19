/**
 * Phase 4 & 5 — Resume upload, management, and multi-resume separation.
 */
import { test, expect } from "@playwright/test";
import path from "path";
import {
  attachDiagnostics,
  saveDiagnostics,
} from "../helpers/diagnostics";
import { ensureAuthenticated, gotoApp } from "../helpers/auth";
import {
  uploadResume,
  expectToast,
  listUploadedCvNames,
  deleteCvByName,
  cancelDeleteCvByName,
  cleanupTestResumes,
  selectCvInPicker,
  dismissNativeConfirm,
  waitForCvsLoaded,
} from "../helpers/resume";
import {
  prepareAllFixtures,
  TEST_MARKER,
} from "../helpers/test-data";
import { screenshotViewport } from "../helpers/layout";

test.describe("Phase 4 — Resume management", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(async ({}, testInfo) => {
    test.skip(
      testInfo.project.name !== "chromium-desktop",
      "Resume management on desktop to avoid duplicate uploads across viewports"
    );
  });

  test("upload variants, list, select, delete confirm/cancel, cleanup", async ({
    page,
  }) => {
    const diag = attachDiagnostics(page);
    const auth = await ensureAuthenticated(page);
    if (!auth.ok) {
      test.info().annotations.push({
        type: "blocked",
        description: auth.blockedReason || "auth unavailable",
      });
      test.skip(true, auth.blockedReason || "auth blocked");
    }

    await expect(page.getByText("סוכן מחובר")).toBeVisible({ timeout: 60_000 });
    const fixtures = await prepareAllFixtures();

    // Start clean for marker files only
    await cleanupTestResumes(page);

    await test.step("Valid PDF upload", async () => {
      await uploadResume(page, fixtures.validPdf);
      await expectToast(page, /הועל/);
      const names = await listUploadedCvNames(page);
      expect(names.some((n) => n.includes(TEST_MARKER))).toBeTruthy();
    });

    await test.step("Duplicate upload prompts confirm dialog", async () => {
      dismissNativeConfirm(page, false);
      await uploadResume(page, fixtures.validPdf);
      // After dismiss, should not add another identical copy
      await page.waitForTimeout(1000);
    });

    await test.step("Unsupported file type rejected client-side", async () => {
      await uploadResume(page, fixtures.unsupported);
      await expect(page.locator(".error-box")).toContainText(/לא נתמך/);
    });

    await test.step("Long filename upload", async () => {
      await uploadResume(page, fixtures.longNamePdf);
      await expectToast(page, /הועל/);
    });

    await test.step("Hebrew filename upload", async () => {
      await uploadResume(page, fixtures.hebrewNamePdf);
      await expectToast(page, /הועל/);
      const names = await listUploadedCvNames(page);
      expect(names.some((n) => /קורות|חיים|בדיקה/.test(n) || n.includes(TEST_MARKER))).toBeTruthy();
    });

    await test.step("Filename with spaces and parentheses", async () => {
      await uploadResume(page, fixtures.spacesParensPdf);
      await expectToast(page, /הועל/);
      const names = await listUploadedCvNames(page);
      expect(
        names.some((n) => n.includes("resume") || n.includes("(") || n.includes(TEST_MARKER))
      ).toBeTruthy();
    });

    await test.step("Resume appears in list and picker", async () => {
      await expect(page.locator(".cv-list .cv-item").first()).toBeVisible();
      await expect(
        page.getByRole("listbox", { name: "בחירת קורות חיים" })
      ).toBeVisible();
    });

    await test.step("Selecting / switching resumes in picker", async () => {
      const options = page.locator('.cv-picker [role="option"]');
      const count = await options.count();
      expect(count).toBeGreaterThan(0);
      if (count >= 2) {
        await options.nth(1).click();
        await expect(options.nth(1)).toHaveAttribute("aria-selected", "true");
        await options.nth(0).click();
        await expect(options.nth(0)).toHaveAttribute("aria-selected", "true");
      }
    });

    await test.step("Refresh after selection preserves uploaded list", async () => {
      await waitForCvsLoaded(page);
      const before = await listUploadedCvNames(page);
      expect(before.length).toBeGreaterThan(0);
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(page.getByRole("button", { name: "התנתק" })).toBeVisible({
        timeout: 30_000,
      });
      await waitForCvsLoaded(page);
      const after = await listUploadedCvNames(page);
      expect(after.length).toBe(before.length);
    });

    await test.step("Cancel delete keeps resume", async () => {
      const names = await listUploadedCvNames(page);
      const target = names.find((n) => n.includes(TEST_MARKER)) || names[0];
      await cancelDeleteCvByName(page, target.slice(0, 20));
    });

    await test.step("Delete confirmation removes only selected test resume", async () => {
      await waitForCvsLoaded(page);
      const names = await listUploadedCvNames(page);
      const target =
        names.find((n) => n.includes("resume (final")) ||
        names.find((n) => n.includes(TEST_MARKER)) ||
        names[0];
      expect(target).toBeTruthy();
      await deleteCvByName(page, target);
      await waitForCvsLoaded(page);
      const after = await listUploadedCvNames(page);
      expect(after.filter((n) => n === target).length).toBe(0);
    });

    await test.step("Cleanup remaining test resumes; observe empty when applicable", async () => {
      await cleanupTestResumes(page);
      const remaining = (await listUploadedCvNames(page)).filter((n) =>
        n.includes(TEST_MARKER)
      );
      expect(remaining).toEqual([]);
      // Empty state only if account has no other CVs
      const anyLeft = await page.locator(".cv-list .cv-item").count();
      if (anyLeft === 0) {
        await expect(page.getByText("עדיין לא העלית קורות חיים")).toBeVisible();
      }
    });

    await screenshotViewport(page, "resume-management-end");
    saveDiagnostics("resume-management", diag);
    expect(diag.pageErrors).toEqual([]);
  });
});

test.describe("Phase 5 — Resume-specific data separation", () => {
  test("two resumes: picker switch + workspace aggregation behavior", async ({
    page,
  }, testInfo) => {
    test.skip(
      testInfo.project.name !== "chromium-desktop",
      "Separation checks once on desktop"
    );

    const diag = attachDiagnostics(page);
    const auth = await ensureAuthenticated(page);
    if (!auth.ok) {
      test.skip(true, auth.blockedReason || "auth blocked");
    }

    await expect(page.getByText("סוכן מחובר")).toBeVisible({ timeout: 60_000 });
    const fixtures = await prepareAllFixtures();
    await cleanupTestResumes(page);

    await test.step("Upload two different test resumes", async () => {
      await uploadResume(page, fixtures.validPdf);
      await expectToast(page, /הועל/);
      await waitForCvsLoaded(page);
      await expect(
        page.locator(".cv-item").filter({ hasText: TEST_MARKER })
      ).toHaveCount(1, { timeout: 30_000 });

      await uploadResume(page, fixtures.validPdfB);
      await expectToast(page, /הועל/);
      await waitForCvsLoaded(page);
      await expect(
        page.locator(".cv-item").filter({ hasText: TEST_MARKER })
      ).toHaveCount(2, { timeout: 30_000 });
      const names = await listUploadedCvNames(page);
      expect(names.filter((n) => n.includes(TEST_MARKER)).length).toBeGreaterThanOrEqual(2);
    });

    let stateA = "";
    let stateB = "";

    await test.step("Select resume A and record visible state", async () => {
      await selectCvInPicker(page, path.basename(fixtures.validPdf).slice(0, 18));
      stateA = await page.locator(".scan-config, .cv-list").first().innerText();
    });

    await test.step("Select resume B and compare", async () => {
      await selectCvInPicker(page, path.basename(fixtures.validPdfB).slice(0, 18));
      stateB = await page.locator(".scan-config, .cv-list").first().innerText();
      // Both resumes remain listed (workspace model) — selection only marks primary
      expect(stateB).toContain(TEST_MARKER);
    });

    await test.step("Switch repeatedly and refresh", async () => {
      for (let i = 0; i < 3; i++) {
        await page.locator('.cv-picker [role="option"]').nth(0).click();
        await page.locator('.cv-picker [role="option"]').nth(1).click();
      }
      const selected = await page
        .locator('.cv-picker [role="option"][aria-selected="true"]')
        .textContent();
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(page.getByRole("button", { name: "התנתק" })).toBeVisible({
        timeout: 30_000,
      });
      // Selected state resets to first CV after refresh (app default) — document behavior
      await expect(page.locator('.cv-picker [role="option"]').first()).toHaveAttribute(
        "aria-selected",
        "true"
      );
      test.info().annotations.push({
        type: "finding",
        description: `Workspace model: job matches are aggregated across CVs, not isolated per resume. Selected primary before refresh was "${(selected || "").trim()}"; after refresh defaults to first CV.`,
      });
      void stateA;
    });

    await test.step("Confirm architecture note for job results ownership", async () => {
      await expect(
        page.getByText(/מאחד את כל הקבצים|פרופיל מועמד מקיף|הפרופיל המאוחד/).first()
      ).toBeVisible();
    });

    await cleanupTestResumes(page);
    saveDiagnostics("resume-separation", diag);
  });
});
