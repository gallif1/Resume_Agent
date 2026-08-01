import { describe, expect, it } from "vitest";
import {
  applyStageEventToAgents,
  buildProgressSnapshot,
  computeWeightedProgress,
  localizeAgentMessage,
  STAGE_ORDER,
  STAGE_WEIGHTS,
} from "./generationProgress";
import type { TailorAgentState } from "./generationProgress";

function agents(status: TailorAgentState["status"][] = []): TailorAgentState[] {
  return STAGE_ORDER.map((id, i) => ({
    id,
    label: id,
    message: "…",
    status: status[i] || "pending",
    progress: status[i] === "completed" ? 100 : 0,
  }));
}

describe("generation progress source of truth", () => {
  it("stage weights sum to 100", () => {
    const sum = Object.values(STAGE_WEIGHTS).reduce((a, b) => a + b, 0);
    expect(sum).toBe(100);
  });

  it("completed count and percent come from the same agent list", () => {
    const list = agents([
      "completed",
      "completed",
      "completed",
      "completed",
      "completed",
      "running",
      "pending",
      "pending",
      "pending",
      "pending",
      "pending",
    ]);
    const snap = buildProgressSnapshot(list, { active: true });
    expect(snap.completedCount).toBe(5);
    expect(snap.totalAgents).toBe(11);
    expect(snap.overallProgress).toBe(computeWeightedProgress(list));
    // 5/11 agents ≠ equal-weight 45% — weighted progress must stay consistent
    expect(snap.overallProgress).not.toBe(
      Math.round((snap.completedCount / snap.totalAgents) * 100)
    );
  });

  it("localizes English SSE messages to Hebrew when possible", () => {
    expect(localizeAgentMessage("Analyzing the job description…", "fallback")).toMatch(
      /מנתח/
    );
  });

  it("applyStageEvent marks prior agents completed", () => {
    let list = agents();
    list = applyStageEventToAgents(list, {
      stage: "evidence_mapping",
      status: "started",
      message: "Mapping resume evidence to requirements…",
    });
    expect(list[0].status).toBe("completed");
    expect(list[3].status).toBe("running");
    expect(list[3].progress).toBe(0); // indeterminate — no fake percent
  });
});
