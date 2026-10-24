/**
 * Phase 2 — Smoke tests against the live deployed app.
 */
import { test, expect } from "@playwright/test";
import {
  attachDiagnostics,
  isImportantApiFailure,
  saveDiagnostics,
} from "../helpers/diagnostics";
import { gotoApp, expectAuthScreen } from "../helpers/auth";
import { screenshotViewport } from "../helpers/layout";

test.describe("Phase 2 — Smoke", () => {
  test("website loads with primary UI and no blank screen", async ({
    page,
  }, testInfo) => {
    const diag = attachDiagnostics(page);
    await gotoApp(page);

    await test.step("Main heading / brand visible", async () => {
      await expect(page.locator(".logo-text")).toContainText("Resume");
      await expect(page.locator(".logo-text")).toContainText("Agent");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    });

    await test.step("Primary auth UI visible (logged-out default)", async () => {
      await expectAuthScreen(page);
      await expect(page.locator("#root")).not.toBeEmpty();
      const rootText = await page.locator("#root").innerText();
      expect(rootText.trim().length).toBeGreaterThan(20);
    });

    await test.step("Static assets / fonts load reasonably", async () => {
      const failedAssets = diag.failedRequests.filter(
        (r) =>
          r.resourceType === "stylesheet" ||
          r.resourceType === "script" ||
          r.resourceType === "font"
      );
      expect(
        failedAssets,
        JSON.stringify(failedAssets)
      ).toEqual([]);
    });

    await test.step("Health endpoint reachable", async () => {
      const res = await page.request.get("/api/health");
      expect(res.ok()).toBeTruthy();
      const body = await res.json();
      expect(body.ok).toBeTruthy();
    });

    await screenshotViewport(
      page,
      `smoke-load-${testInfo.project.name}`
    );
    saveDiagnostics(`smoke-load-${testInfo.project.name}`, diag);

    const critical = [
      ...diag.pageErrors,
      ...diag.httpErrors.filter(isImportantApiFailure).map((e) => `${e.status} ${e.url}`),
    ];
    expect(critical, critical.join("\n")).toEqual([]);
  });

  test("navigation tabs, refresh, and history behave", async ({ page }, testInfo) => {
    // History checks once on desktop to reduce noise
    test.skip(
      !["chromium-desktop", "chromium-mobile"].includes(testInfo.project.name),
      "History smoke on desktop + mobile only"
    );

    const diag = attachDiagnostics(page);
    await gotoApp(page);

    await test.step("Auth tab navigation works", async () => {
      await page.getByRole("tab", { name: "הרשמה" }).click();
      await expect(page.getByRole("button", { name: "צור חשבון" })).toBeVisible();
      await page.getByRole("tab", { name: "התחברות" }).click();
      await expect(page.getByRole("button", { name: "התחבר" })).toBeVisible();
    });

    await test.step("Refresh does not crash", async () => {
      await page.reload({ waitUntil: "domcontentloaded" });
      await expectAuthScreen(page);
      await expect(page.locator(".logo-text")).toBeVisible();
    });

    await test.step("Deep-link SPA fallback works for unknown UI paths", async () => {
      await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
      await expect(page.locator("#root")).toBeVisible();
      await expect(page.locator(".logo-text")).toBeVisible();
    });

    await test.step("Document SPA/API path collision on /jobs and /cvs", async () => {
      const jobs = await page.request.get("/jobs");
      const cvs = await page.request.get("/cvs");
      // Confirmed production behavior (defect DEF-001): these API prefixes
      // do not fall back to index.html, so browser navigation shows raw JSON.
      expect(jobs.headers()["content-type"] || "").toMatch(/json/);
      expect([401, 404, 405, 422]).toContain(jobs.status());
      expect(cvs.headers()["content-type"] || "").toMatch(/json/);
      test.info().annotations.push({
        type: "defect",
        description:
          "DEF-001: Browser navigation to /jobs or /cvs returns API JSON instead of the React SPA (no HTML fallback). Screenshot: smoke history failure artifacts.",
      });
    });

    await test.step("Back / Forward after path probe", async () => {
      await page.goto("/", { waitUntil: "domcontentloaded" });
      await expectAuthScreen(page);
      await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
      await expect(page.locator(".logo-text")).toBeVisible();
      await page.goBack();
      await expect(page.locator(".logo-text")).toBeVisible();
      await page.goForward();
      await expect(page.locator(".logo-text")).toBeVisible();
      await page.goto("/", { waitUntil: "domcontentloaded" });
      await expectAuthScreen(page);
    });

    saveDiagnostics(`smoke-nav-${testInfo.project.name}`, diag);
    expect(diag.pageErrors).toEqual([]);
  });

  test("no critical console exceptions on initial load", async ({ page }, testInfo) => {
    test.skip(
      testInfo.project.name !== "chromium-desktop",
      "Console check once on desktop"
    );
    const diag = attachDiagnostics(page);
    await gotoApp(page);
    await page.waitForTimeout(1500); // brief settle for async health ping — not a long sleep
    expect(diag.pageErrors).toEqual([]);
    // Filter known benign network noise
    const criticalConsole = diag.consoleErrors.filter(
      (e) => !/Failed to load resource/i.test(e) || /\/api\//.test(e)
    );
    saveDiagnostics("smoke-console", diag);
    expect(criticalConsole).toEqual([]);
  });
});
