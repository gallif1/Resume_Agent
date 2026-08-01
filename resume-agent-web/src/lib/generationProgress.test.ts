import { describe, expect, it } from "vitest";
import {
  applyStageEventToAgents,
  buildProgressSnapshot,
  computeWeightedProgress,
  localizeAgentMessage,
  resolveMergedStage,
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

describe("generation progress source of truth (single-agent stages)", () => {
  it("stage weights sum to 100", () => {
    const sum = Object.values(STAGE_WEIGHTS).reduce((a, b) => a + b, 0);
    expect(sum).toBe(100);
  });

  it("exposes exactly three stages", () => {
    expect(STAGE_ORDER).toHaveLength(3);
    expect(STAGE_ORDER[0]).toBe("prepare_evidence");
    expect(STAGE_ORDER[1]).toBe("resume_generation_agent");
    expect(STAGE_ORDER[2]).toBe("final_hiring_ats_page");
  });

  it("completed count and percent come from the same agent list", () => {
    const list = agents(["completed", "running", "pending"]);
    const snap = buildProgressSnapshot(list, { active: true });
    expect(snap.completedCount).toBe(1);
    expect(snap.totalAgents).toBe(3);
    expect(snap.overallProgress).toBe(computeWeightedProgress(list));
    expect(snap.stageOfLabel).toBe("Stage 2 of 3");
  });

  it("maps legacy / four-agent SSE ids onto current stages", () => {
    expect(resolveMergedStage("evidence_mapping")).toBe("prepare_evidence");
    expect(resolveMergedStage("human_writer")).toBe("resume_generation_agent");
    expect(resolveMergedStage("final_polish")).toBe("final_hiring_ats_page");
    expect(resolveMergedStage("candidate_opportunity_intelligence")).toBe(
      "prepare_evidence"
    );
  });

  it("localizes English SSE messages to Hebrew when possible", () => {
    expect(localizeAgentMessage("Analyzing the job description…", "fallback")).toMatch(
      /מנתח/
    );
  });

  it("applyStageEvent marks prior stages completed via legacy ids", () => {
    let list = agents();
    list = applyStageEventToAgents(list, {
      stage: "evidence_mapping",
      status: "started",
      message: "Mapping resume evidence to requirements…",
    });
    expect(list[0].status).toBe("running");
    expect(list[0].id).toBe("prepare_evidence");
    expect(list[0].progress).toBe(0); // indeterminate — no fake percent
  });
});
