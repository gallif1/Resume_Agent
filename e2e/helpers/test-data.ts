/**
 * Test-data helpers for Resume Agent E2E.
 * Creates local dummy files only — never touches production user data outside the test account.
 */
import fs from "fs";
import path from "path";
import { PDFDocument, StandardFonts } from "pdf-lib";

export const FIXTURES_DIR = path.join(__dirname, "..", "fixtures");
export const ARTIFACTS_DIR = path.join(__dirname, "..", "artifacts");

export const TEST_MARKER = "e2e-qa-temp";

/** Unique run id so parallel / rerun artifacts stay distinct. */
export const RUN_ID = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

let credentialNonce = 0;

export function ensureDirs() {
  for (const dir of [
    FIXTURES_DIR,
    path.join(ARTIFACTS_DIR, "screenshots"),
    path.join(ARTIFACTS_DIR, "traces"),
    path.join(ARTIFACTS_DIR, "videos"),
    path.join(ARTIFACTS_DIR, "discovery"),
    path.join(ARTIFACTS_DIR, "reports"),
    path.join(ARTIFACTS_DIR, "diagnostics"),
  ]) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

/**
 * Credentials strategy:
 * 1. Prefer QA_TEST_EMAIL / QA_TEST_PASSWORD when set.
 * 2. Otherwise generate a dedicated throwaway account (unique per call unless stable).
 */
export function getQaCredentials(options?: { stable?: boolean }): {
  email: string;
  password: string;
  fromEnv: boolean;
} {
  const email = process.env.QA_TEST_EMAIL?.trim();
  const password = process.env.QA_TEST_PASSWORD;
  if (email && password) {
    return { email, password, fromEnv: true };
  }
  const nonce = options?.stable ? 0 : ++credentialNonce;
  return {
    email: `qa.e2e.${RUN_ID}.${nonce}@example.com`,
    password: `QaE2e!${RUN_ID.slice(-8)}x`,
    fromEnv: false,
  };
}

export async function createDummyPdf(
  fileName: string,
  bodyText = "QA E2E Test Resume"
): Promise<string> {
  ensureDirs();
  const filePath = path.join(FIXTURES_DIR, fileName);
  const pdf = await PDFDocument.create();
  const page = pdf.addPage([612, 792]);
  const font = await pdf.embedFont(StandardFonts.Helvetica);
  page.drawText(bodyText, { x: 50, y: 720, size: 14, font });
  page.drawText("Software Engineer | QA Automation | Playwright", {
    x: 50,
    y: 690,
    size: 11,
    font,
  });
  page.drawText(`Marker: ${TEST_MARKER} | Run: ${RUN_ID}`, {
    x: 50,
    y: 660,
    size: 10,
    font,
  });
  page.drawText("Skills: TypeScript, React, Python, SQL, Testing", {
    x: 50,
    y: 630,
    size: 11,
    font,
  });
  const bytes = await pdf.save();
  fs.writeFileSync(filePath, bytes);
  return filePath;
}

export async function createUnsupportedFile(
  fileName = "not-a-resume.exe"
): Promise<string> {
  ensureDirs();
  const filePath = path.join(FIXTURES_DIR, fileName);
  fs.writeFileSync(filePath, Buffer.from("MZ-not-a-real-exe-qa-fixture"));
  return filePath;
}

export async function prepareAllFixtures(): Promise<{
  validPdf: string;
  validPdfB: string;
  unsupported: string;
  longNamePdf: string;
  hebrewNamePdf: string;
  spacesParensPdf: string;
}> {
  ensureDirs();
  const longName =
    "qa_e2e_" +
    "very_long_filename_".repeat(8) +
    "resume.pdf";
  return {
    validPdf: await createDummyPdf(
      `${TEST_MARKER}-resume-a.pdf`,
      "QA Resume A — Frontend Engineer"
    ),
    validPdfB: await createDummyPdf(
      `${TEST_MARKER}-resume-b.pdf`,
      "QA Resume B — Backend Engineer Python FastAPI"
    ),
    unsupported: await createUnsupportedFile(),
    longNamePdf: await createDummyPdf(longName, "QA Long Filename Resume"),
    hebrewNamePdf: await createDummyPdf(
      `${TEST_MARKER}-קורות-חיים-בדיקה.pdf`,
      "QA Hebrew Filename Resume"
    ),
    spacesParensPdf: await createDummyPdf(
      `${TEST_MARKER} resume (final copy).pdf`,
      "QA Spaces and Parentheses Resume"
    ),
  };
}

export function writeJsonArtifact(name: string, data: unknown) {
  ensureDirs();
  const target = path.join(ARTIFACTS_DIR, name);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, JSON.stringify(data, null, 2), "utf8");
}

/** Redact secrets from diagnostic dumps before writing reports. */
export function redactSecrets(value: string): string {
  return value
    .replace(/Bearer\s+[A-Za-z0-9\-._~+/]+=*/gi, "Bearer [REDACTED]")
    .replace(/("access_token"\s*:\s*")[^"]+"/gi, '$1[REDACTED]"')
    .replace(/(password["']?\s*[:=]\s*["'])[^"']+/gi, "$1[REDACTED]")
    .replace(/resume_agent_jwt[=:][^\s"']+/gi, "resume_agent_jwt=[REDACTED]");
}
