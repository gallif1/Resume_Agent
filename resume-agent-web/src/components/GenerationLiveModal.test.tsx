import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import GenerationLiveModal from "./GenerationLiveModal";
import TailorGenerationProgress, {
  applyStageEvent,
  initialAgents,
} from "./TailorGenerationProgress";

describe("GenerationLiveModal mobile close UX", () => {
  it("keeps close control visible and tappable at iPhone-sized viewport", () => {
    Object.defineProperty(window, "innerWidth", { writable: true, value: 390 });
    Object.defineProperty(window, "innerHeight", { writable: true, value: 844 });

    const onRequestClose = vi.fn();
    const onConfirmClose = vi.fn();

    render(
      <GenerationLiveModal
        open
        active={false}
        stages={[]}
        decisions={[]}
        generationReport={{
          resume_revisions: 2,
          generation_time_seconds: 38,
          score_breakdown: {
            original_score: 64,
            tailored_score: 72,
            score_delta: 8,
            calculation_status: "complete",
          },
        }}
        originalBaseline={64}
        onRequestClose={onRequestClose}
        onConfirmClose={onConfirmClose}
      />
    );

    const closeBtns = screen.getAllByRole("button", { name: "סגור" });
    expect(closeBtns.length).toBeGreaterThan(0);
    expect(closeBtns[0]).toBeVisible();
    fireEvent.click(closeBtns[0]);
    expect(onRequestClose).toHaveBeenCalled();
  });

  it("shows cancel confirmation while generation is active", () => {
    const onConfirmClose = vi.fn();
    render(
      <GenerationLiveModal
        open
        active
        stages={[]}
        decisions={[]}
        originalBaseline={64}
        onRequestClose={vi.fn()}
        onConfirmClose={onConfirmClose}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "סגור" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent(/יצירת קורות החיים עדיין בתהליך/);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "בטל יצירה" })
    );
    expect(onConfirmClose).toHaveBeenCalledWith("cancel");
  });

  it("Escape opens confirm while generating", () => {
    render(
      <GenerationLiveModal
        open
        active
        stages={[]}
        decisions={[]}
        onRequestClose={vi.fn()}
        onConfirmClose={vi.fn()}
      />
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(
      screen.getByRole("alertdialog")
    ).toBeInTheDocument();
  });

  it("shows original score during generation and not tailored", () => {
    render(
      <GenerationLiveModal
        open
        active
        stages={[]}
        decisions={[]}
        originalBaseline={64}
        onRequestClose={vi.fn()}
        onConfirmClose={vi.fn()}
      />
    );
    expect(screen.getByText("64%")).toBeInTheDocument();
    expect(screen.getByText(/מחשב אחרי אימות סופי/)).toBeInTheDocument();
  });

  it("completion report shows improvement delta", () => {
    render(
      <GenerationLiveModal
        open
        active={false}
        stages={[]}
        decisions={[]}
        generationReport={{
          resume_revisions: 2,
          generation_time_seconds: 38,
          score_breakdown: {
            original_score: 64,
            tailored_score: 72,
            score_delta: 8,
            calculation_status: "complete",
          },
        }}
        originalBaseline={64}
        onRequestClose={vi.fn()}
        onConfirmClose={vi.fn()}
        onPreview={vi.fn()}
      />
    );
    expect(screen.getByText(/קורות החיים מוכנים/)).toBeInTheDocument();
    expect(screen.getByText("+8")).toBeInTheDocument();
  });

  it("shows הצג PDF and preview actions when result is ready", () => {
    const onViewPdf = vi.fn();
    const onPreview = vi.fn();
    render(
      <GenerationLiveModal
        open
        active={false}
        stages={[]}
        decisions={[]}
        generationReport={{
          resume_revisions: 1,
          generation_time_seconds: 12,
          score_breakdown: {
            original_score: 50,
            tailored_score: 60,
            score_delta: 10,
            calculation_status: "complete",
          },
        }}
        result={{
          cv_id: "cv1",
          job_id: 7,
          title: "Engineer",
          company: "Acme",
          markdown: "# CV",
          highlights: [],
          caveats: [],
          from_cache: false,
          saved_path: "x.md",
        }}
        onRequestClose={vi.fn()}
        onConfirmClose={vi.fn()}
        onPreview={onPreview}
        onViewPdf={onViewPdf}
      />
    );

    const pdfButtons = screen.getAllByRole("button", { name: "הצג PDF" });
    expect(pdfButtons.length).toBeGreaterThan(0);
    fireEvent.click(pdfButtons[0]);
    expect(onViewPdf).toHaveBeenCalled();

    const previewButtons = screen.getAllByRole("button", {
      name: "תצוגה מקדימה",
    });
    fireEvent.click(previewButtons[0]);
    expect(onPreview).toHaveBeenCalled();
  });

  it("shows completion actions when result exists without generationReport", () => {
    render(
      <GenerationLiveModal
        open
        active={false}
        stages={[]}
        decisions={[]}
        result={{
          cv_id: "cv1",
          job_id: 7,
          title: "Engineer",
          company: "Acme",
          markdown: "# CV",
          highlights: [],
          caveats: [],
          from_cache: false,
          saved_path: "x.md",
        }}
        onRequestClose={vi.fn()}
        onConfirmClose={vi.fn()}
        onPreview={vi.fn()}
        onViewPdf={vi.fn()}
      />
    );
    expect(screen.getByText(/קורות החיים מוכנים/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "הצג PDF" }).length).toBeGreaterThan(
      0
    );
  });

  it("cache load shows saved-draft copy and not a fake 0/4 run", () => {
    render(
      <GenerationLiveModal
        open
        active={false}
        stages={[]}
        decisions={[]}
        generationReport={{
          from_cache: true,
          agents_completed: 4,
          agents_total: 4,
          resume_revisions: 3,
          generation_time_seconds: null,
          score_breakdown: {
            original_score: 70,
            tailored_score: 77,
            score_delta: 7,
            calculation_status: "complete",
          },
        }}
        result={{
          cv_id: "cv1",
          job_id: 7,
          title: "Backend Engineer",
          company: "TARC",
          markdown: "# CV",
          highlights: [],
          caveats: [],
          from_cache: true,
          saved_path: "x.md",
        }}
        originalBaseline={70}
        onRequestClose={vi.fn()}
        onConfirmClose={vi.fn()}
        onPreview={vi.fn()}
        onViewPdf={vi.fn()}
      />
    );
    expect(screen.getByText(/נטענו קורות חיים שמורים/)).toBeInTheDocument();
    expect(screen.getByText("4/4")).toBeInTheDocument();
    expect(screen.getByText("נטען מהשמור")).toBeInTheDocument();
    expect(screen.queryByText(/0 שניות/)).not.toBeInTheDocument();
  });
});

describe("expandable agent cards", () => {
  it("expands every agent card to reveal full message", () => {
    const longHe =
      "ממפה ראיות מפורטות מהניסיון התעסוקתי כולל React ו-REST API ומוודא שאין טענות לא נתמכות";
    const longEn =
      "Found strong evidence for React and REST API integration across multiple production services";
    let agents = initialAgents();
    agents = applyStageEvent(agents, {
      stage: "evidence_mapping",
      status: "completed",
      message: `${longHe}. ${longEn}`,
    });

    // Force stages path via controlled component — render with stages
    render(
      <TailorGenerationProgress
        active={false}
        stages={[
          {
            stage: "evidence_mapping",
            status: "completed",
            message: `${longHe}. ${longEn}`,
          },
        ]}
        decisions={[
          {
            stage: "evidence_mapping",
            text: longEn,
            action: "match",
          },
        ]}
        originalBaseline={64}
      />
    );

    const buttons = screen.getAllByRole("button", { expanded: false });
    expect(buttons.length).toBeGreaterThanOrEqual(4);
    for (const btn of buttons) {
      fireEvent.click(btn);
      expect(btn).toHaveAttribute("aria-expanded", "true");
      const panelId = btn.getAttribute("aria-controls");
      expect(panelId).toBeTruthy();
      const panel = document.getElementById(panelId!);
      expect(panel).toBeTruthy();
      expect(panel).not.toHaveAttribute("hidden");
    }
  });

  it("decision log offers view-all for more than 3 events", () => {
    render(
      <TailorGenerationProgress
        active
        stages={[]}
        decisions={[
          { text: "d1", stage: "evidence_mapping" },
          { text: "d2", stage: "claim_validation" },
          { text: "d3", stage: "senior_recruiter" },
          { text: "d4", stage: "hiring_manager" },
        ]}
      />
    );
    const toggle = screen.getByRole("button", { name: /הצג את כל ההחלטות/ });
    fireEvent.click(toggle);
    expect(screen.getAllByText("d1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("d4").length).toBeGreaterThan(0);
  });
});

describe("safe-area modal chrome", () => {
  it("generation modal header/footer classes exist for safe-area CSS", () => {
    const { container } = render(
      <GenerationLiveModal
        open
        active
        stages={[]}
        decisions={[]}
        onRequestClose={vi.fn()}
        onConfirmClose={vi.fn()}
      />
    );
    expect(
      container.querySelector(".generation-modal-header")
    ).toBeTruthy();
    expect(
      container.querySelector(".generation-modal-footer")
    ).toBeTruthy();
    expect(container.querySelector(".generation-modal-close")).toBeTruthy();
    const close = container.querySelector(".generation-modal-close")!;
    expect(close.className).toMatch(/touch-target/);
  });
});
