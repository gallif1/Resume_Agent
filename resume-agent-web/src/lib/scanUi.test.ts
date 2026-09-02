import { describe, expect, it } from "vitest";
import { applyInlinePipelineStageHints, type PipelineStageId } from "./scanUi";

function stages(
  statuses: Partial<Record<PipelineStageId, "pending" | "running" | "success">>
) {
  return (["parse", "strategy", "collect", "enrich", "match"] as const).map(
    (id) => ({
      id,
      status: statuses[id] ?? ("pending" as const),
    })
  );
}

describe("applyInlinePipelineStageHints", () => {
  it("marks enrich and match running once live matches stream during collect", () => {
    const result = applyInlinePipelineStageHints(
      stages({ collect: "running" }),
      { running: true, liveMatchCount: 3 }
    );
    expect(result.find((s) => s.id === "enrich")?.status).toBe("running");
    expect(result.find((s) => s.id === "match")?.status).toBe("running");
    expect(result.find((s) => s.id === "collect")?.status).toBe("running");
  });

  it("does not change stages before any fully-processed job appears", () => {
    const input = stages({ collect: "running" });
    expect(
      applyInlinePipelineStageHints(input, { running: true, liveMatchCount: 0 })
    ).toEqual(input);
  });

  it("leaves enrich/match alone when scan is not running", () => {
    const input = stages({ collect: "success", enrich: "pending" });
    expect(
      applyInlinePipelineStageHints(input, {
        running: false,
        liveMatchCount: 10,
      })
    ).toEqual(input);
  });
});
