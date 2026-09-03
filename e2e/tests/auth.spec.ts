/**
 * Phase 3 — Authentication flows.
 * Uses QA_TEST_EMAIL / QA_TEST_PASSWORD when set; otherwise registers a throwaway account.
 */
import { test, expect } from "@playwright/test";
import {
  attachDiagnostics,
  saveDiagnostics,
} from "../helpers/diagnostics";
import {
  gotoApp,
  switchAuthTab,
  fillAuthForm,
  submitAuth,
  logout,
  expectAuthScreen,
} from "../helpers/auth";
import { getQaCredentials, RUN_ID } from "../helpers/test-data";
import { screenshotViewport } from "../helpers/layout";

test.describe.configure({ mode: "serial" });

test.describe("Phase 3 — Authentication", () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(
      !["chromium-desktop", "chromium-mobile"].includes(testInfo.project.name),
      "Auth suite on desktop + mobile"
    );
  });

  test("registration validation: empty fields, invalid email, weak password", async ({
    page,
  }) => {
    const diag = attachDiagnostics(page);
    await gotoApp(page);
    await switchAuthTab(page, "register");

    await test.step("Empty fields blocked by HTML5 required", async () => {
      await fillAuthForm(page, "", "");
      await submitAuth(page);
      // Still on auth form — browser validation prevents submit
      await expectAuthScreen(page);
      const emailValid = await page.getByLabel("אימייל").evaluate(
        (el: HTMLInputElement) => el.validity.valid
      );
      expect(emailValid).toBeFalsy();
    });

    await test.step("Invalid email", async () => {
      await fillAuthForm(page, "not-an-email", "validpass123");
      // type=email may block submit client-side
      const emailInput = page.getByLabel("אימייל");
      const clientValid = await emailInput.evaluate(
        (el: HTMLInputElement) => el.validity.valid
      );
      if (clientValid) {
        await submitAuth(page);
        await expect(page.getByRole("alert")).toBeVisible({ timeout: 10_000 });
      } else {
        await submitAuth(page);
        await expectAuthScreen(page);
      }
    });

    await test.step("Weak password (< 6 chars)", async () => {
      await fillAuthForm(page, `weak.${RUN_ID}@example.com`, "12345");
      const pw = page.getByLabel("סיסמה");
      const minOk = await pw.evaluate(
        (el: HTMLInputElement) => el.validity.tooShort || !el.validity.valid
      );
      expect(minOk).toBeTruthy();
      await submitAuth(page);
      // Should not become authenticated
      await expect(page.getByRole("button", { name: "התנתק" })).toHaveCount(0);
    });

    await test.step("Password mismatch — N/A (no confirm field in UI)", async () => {
      test.info().annotations.push({
        type: "note",
        description:
          "AuthView has no password-confirm field; mismatch scenario is not applicable to current UI",
      });
    });

    saveDiagnostics("auth-validation", diag);
  });

  test("register valid account, duplicate blocked, login / logout / refresh", async ({
    page,
  }, testInfo) => {
    const diag = attachDiagnostics(page);
    const creds = getQaCredentials();
    // Use unique email for this test so we always exercise registration
    const email = creds.fromEnv
      ? creds.email
      : `qa.auth.${RUN_ID}.${testInfo.project.name}@example.com`;
    const password = creds.fromEnv ? creds.password : `AuthOk!${RUN_ID.slice(-6)}`;

    await test.step("Valid registration", async () => {
      await gotoApp(page);
      await page.evaluate(() => localStorage.removeItem("resume_agent_jwt"));
      await page.reload({ waitUntil: "domcontentloaded" });
      await switchAuthTab(page, "register");
      await fillAuthForm(page, email, password);
      await submitAuth(page);

      if (creds.fromEnv) {
        // Env account may already exist — accept either login success via register error then login
        const loggedIn = await page
          .getByRole("button", { name: "התנתק" })
          .isVisible({ timeout: 15_000 })
          .catch(() => false);
        if (!loggedIn) {
          const alert = page.getByRole("alert");
          if (await alert.isVisible().catch(() => false)) {
            await switchAuthTab(page, "login");
            await fillAuthForm(page, email, password);
            await submitAuth(page);
          }
        }
      }

      await expect(page.getByRole("button", { name: "התנתק" })).toBeVisible({
        timeout: 30_000,
      });
      await expect(page.locator(".user-chip")).toContainText(email);
    });

    await test.step("Refresh after login preserves session", async () => {
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(page.getByRole("button", { name: "התנתק" })).toBeVisible({
        timeout: 30_000,
      });
    });

    await test.step("Logout", async () => {
      await logout(page);
      await expectAuthScreen(page);
      const token = await page.evaluate(() =>
        localStorage.getItem("resume_agent_jwt")
      );
      expect(token).toBeNull();
    });

    await test.step("Duplicate registration rejected", async () => {
      await switchAuthTab(page, "register");
      await fillAuthForm(page, email, password);
      await submitAuth(page);
      await expect(page.getByRole("alert")).toBeVisible({ timeout: 15_000 });
      await expect(page.getByRole("alert")).toContainText(/קיים|כבר/);
    });

    await test.step("Valid login", async () => {
      await switchAuthTab(page, "login");
      await fillAuthForm(page, email, password);
      await submitAuth(page);
      await expect(page.getByRole("button", { name: "התנתק" })).toBeVisible({
        timeout: 20_000,
      });
    });

    await test.step("Invalid login", async () => {
      await logout(page);
      await switchAuthTab(page, "login");
      await fillAuthForm(page, email, "wrong-password-!!!!");
      await submitAuth(page);
      await expect(page.getByRole("alert")).toBeVisible({ timeout: 15_000 });
      await expect(page.getByRole("button", { name: "התנתק" })).toHaveCount(0);
    });

    await test.step("Press Enter to submit", async () => {
      await switchAuthTab(page, "login");
      await fillAuthForm(page, email, password);
      await page.getByLabel("סיסמה").press("Enter");
      await expect(page.getByRole("button", { name: "התנתק" })).toBeVisible({
        timeout: 20_000,
      });
    });

    await test.step("Repeated clicks on submit do not break auth", async () => {
      await logout(page);
      await switchAuthTab(page, "login");
      await fillAuthForm(page, email, password);
      const submit = page.locator('form.auth-form button[type="submit"]');
      // Rapid multi-click: first click starts request (button disables); force later clicks
      await submit.click();
      await submit.click({ force: true }).catch(() => undefined);
      await submit.click({ force: true }).catch(() => undefined);
      await expect(page.getByRole("button", { name: "התנתק" })).toBeVisible({
        timeout: 25_000,
      });
    });

    await screenshotViewport(page, `auth-success-${testInfo.project.name}`);
    saveDiagnostics(`auth-flow-${testInfo.project.name}`, diag);
  });

  test("protected workspace not reachable while logged out", async ({ page }) => {
    const diag = attachDiagnostics(page);
    await gotoApp(page);
    await page.evaluate(() => localStorage.removeItem("resume_agent_jwt"));
    await page.reload({ waitUntil: "domcontentloaded" });
    await expectAuthScreen(page);
    // Injecting a fake token should not grant access without valid JWT
    await page.evaluate(() =>
      localStorage.setItem("resume_agent_jwt", "not.a.real.token")
    );
    await page.reload({ waitUntil: "domcontentloaded" });
    // App should clear invalid token and show auth (or briefly check then auth)
    await expectAuthScreen(page);
    // Upload / scan controls must not be available
    await expect(page.getByText("גרור לכאן קבצי קורות חיים")).toHaveCount(0);
    saveDiagnostics("auth-protected", diag);
  });

  test("credentials availability note", async () => {
    const creds = getQaCredentials({ stable: true });
    if (!creds.fromEnv) {
      test.info().annotations.push({
        type: "note",
        description:
          "QA_TEST_EMAIL / QA_TEST_PASSWORD were not set; suite used generated throwaway accounts via registration",
      });
    } else {
      test.info().annotations.push({
        type: "note",
        description: "QA_TEST_EMAIL / QA_TEST_PASSWORD were provided",
      });
    }
    // Auth already exercised above — this test only records credential source.
    expect(creds.email).toBeTruthy();
    expect(creds.password.length).toBeGreaterThanOrEqual(6);
  });
});
