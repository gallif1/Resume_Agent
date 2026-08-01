import { useCallback, useEffect, useId, useRef, useState } from "react";
import { X } from "lucide-react";
import type {
  GenerationReport,
  TailorDecision,
  TailorStageEvent,
  TailoredCvResponse,
} from "../lib/api";
import TailorGenerationProgress from "./TailorGenerationProgress";
import { buildScoreBreakdown } from "../lib/tailorScores";

export type CloseChoice = "stay" | "background" | "cancel";

interface Props {
  open: boolean;
  active: boolean;
  title?: string;
  jobLabel?: string | null;
  stages: TailorStageEvent[];
  decisions: TailorDecision[];
  statusMessage?: string | null;
  generationReport?: GenerationReport | null;
  result?: TailoredCvResponse | null;
  originalBaseline?: number | null;
  elapsedSeconds?: number | null;
  supportsBackground?: boolean;
  onRequestClose: () => void;
  onConfirmClose: (choice: CloseChoice) => void;
  onPreview?: () => void;
  onContinueWatching?: () => void;
}

/**
 * Full-screen / sheet modal for live resume generation.
 * Sticky safe-area header + bottom action bar so close is always tappable on mobile.
 */
export default function GenerationLiveModal({
  open,
  active,
  title = "יצירת קורות חיים מותאמים",
  jobLabel,
  stages,
  decisions,
  statusMessage,
  generationReport,
  result = null,
  originalBaseline = null,
  elapsedSeconds = null,
  supportsBackground = true,
  onRequestClose,
  onConfirmClose,
  onPreview,
  onContinueWatching,
}: Props) {
  const titleId = useId();
  const closeBtnRef = useRef<HTMLButtonElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const activeRef = useRef(active);
  activeRef.current = active;

  const score = buildScoreBreakdown({
    result,
    generationReport,
    originalBaseline,
    isGenerating: active,
  });

  const handleCloseAttempt = useCallback(() => {
    if (activeRef.current) {
      setConfirmOpen(true);
      return;
    }
    onRequestClose();
  }, [onRequestClose]);

  useEffect(() => {
    if (!open) {
      setConfirmOpen(false);
      return;
    }
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const t = window.setTimeout(() => closeBtnRef.current?.focus(), 50);

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        handleCloseAttempt();
      }
    };
    window.addEventListener("keydown", onKey);

    const onPopState = () => {
      window.history.pushState({ generationModal: true }, "");
      handleCloseAttempt();
    };
    window.history.pushState({ generationModal: true }, "");
    window.addEventListener("popstate", onPopState);

    return () => {
      window.clearTimeout(t);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("popstate", onPopState);
      document.body.style.overflow = prevOverflow;
      previouslyFocused.current?.focus?.();
    };
  }, [open, handleCloseAttempt]);

  if (!open) return null;

  function resolveConfirm(choice: CloseChoice) {
    setConfirmOpen(false);
    if (choice === "stay") return;
    onConfirmClose(choice);
  }

  const showCompletion = !active && !!generationReport;

  return (
    <div
      className="generation-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
    >
      <div className="generation-modal" dir="rtl">
        <header className="generation-modal-header">
          <div className="generation-modal-heading">
            <h2 id={titleId}>{title}</h2>
            {jobLabel ? <p className="generation-modal-sub">{jobLabel}</p> : null}
            {active && elapsedSeconds != null && (
              <p className="generation-modal-elapsed" aria-live="off">
                {elapsedSeconds}s
              </p>
            )}
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            className="generation-modal-close touch-target"
            onClick={handleCloseAttempt}
            aria-label="סגור"
          >
            <X size={22} aria-hidden="true" />
            <span>סגור</span>
          </button>
        </header>

        <div className="generation-modal-body">
          <TailorGenerationProgress
            active={active}
            stages={stages}
            decisions={decisions}
            statusMessage={statusMessage}
            generationReport={generationReport}
            originalBaseline={originalBaseline}
            scoreBreakdown={score}
            showCompletion={showCompletion}
            onPreview={onPreview}
            onClose={handleCloseAttempt}
          />
        </div>

        <footer className="generation-modal-footer">
          {active ? (
            <>
              <button
                type="button"
                className="btn btn-ghost touch-target"
                onClick={() => resolveConfirm("background")}
              >
                הסתר / ברקע
              </button>
              <button
                type="button"
                className="btn btn-ghost touch-target"
                onClick={() => setConfirmOpen(true)}
              >
                בטל יצירה
              </button>
              <button
                type="button"
                className="btn btn-primary touch-target"
                onClick={onContinueWatching || (() => closeBtnRef.current?.focus())}
              >
                המשך לצפות
              </button>
            </>
          ) : (
            <>
              {onPreview && (
                <button
                  type="button"
                  className="btn btn-primary touch-target"
                  onClick={onPreview}
                >
                  תצוגה מקדימה
                </button>
              )}
              <button
                type="button"
                className="btn btn-ghost touch-target"
                onClick={handleCloseAttempt}
              >
                סגור
              </button>
            </>
          )}
        </footer>
      </div>

      {confirmOpen && (
        <div
          className="generation-confirm-overlay"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="generation-confirm-title"
        >
          <div className="generation-confirm" dir="rtl">
            <h3 id="generation-confirm-title">יצירת קורות החיים עדיין בתהליך</h3>
            <p>
              {supportsBackground
                ? "האם להמשיך ברקע, לבטל את היצירה, או להישאר כאן?"
                : "סגירה תבטל את הבקשה. אין תמיכה בהמשך ברקע בשרת זה."}
            </p>
            <div className="generation-confirm-actions">
              {supportsBackground && (
                <button
                  type="button"
                  className="btn btn-primary touch-target"
                  onClick={() => resolveConfirm("background")}
                >
                  המשך ברקע
                </button>
              )}
              <button
                type="button"
                className="btn btn-ghost touch-target"
                onClick={() => resolveConfirm("cancel")}
              >
                בטל יצירה
              </button>
              <button
                type="button"
                className="btn btn-ghost touch-target"
                onClick={() => resolveConfirm("stay")}
              >
                הישאר כאן
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
