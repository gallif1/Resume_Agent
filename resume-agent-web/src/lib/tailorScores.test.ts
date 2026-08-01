import { describe, expect, it } from "vitest";
import {
  buildScoreBreakdown,
  getOriginalMatchScore,
  getTailoredScore,
} from "./tailorScores";
import type { TailoredCvResponse } from "./api";

function baseResult(
  overrides: Partial<TailoredCvResponse> = {}
): TailoredCvResponse {
  return {
    cv_id: "c1",
    job_id: 1,
    title: "Engineer",
    company: "Acme",
    markdown: "# CV",
    highlights: [],
    caveats: [],
    from_cache: false,
    saved_path: "",
    ...overrides,
  };
}

describe("score helpers", () => {
  it("prefers original_match_score over initial_match_score", () => {
    const result = baseResult({
      original_match_score: 64,
      initial_match_score: 50,
    });
    expect(getOriginalMatchScore(result, 40)).toBe(64);
  });

  it("prefers tailored_match_score over score_after / estimated", () => {
    const result = baseResult({
      tailored_match_score: 76,
      score_after: 70,
      estimated_ats_score: 60,
    });
    expect(getTailoredScore(result)).toBe(76);
  });

  it("during generation never exposes a tailored score", () => {
    const breakdown = buildScoreBreakdown({
      result: baseResult({
        original_match_score: 64,
        tailored_match_score: 90,
        score_after: 90,
      }),
      originalBaseline: 64,
      isGenerating: true,
    });
    expect(breakdown.original_score).toBe(64);
    expect(breakdown.tailored_score).toBeNull();
    expect(breakdown.calculation_status).toBe("calculating");
    expect(breakdown.score_delta).toBeNull();
  });

  it("after completion returns original, tailored, and delta", () => {
    const breakdown = buildScoreBreakdown({
      result: baseResult({
        original_match_score: 64,
        tailored_match_score: 72,
        score_breakdown: {
          original_score: 64,
          tailored_score: 72,
          score_delta: 8,
          calculation_status: "complete",
          improved_because: ["React emphasized"],
          still_missing: ["TypeScript"],
        },
      }),
      isGenerating: false,
    });
    expect(breakdown.original_score).toBe(64);
    expect(breakdown.tailored_score).toBe(72);
    expect(breakdown.score_delta).toBe(8);
    expect(breakdown.still_missing).toContain("TypeScript");
  });

  it("failed calculation status is preserved", () => {
    const breakdown = buildScoreBreakdown({
      generationReport: {
        score_breakdown: {
          original_score: 64,
          tailored_score: null,
          calculation_status: "failed",
        },
      },
      originalBaseline: 64,
      isGenerating: false,
    });
    expect(breakdown.calculation_status).toBe("failed");
    expect(breakdown.tailored_score).toBeNull();
  });
});
