import { describe, expect, it } from "vitest";
import { formatJobDescription } from "./formatJobDescription";

describe("formatJobDescription", () => {
  it("removes LinkedIn show more/less UI noise", () => {
    const raw = [
      "Senior Software Engineer",
      "Show more",
      "We build great products.",
      "Show less",
      "Requirements: Python",
    ].join("\n");

    const formatted = formatJobDescription(raw);
    expect(formatted.toLowerCase()).not.toContain("show more");
    expect(formatted.toLowerCase()).not.toContain("show less");
    expect(formatted).toContain("Senior Software Engineer");
    expect(formatted).toContain("Requirements");
  });
});
