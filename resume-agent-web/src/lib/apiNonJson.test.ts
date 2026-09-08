import { describe, expect, it } from "vitest";
import { isTransientApiError, missingApiPortHint, nonJsonResponseMessage } from "./api";

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

  it("does not claim a one-minute wait for immediate HTML 200", () => {
    const msg = nonJsonResponseMessage(
      { ok: true, status: 200 },
      "<!DOCTYPE html><html><body>SPA shell</body></html>",
      "fallback",
      { protocol: "http:", hostname: "18.195.208.12", port: "8001" }
    );
    expect(msg).toContain("HTML");
    expect(msg).not.toContain("כ־דקה");
    expect(msg).not.toContain("http://18.195.208.12:8001/cv-tailor");
  });

  it("treats content-type text/html as an HTML API failure", () => {
    const msg = nonJsonResponseMessage(
      {
        ok: true,
        status: 200,
        headers: {
          get: (name: string) => (name === "content-type" ? "text/html; charset=utf-8" : null),
        },
      },
      "<!doctype html><html><body>SPA</body></html>",
      "fallback",
      { protocol: "http:", hostname: "18.195.208.12", port: "8001" }
    );
    expect(msg).toContain("HTML");
    expect(msg).toContain("200");
  });

  it("does not blame :8001 when the page is already on that port", () => {
    const msg = nonJsonResponseMessage(
      { ok: false, status: 500 },
      "<!DOCTYPE html><html><head></head><body>error</body></html>",
      "fallback",
      { protocol: "http:", hostname: "18.195.208.12", port: "8001" }
    );
    expect(msg).toContain("שגיאה 500");
    expect(msg).toContain("HTML");
    expect(msg).not.toContain("http://18.195.208.12:8001/cv-tailor");
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

describe("isTransientApiError", () => {
  it("retries the current HTML-instead-of-API copy", () => {
    expect(
      isTransientApiError(
        new Error("השרת החזיר דף HTML במקום תשובת API (שגיאה 200). רענן את הדף ונסה שוב.")
      )
    ).toBe(true);
  });

  it("does not treat parse errors as transient", () => {
    expect(
      isTransientApiError(
        new Error("לא הצלחנו לקרוא טקסט מקובץ קורות החיים. נסה DOCX מ-Word, או PDF מבוסס טקסט.")
      )
    ).toBe(false);
  });
});
