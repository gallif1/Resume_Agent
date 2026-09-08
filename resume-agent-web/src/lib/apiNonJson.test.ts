import { describe, expect, it } from "vitest";
import { missingApiPortHint, nonJsonResponseMessage } from "./api";

describe("nonJsonResponseMessage", () => {
  it("blames timeout for gateway HTML pages", () => {
    const msg = nonJsonResponseMessage(
      { ok: false, status: 504 },
      "<!doctype html><html><body>gateway timeout</body></html>",
      "fallback"
    );
    expect(msg).toContain("timeout");
    expect(msg).not.toContain(":8001");
  });

  it("treats HTML 200 as a mobile mid-request disconnect", () => {
    const msg = nonJsonResponseMessage(
      { ok: true, status: 200 },
      "<!DOCTYPE html><html><body>SPA shell</body></html>",
      "fallback",
      { protocol: "http:", hostname: "18.195.208.12", port: "8001" }
    );
    expect(msg).toContain("מובייל");
    expect(msg).not.toContain(":8001");
  });

  it("does not blame :8001 when the page is already on that port", () => {
    const msg = nonJsonResponseMessage(
      { ok: false, status: 500 },
      "<!DOCTYPE html><html><head></head><body>error</body></html>",
      "fallback",
      { protocol: "http:", hostname: "18.195.208.12", port: "8001" }
    );
    expect(msg).toContain("שגיאה 500");
    expect(msg).toContain("נסה שוב");
    expect(msg).not.toContain(":8001");
  });

  it("suggests :8001 only when browsing default http port", () => {
    const hint = missingApiPortHint({
      protocol: "http:",
      hostname: "18.195.208.12",
      port: "",
    });
    expect(hint).toContain("http://18.195.208.12:8001/cv-tailor");
  });
});
