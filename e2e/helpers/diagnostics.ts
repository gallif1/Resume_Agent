/**
 * Page diagnostics: console errors, pageerrors, failed / 4xx+ network.
 * Secrets are redacted before persistence.
 */
import type { Page, Request, Response, ConsoleMessage } from "@playwright/test";
import fs from "fs";
import path from "path";
import { ARTIFACTS_DIR, ensureDirs, redactSecrets } from "./test-data";

export type NetworkIssue = {
  url: string;
  method: string;
  status?: number;
  failure?: string;
  resourceType?: string;
  at: string;
};

export type PageDiagnostics = {
  consoleErrors: string[];
  pageErrors: string[];
  failedRequests: NetworkIssue[];
  httpErrors: NetworkIssue[];
  pendingRequests: string[];
  requestLog: { url: string; method: string; at: string }[];
};

const IGNORE_CONSOLE = [
  /Download the React DevTools/i,
  /\[vite\]/i,
  /favicon/i,
];

const IGNORE_URL = [/google-analytics/i, /googletagmanager/i, /fonts\.gstatic/i];

export function attachDiagnostics(page: Page): PageDiagnostics {
  const diag: PageDiagnostics = {
    consoleErrors: [],
    pageErrors: [],
    failedRequests: [],
    httpErrors: [],
    pendingRequests: [],
    requestLog: [],
  };

  const pending = new Map<Request, string>();

  page.on("console", (msg: ConsoleMessage) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    if (IGNORE_CONSOLE.some((re) => re.test(text))) return;
    diag.consoleErrors.push(redactSecrets(text));
  });

  page.on("pageerror", (err) => {
    diag.pageErrors.push(redactSecrets(err.message || String(err)));
  });

  page.on("request", (req) => {
    if (IGNORE_URL.some((re) => re.test(req.url()))) return;
    pending.set(req, new Date().toISOString());
    diag.requestLog.push({
      url: redactSecrets(req.url()),
      method: req.method(),
      at: new Date().toISOString(),
    });
  });

  page.on("requestfailed", (req) => {
    if (IGNORE_URL.some((re) => re.test(req.url()))) return;
    pending.delete(req);
    diag.failedRequests.push({
      url: redactSecrets(req.url()),
      method: req.method(),
      failure: req.failure()?.errorText,
      resourceType: req.resourceType(),
      at: new Date().toISOString(),
    });
  });

  page.on("response", (res: Response) => {
    const req = res.request();
    pending.delete(req);
    if (IGNORE_URL.some((re) => re.test(res.url()))) return;
    if (res.status() >= 400) {
      diag.httpErrors.push({
        url: redactSecrets(res.url()),
        method: req.method(),
        status: res.status(),
        resourceType: req.resourceType(),
        at: new Date().toISOString(),
      });
    }
  });

  // Expose pending snapshot helper
  (diag as PageDiagnostics & { _flushPending: () => void })._flushPending = () => {
    diag.pendingRequests = [...pending.keys()].map((r) =>
      redactSecrets(`${r.method()} ${r.url()}`)
    );
  };

  return diag;
}

export function flushPending(diag: PageDiagnostics) {
  const flush = (diag as PageDiagnostics & { _flushPending?: () => void })
    ._flushPending;
  flush?.();
}

export function saveDiagnostics(name: string, diag: PageDiagnostics) {
  ensureDirs();
  flushPending(diag);
  const file = path.join(ARTIFACTS_DIR, "diagnostics", `${name}.json`);
  fs.writeFileSync(file, JSON.stringify(diag, null, 2), "utf8");
  return file;
}

/** Critical API paths that should not fail unexpectedly during smoke. */
export function isImportantApiFailure(issue: NetworkIssue): boolean {
  const u = issue.url;
  if (!/\/(api|cvs|jobs)\b/.test(u)) return false;
  // Expected auth failures during negative tests
  if (/\/api\/auth\/(login|register)/.test(u) && (issue.status === 400 || issue.status === 401 || issue.status === 422)) {
    return false;
  }
  // Expected duplicate upload 409
  if (/\/cvs\/upload/.test(u) && issue.status === 409) return false;
  return (issue.status ?? 0) >= 400 || Boolean(issue.failure);
}
