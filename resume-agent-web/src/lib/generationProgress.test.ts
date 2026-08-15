import { describe, expect, it } from "vitest";
import {
  applyStageEventToAgents,
  buildProgressSnapshot,
  computeWeightedProgress,
  isTailorGenerating,
  localizeAgentMessage,
  markAgentsCompletedIfIdle,
  resolveActiveTailoredCv,
  resolveMergedStage,
  STAGE_ORDER,
  STAGE_WEIGHTS,
  type TailorAgentState,
} from "./generationProgress";

function agents(status: TailorAgentState["status"][] = []): TailorAgentState[] {
  return STAGE_ORDER.map((id, i) => ({
    id,
    label: id,
    message: "…",
    status: status[i] || "pending",
    progress: status[i] === "completed" ? 100 : 0,
  }));
}

describe("generation progress source of truth (1 smart agent)", () => {
  it("stage weights sum to 100", () => {
    const sum = Object.values(STAGE_WEIGHTS).reduce((a, b) => a + b, 0);
    expect(sum).toBe(100);
  });

  it("exposes exactly one stage", () => {
    expect(STAGE_ORDER).toHaveLength(1);
    expect(STAGE_ORDER[0]).toBe("smart_resume_agent");
  });

  it("completed count and percent come from the same agent list", () => {
    const list = agents(["running"]);
    const snap = buildProgressSnapshot(list, { active: true });
    expect(snap.completedCount).toBe(0);
    expect(snap.totalAgents).toBe(1);
    expect(snap.overallProgress).toBe(computeWeightedProgress(list));
    expect(snap.stageOfLabel).toBe("Stage 1 of 1");
  });

  it("maps legacy / four-agent SSE ids onto the smart agent", () => {
    expect(resolveMergedStage("evidence_mapping")).toBe("smart_resume_agent");
    expect(resolveMergedStage("human_writer")).toBe("smart_resume_agent");
    expect(resolveMergedStage("final_polish")).toBe("smart_resume_agent");
    expect(resolveMergedStage("candidate_opportunity_intelligence")).toBe(
      "smart_resume_agent"
    );
  });

  it("localizes English SSE messages to Hebrew when possible", () => {
    expect(localizeAgentMessage("Analyzing the job description…", "fallback")).toMatch(
      /מנתח/
    );
  });

  it("applyStageEvent marks the smart agent running via legacy ids", () => {
    let list = agents();
    list = applyStageEventToAgents(list, {
      stage: "evidence_mapping",
      status: "started",
      message: "Mapping resume evidence to requirements…",
    });
    expect(list[0].status).toBe("running");
    expect(list[0].id).toBe("smart_resume_agent");
    expect(list[0].progress).toBe(0); // indeterminate — no fake percent
  });
});

describe("cross-job tailor session state", () => {
  it("treats any in-flight tailoringId as generating", () => {
    expect(
      isTailorGenerating({ regenerating: false, tailoringId: 2 })
    ).toBe(true);
    expect(
      isTailorGenerating({ regenerating: true, tailoringId: null })
    ).toBe(true);
    expect(
      isTailorGenerating({ regenerating: false, tailoringId: null })
    ).toBe(false);
  });

  it("hides a stale draft from another job while a new tailor runs", () => {
    const draftA = { job_id: 1 };
    expect(resolveActiveTailoredCv(draftA, 2)).toBeNull();
    expect(resolveActiveTailoredCv(draftA, 1)).toEqual(draftA);
    expect(resolveActiveTailoredCv(draftA, null)).toEqual(draftA);
    expect(resolveActiveTailoredCv(null, 2)).toBeNull();
  });

  it("marks idle agents completed when a run finished without SSE stages", () => {
    const idle: TailorAgentState[] = STAGE_ORDER.map((id) => ({
      id,
      label: id,
      message: "",
      status: "pending",
      progress: 0,
    }));
    const done = markAgentsCompletedIfIdle(idle, {
      active: false,
      complete: true,
    });
    expect(done.every((a) => a.status === "completed")).toBe(true);
    expect(
      markAgentsCompletedIfIdle(idle, { active: true, complete: false })
    ).toEqual(idle);
  });
});
