/**
 * Phase 1 — Application discovery against the live deployed app.
 * Produces artifacts/discovery/flow-map.json and APPLICATION_FLOW_MAP.md content.
 */
import { test, expect } from "@playwright/test";
import fs from "fs";
import path from "path";
import { attachDiagnostics, saveDiagnostics } from "../helpers/diagnostics";
import { ensureAuthenticated, gotoApp, expectAuthScreen } from "../helpers/auth";
import {
  ARTIFACTS_DIR,
  ensureDirs,
  prepareAllFixtures,
  writeJsonArtifact,
} from "../helpers/test-data";
import { screenshotViewport } from "../helpers/layout";

test.describe.configure({ mode: "serial" });

test.describe("Phase 1 — Application discovery", () => {
  test("map public auth surface, routes, and authenticated controls", async ({
    page,
  }, testInfo) => {
    // Discovery is desktop-only to avoid duplicate maps across viewports.
    test.skip(
      testInfo.project.name !== "chromium-desktop",
      "Discovery runs once on desktop"
    );

    ensureDirs();
    const diag = attachDiagnostics(page);
    const flowMap: Record<string, unknown> = {
      baseURL: testInfo.project.use?.baseURL,
      discoveredAt: new Date().toISOString(),
      framework: "React + TypeScript + Vite (SPA, no client router)",
      backend: "FastAPI (same-origin /api, /cvs, /jobs)",
      routes: [] as string[],
      navigation: [] as string[],
      buttons: [] as string[],
      forms: [] as string[],
      modals: [] as string[],
      resumeControls: [] as string[],
      jobScanControls: [] as string[],
      jobResultControls: [] as string[],
      auth: {} as Record<string, unknown>,
      states: {} as Record<string, unknown>,
      notes: [] as string[],
    };

    await test.step("Public / unauthenticated surface", async () => {
      await gotoApp(page);
      await expectAuthScreen(page);
      await screenshotViewport(page, "discovery-auth");

      const dir = await page.locator("html").getAttribute("dir");
      const lang = await page.locator("html").getAttribute("lang");
      flowMap.auth = {
        requiresAuth: true,
        modes: ["login", "register"],
        fields: ["email", "password"],
        passwordMinLength: 6,
        passwordConfirmField: false,
        jwtStorageKey: "resume_agent_jwt",
        htmlDir: dir,
        htmlLang: lang,
      };

      const tabs = await page.getByRole("tab").allTextContents();
      (flowMap.navigation as string[]).push(...tabs.map((t) => t.trim()));

      const authButtons = await page.locator(".auth-view button").allTextContents();
      (flowMap.buttons as string[]).push(
        ...authButtons.map((t) => t.trim()).filter(Boolean)
      );
      (flowMap.forms as string[]).push("auth-form (email, password)");
      (flowMap.routes as string[]).push(
        "/ (SPA root — AuthView when logged out)"
      );
      (flowMap.states as Record<string, unknown>).authEmpty = true;
    });

    await test.step("Probe unknown SPA paths (should not crash)", async () => {
      for (const route of ["/jobs", "/cvs", "/dashboard", "/settings"]) {
        await page.goto(route, { waitUntil: "domcontentloaded" });
        // SPA has no router — still serves index.html / root app
        const hasUi = await page
          .locator(".logo-text, .auth-title, #root")
          .first()
          .isVisible()
          .catch(() => false);
        (flowMap.routes as string[]).push(
          `${route} → SPA fallback (hasUi=${hasUi})`
        );
      }
      await page.goto("/", { waitUntil: "domcontentloaded" });
    });

    const auth = await ensureAuthenticated(page);
    if (!auth.ok) {
      (flowMap.notes as string[]).push(`Auth blocked: ${auth.blockedReason}`);
      writeJsonArtifact("discovery/flow-map.json", flowMap);
      saveDiagnostics("discovery", diag);
      test.info().annotations.push({
        type: "blocked",
        description: auth.blockedReason || "auth failed",
      });
      return;
    }

    await test.step("Authenticated workspace discovery", async () => {
      await expect(page.locator(".server-status.up")).toBeVisible({ timeout: 60_000 });
      await screenshotViewport(page, "discovery-workspace-empty");

      const headings = await page.locator("h1, h2, h3").allTextContents();
      (flowMap.navigation as string[]).push(
        ...headings.map((h) => h.trim()).filter(Boolean)
      );

      const buttons = await page.locator("button:visible").allTextContents();
      (flowMap.buttons as string[]).push(
        ...[...new Set(buttons.map((b) => b.trim()).filter(Boolean))]
      );

      (flowMap.resumeControls as string[]).push(
        "dropzone file upload (pdf/doc/docx/txt/images)",
        "cv list with delete + confirm modal",
        "cv picker (marks primary display; workspace aggregates all CVs)",
        "אפס תוצאות / אפס קבצים reset modals"
      );
      (flowMap.jobScanControls as string[]).push(
        "site toggles: Drushim / LinkedIn / GotFriends",
        "שגר סוכן לסריקה",
        "עצור סריקה (while running)",
        "PipelineProgress live steps"
      );
      (flowMap.jobResultControls as string[]).push(
        "צפה בתוצאות → CvDetails workspace mode",
        "sort: score / date / site",
        "expand job card: description, skills, status select",
        "הגש קורות חיים (confirm modal — do not submit in QA)",
        "ייצר קורות חיים (AI tailor — expensive, skip in discovery)",
        "NO client-side search / filter / pagination UI observed in source"
      );
      (flowMap.modals as string[]).push(
        "delete CV confirm",
        "reset results / reset files",
        "apply confirm",
        "application log",
        "tailored CV preview"
      );
      (flowMap.routes as string[]).push(
        "/ (CvManager when logged in)",
        "in-app view: CvDetails (showMatches state, no URL change)"
      );
      (flowMap.states as Record<string, unknown>).emptyCvs = await page
        .getByText("עדיין לא העלית קורות חיים")
        .isVisible()
        .catch(() => false);

      // Light upload to reveal scan controls (no scan started)
      const fixtures = await prepareAllFixtures();
      const input = page.locator('input[type="file"]');
      await input.setInputFiles(fixtures.validPdf);
      await expect(page.locator(".toast")).toContainText(/הועל/, {
        timeout: 30_000,
      });
      await screenshotViewport(page, "discovery-with-resume");

      await expect(
        page.getByRole("button", { name: "שגר סוכן לסריקה" })
      ).toBeVisible();

      // Cleanup discovery upload
      const del = page
        .locator(".cv-item")
        .filter({ hasText: "e2e-qa-temp" })
        .first();
      if (await del.isVisible().catch(() => false)) {
        await del.getByRole("button", { name: "מחק" }).click();
        await page
          .locator(".modal")
          .getByRole("button", { name: "מחק לצמיתות" })
          .click();
      }
    });

    writeJsonArtifact("discovery/flow-map.json", flowMap);

    const md = `# Application Flow Map

Generated: ${flowMap.discoveredAt}

## Stack
- Frontend: React + TypeScript + Vite SPA (no client-side router)
- Backend: FastAPI on same origin (\`/api\`, \`/cvs\`, \`/jobs\`)
- Auth: JWT in \`localStorage\` key \`resume_agent_jwt\`
- UI language: Hebrew RTL (\`html[dir=rtl][lang=he]\`)

## Accessible routes
${(flowMap.routes as string[]).map((r) => `- ${r}`).join("\n")}

## Auth
\`\`\`json
${JSON.stringify(flowMap.auth, null, 2)}
\`\`\`

## Main user workflows
1. Register / Login
2. Upload one or more resumes
3. Select job sites + launch agent scan (workspace aggregates all CVs)
4. View matches, sort, update status, tailor CV, open apply confirm
5. Logout

## Resume controls
${(flowMap.resumeControls as string[]).map((r) => `- ${r}`).join("\n")}

## Job scan controls
${(flowMap.jobScanControls as string[]).map((r) => `- ${r}`).join("\n")}

## Job result controls
${(flowMap.jobResultControls as string[]).map((r) => `- ${r}`).join("\n")}

## Modals
${(flowMap.modals as string[]).map((r) => `- ${r}`).join("\n")}

## Buttons observed (sample)
${[...new Set(flowMap.buttons as string[])]
  .slice(0, 40)
  .map((b) => `- ${b}`)
  .join("\n")}

## Notes
${(flowMap.notes as string[]).map((n) => `- ${n}`).join("\n") || "- none"}
`;
    fs.writeFileSync(
      path.join(ARTIFACTS_DIR, "discovery", "APPLICATION_FLOW_MAP.md"),
      md,
      "utf8"
    );
    // Also copy to e2e root for required docs
    fs.writeFileSync(
      path.join(ARTIFACTS_DIR, "..", "APPLICATION_FLOW_MAP.md"),
      md,
      "utf8"
    );

    saveDiagnostics("discovery", diag);
    expect(diag.pageErrors, `pageerrors: ${diag.pageErrors.join("; ")}`).toEqual(
      []
    );
  });
});
