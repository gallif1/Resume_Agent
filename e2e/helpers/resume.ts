/**
 * Resume upload / delete helpers. Only deletes resumes created by this suite.
 */
import { expect, type Page } from "@playwright/test";
import { TEST_MARKER } from "./test-data";

export async function uploadResume(page: Page, filePath: string) {
  const input = page.locator('input[type="file"]');
  await expect(input).toBeAttached();
  // Wait until previous upload finishes
  await expect(page.locator(".dropzone.busy")).toHaveCount(0, { timeout: 60_000 });
  await input.setInputFiles(filePath);
  await expect(page.locator(".dropzone.busy")).toHaveCount(0, { timeout: 60_000 });
}

export async function waitForCvsLoaded(page: Page) {
  await expect(page.getByText("סוכן מחובר")).toBeVisible({ timeout: 60_000 });
  // history-count shows "טוען..." while fetching
  await expect(page.locator(".history-count")).not.toHaveText(/טוען/, {
    timeout: 60_000,
  });
}

export async function expectToast(page: Page, text: string | RegExp) {
  await expect(page.locator(".toast")).toContainText(text, { timeout: 30_000 });
}

export async function listUploadedCvNames(page: Page): Promise<string[]> {
  const items = page.locator(".cv-list .cv-item .cv-name");
  const count = await items.count();
  const names: string[] = [];
  for (let i = 0; i < count; i++) {
    names.push(((await items.nth(i).textContent()) || "").trim());
  }
  return names;
}

export async function deleteCvByName(page: Page, namePart: string) {
  const item = page.locator(".cv-item").filter({ hasText: namePart }).first();
  await expect(item).toBeVisible();
  const fullName = ((await item.locator(".cv-name").textContent()) || "").trim();
  await item.getByRole("button", { name: "מחק" }).click();
  const modal = page.locator(".modal").filter({ hasText: "מחיקת קורות חיים" });
  await expect(modal).toBeVisible();
  await modal.getByRole("button", { name: "מחק לצמיתות" }).click();
  await expect(modal).toBeHidden({ timeout: 15_000 });
  await expect(
    page.locator(".cv-item .cv-name").filter({ hasText: fullName })
  ).toHaveCount(0, { timeout: 30_000 });
  return fullName;
}

export async function cancelDeleteCvByName(page: Page, namePart: string) {
  const item = page.locator(".cv-item").filter({ hasText: namePart }).first();
  await item.getByRole("button", { name: "מחק" }).click();
  const modal = page.locator(".modal").filter({ hasText: "מחיקת קורות חיים" });
  await expect(modal).toBeVisible();
  await modal.getByRole("button", { name: "ביטול" }).click();
  await expect(modal).toBeHidden();
  await expect(item).toBeVisible();
}

/** Delete every CV whose name contains the QA marker (safe cleanup). */
export async function cleanupTestResumes(page: Page) {
  // Prefer "אפס קבצים" only on dedicated throwaway accounts when all files are ours.
  // Safer: delete individually by marker.
  for (let guard = 0; guard < 20; guard++) {
    const item = page
      .locator(".cv-item")
      .filter({ hasText: TEST_MARKER })
      .first();
    if (!(await item.isVisible().catch(() => false))) break;
    const name = ((await item.locator(".cv-name").textContent()) || "").trim();
    await item.getByRole("button", { name: "מחק" }).click();
    const modal = page.locator(".modal").filter({ hasText: "מחיקת קורות חיים" });
    await modal.getByRole("button", { name: "מחק לצמיתות" }).click();
    await expect(page.locator(".cv-item").filter({ hasText: name })).toHaveCount(
      0,
      { timeout: 20_000 }
    );
  }
}

export async function selectCvInPicker(page: Page, namePart: string) {
  const option = page
    .locator('.cv-picker [role="option"]')
    .filter({ hasText: namePart })
    .first();
  await option.click();
  await expect(option).toHaveAttribute("aria-selected", "true");
}

export async function dismissNativeConfirm(page: Page, accept: boolean) {
  page.once("dialog", async (dialog) => {
    if (accept) await dialog.accept();
    else await dialog.dismiss();
  });
}
