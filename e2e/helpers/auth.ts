/**
 * Auth and navigation helpers for Resume Agent E2E.
 */
import { expect, type Page } from "@playwright/test";
import { getQaCredentials } from "./test-data";

export async function gotoApp(page: Page) {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".logo-text, .auth-title").first()).toBeVisible({
    timeout: 45_000,
  });
}

export async function waitForServerConnected(page: Page) {
  // On viewports <768px the status label is CSS-hidden; the green status control remains.
  await expect(page.locator(".server-status.up")).toBeVisible({ timeout: 60_000 });
}

export async function switchAuthTab(page: Page, mode: "login" | "register") {
  const label = mode === "login" ? "התחברות" : "הרשמה";
  await page.getByRole("tab", { name: label }).click();
}

export async function fillAuthForm(
  page: Page,
  email: string,
  password: string
) {
  await page.getByLabel("אימייל").fill(email);
  await page.getByLabel("סיסמה").fill(password);
}

export async function submitAuth(page: Page) {
  await page.locator('form.auth-form button[type="submit"]').click();
}

export async function registerAccount(
  page: Page,
  email: string,
  password: string
) {
  await gotoApp(page);
  await switchAuthTab(page, "register");
  await fillAuthForm(page, email, password);
  await submitAuth(page);
  await expect(page.getByRole("button", { name: "התנתק" })).toBeVisible({
    timeout: 30_000,
  });
}

export async function loginAccount(
  page: Page,
  email: string,
  password: string
) {
  await gotoApp(page);
  // Clear any prior session
  await page.evaluate(() => localStorage.removeItem("resume_agent_jwt"));
  await page.reload({ waitUntil: "domcontentloaded" });
  await switchAuthTab(page, "login");
  await fillAuthForm(page, email, password);
  await submitAuth(page);
  await expect(page.getByRole("button", { name: "התנתק" })).toBeVisible({
    timeout: 30_000,
  });
}

export async function logout(page: Page) {
  const btn = page.getByRole("button", { name: "התנתק" });
  if (await btn.isVisible().catch(() => false)) {
    await btn.click();
  }
  await expect(page.getByRole("tab", { name: "התחברות" })).toBeVisible({
    timeout: 15_000,
  });
}

/**
 * Ensure we have a usable session.
 * Uses env credentials when present; otherwise registers a fresh account.
 * Returns blocked reason if authentication cannot be established.
 */
export async function ensureAuthenticated(page: Page): Promise<{
  ok: boolean;
  email: string;
  blockedReason?: string;
  createdNew?: boolean;
}> {
  const creds = getQaCredentials();
  try {
    await gotoApp(page);
    await page.evaluate(() => localStorage.removeItem("resume_agent_jwt"));
    await page.reload({ waitUntil: "domcontentloaded" });

    if (creds.fromEnv) {
      await switchAuthTab(page, "login");
      await fillAuthForm(page, creds.email, creds.password);
      await submitAuth(page);
      const loggedIn = await page
        .getByRole("button", { name: "התנתק" })
        .isVisible({ timeout: 20_000 })
        .catch(() => false);
      if (loggedIn) {
        return { ok: true, email: creds.email, createdNew: false };
      }
      // Try register if login failed (first-time env account)
      await switchAuthTab(page, "register");
      await fillAuthForm(page, creds.email, creds.password);
      await submitAuth(page);
      const registered = await page
        .getByRole("button", { name: "התנתק" })
        .isVisible({ timeout: 20_000 })
        .catch(() => false);
      if (registered) {
        return { ok: true, email: creds.email, createdNew: true };
      }
      const alert = await page.getByRole("alert").textContent().catch(() => null);
      // Duplicate → login again
      if (alert && /קיים|כבר/.test(alert)) {
        await switchAuthTab(page, "login");
        await fillAuthForm(page, creds.email, creds.password);
        await submitAuth(page);
        const ok = await page
          .getByRole("button", { name: "התנתק" })
          .isVisible({ timeout: 20_000 })
          .catch(() => false);
        if (ok) return { ok: true, email: creds.email, createdNew: false };
      }
      return {
        ok: false,
        email: creds.email,
        blockedReason:
          "QA_TEST_EMAIL/QA_TEST_PASSWORD provided but login and register both failed",
      };
    }

    // Generated credentials — register new throwaway account
    await switchAuthTab(page, "register");
    await fillAuthForm(page, creds.email, creds.password);
    await submitAuth(page);
    let alert = await page.getByRole("alert").textContent().catch(() => null);
    if (alert && /קיים|כבר/.test(alert)) {
      await switchAuthTab(page, "login");
      await fillAuthForm(page, creds.email, creds.password);
      await submitAuth(page);
    }
    const ok = await page
      .getByRole("button", { name: "התנתק" })
      .isVisible({ timeout: 30_000 })
      .catch(() => false);
    if (!ok) {
      alert = await page.getByRole("alert").textContent().catch(() => null);
      return {
        ok: false,
        email: creds.email,
        blockedReason: `Could not register/login generated QA account: ${alert ?? "unknown error"}`,
      };
    }
    return { ok: true, email: creds.email, createdNew: true };
  } catch (e) {
    return {
      ok: false,
      email: creds.email,
      blockedReason: e instanceof Error ? e.message : String(e),
    };
  }
}

export async function isLoggedIn(page: Page): Promise<boolean> {
  return page
    .getByRole("button", { name: "התנתק" })
    .isVisible()
    .catch(() => false);
}

export async function expectAuthScreen(page: Page) {
  await expect(page.getByRole("tab", { name: "התחברות" })).toBeVisible();
  await expect(page.getByLabel("אימייל")).toBeVisible();
  await expect(page.getByLabel("סיסמה")).toBeVisible();
}
