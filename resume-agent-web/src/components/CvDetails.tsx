import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import { ArrowRight, Search } from "lucide-react";
import {
  applyToJob,
  downloadTailoredCvPdf,
  DuplicateApplicationError,
  getCvMatches,
  getCvScanStatus,
  getJobMatches,
  getJobMatchStatus,
  getJobApplication,
  parseScanSummary,
  tailorCvForJob,
  tailorWorkspaceJob,
  updateMatchStatus,
  updateWorkspaceMatchStatus,
  type ApplicationStatus,
  type Cv,
  type CvMatch,
  type CvScanStatus,
  type JobApplication,
  type JobApplicationStatus,
  type MatchSortBy,
  type MatchSortOrder,
  type TailoredCvResponse,
} from "../lib/api";
import PipelineProgress from "./PipelineProgress";
import ProfileSettings from "./ProfileSettings";

interface Props {
  cvId: string;
  cv: Cv | undefined;
  scanStatus?: CvScanStatus | null;
  workspaceMode?: boolean;
  onBack: () => void;
}

const STATUS_OPTIONS: { value: ApplicationStatus; label: string }[] = [
  { value: "not_sent", label: "לא נשלחו קו\"ח" },
  { value: "sent", label: "נשלחו קו\"ח" },
  { value: "interested", label: "מעניין" },
  { value: "not_relevant", label: "לא רלוונטי" },
  { value: "applied_manually", label: "הוגש ידנית" },
];

const STATUS_LABEL: Record<ApplicationStatus, string> = Object.fromEntries(
  STATUS_OPTIONS.map((o) => [o.value, o.label])
) as Record<ApplicationStatus, string>;

const JOB_APP_STATUS_LABEL: Record<JobApplicationStatus, string> = {
  pending: "ממתין להגשה",
  in_progress: "מגיש…",
  submitted: "קורות החיים נשלחו",
  failed: "ההגשה נכשלה",
  requires_user_action: "נדרשת השלמה ידנית",
};

const SCORE_LABEL_HE: Record<string, string> = {
  "Excellent Match": "התאמה מצוינת",
  "Good Match": "התאמה טובה",
  "Partial Match": "התאמה חלקית",
  "Potential Match": "התאמה פוטנציאלית",
  "Weak Match": "התאמה חלשה",
};

interface ConfirmState {
  match: CvMatch;
  force?: boolean;
}

function scoreClass(score: number | null, isPotential = false): string {
  if (score == null) return "";
  if (isPotential && score < 50) return "score-potential";
  if (score >= 85) return "score-high";
  if (score >= 70) return "score-mid";
  return "score-low";
}

/** Best available ATS/match score from a tailored-CV payload. */
function getTailoredScore(result: TailoredCvResponse): number | null {
  if (typeof result.estimated_ats_score === "number") {
    return result.estimated_ats_score;
  }
  const fromFeedback = result.matcher_feedback?.current?.ats_score;
  return typeof fromFeedback === "number" ? fromFeedback : null;
}

const IMPROVE_MATCH_HELPER =
  "הבינה המלאכותית מלטשת את קורות החיים ומוסיפה מילות מפתח מתוך תיאור המשרה כדי להעלות את הציון במערכת הסינון (ATS).";

const STAGNANT_ATTEMPTS_BEFORE_MAX = 2;

function formatScoreLabel(label: string | null, isPotential = false): string | null {
  if (!label) {
    return isPotential ? SCORE_LABEL_HE["Potential Match"] : null;
  }
  if (isPotential && (label === "Weak Match" || label === "Potential Match")) {
    return SCORE_LABEL_HE["Potential Match"];
  }
  return SCORE_LABEL_HE[label] ?? label;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("he-IL", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d);
}

/** Parse board/API dates into a numeric timestamp for chronological sorting. */
function matchDateMs(match: CvMatch): number {
  const raw = (match.posted_date || match.job_created_at || "").trim();
  if (!raw) return 0;
  // YYYY-MM-DD → UTC midnight so lexicographic ISO dates sort as real dates.
  const normalized =
    /^\d{4}-\d{2}-\d{2}$/.test(raw) ? `${raw}T00:00:00.000Z` : raw.replace(" ", "T");
  const ms = Date.parse(normalized);
  return Number.isNaN(ms) ? 0 : ms;
}

function sortMatchesChronologically(
  items: CvMatch[],
  order: MatchSortOrder
): CvMatch[] {
  const dir = order === "asc" ? 1 : -1;
  return [...items].sort((a, b) => {
    const diff = matchDateMs(a) - matchDateMs(b);
    if (diff !== 0) return diff * dir;
    return (b.match_id ?? 0) - (a.match_id ?? 0);
  });
}

function isActiveApplication(status: JobApplicationStatus | undefined): boolean {
  return status === "pending" || status === "in_progress";
}

function isPotentialMatch(match: CvMatch): boolean {
  return Boolean(match.is_potential_junior_match) && (match.match_score ?? 0) < 50;
}

function downloadTextFile(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Prefer the resume body after `---` / "קורות החיים המעודכנים". */
function extractTailoredCvBody(markdown: string, cvMarkdown?: string | null): string {
  if (cvMarkdown?.trim()) return cvMarkdown.trim();
  const text = (markdown || "").trim();
  if (!text) return "";

  const hrSplit = text.split(/\n---\s*\n/);
  if (hrSplit.length >= 2) {
    let body = hrSplit.slice(1).join("\n---\n").trim();
    body = body.replace(
      /^##\s*(?:קורות החיים המעודכנים|The Tailored CV|Tailored CV)\s*\n+/i,
      ""
    );
    return body.trim() || text;
  }

  const headingMatch = text.match(
    /^##\s*(?:קורות החיים המעודכנים|The Tailored CV|Tailored CV)\s*$/im
  );
  if (headingMatch?.index != null) {
    return text.slice(headingMatch.index + headingMatch[0].length).trim() || text;
  }
  return text;
}

export default function CvDetails({
  cvId,
  cv,
  scanStatus = null,
  workspaceMode = false,
  onBack,
}: Props) {
  const [matches, setMatches] = useState<CvMatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<MatchSortBy>("score");
  const [sortOrder, setSortOrder] = useState<MatchSortOrder>("desc");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [applyingId, setApplyingId] = useState<number | null>(null);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [logApplication, setLogApplication] = useState<JobApplication | null>(null);
  const [activeTab, setActiveTab] = useState<"jobs" | "profile">("jobs");
  const [tailoringId, setTailoringId] = useState<number | null>(null);
  const [tailoredCv, setTailoredCv] = useState<TailoredCvResponse | null>(null);
  const [copyDone, setCopyDone] = useState(false);
  const [pdfDownloading, setPdfDownloading] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [infoMessage, setInfoMessage] = useState<string | null>(null);
  const [stagnantAttempts, setStagnantAttempts] = useState(0);
  const [maxMatchReached, setMaxMatchReached] = useState(false);
  const [previewAnimKey, setPreviewAnimKey] = useState(0);
  const [lastScanInfo, setLastScanInfo] = useState(() =>
    parseScanSummary(null)
  );
  const prevRunning = useRef(false);
  /** Session-best tailored draft so a lower-scoring regenerate never overwrites it. */
  const bestSessionRef = useRef<{
    jobId: number;
    score: number;
    result: TailoredCvResponse;
  } | null>(null);
  const running = scanStatus?.running ?? false;
  const isGenerating =
    regenerating ||
    (tailoringId != null &&
      (tailoredCv == null || tailoringId === tailoredCv.job_id));

  const { primaryMatches, potentialMatches } = useMemo(() => {
    const primary: CvMatch[] = [];
    const potential: CvMatch[] = [];
    for (const m of matches) {
      if (isPotentialMatch(m)) potential.push(m);
      else primary.push(m);
    }
    // Re-apply chronological compare on the client so date sort never falls
    // back to alphabetical string ordering after bucket splits.
    if (sortBy === "date") {
      return {
        primaryMatches: sortMatchesChronologically(primary, sortOrder),
        potentialMatches: sortMatchesChronologically(potential, sortOrder),
      };
    }
    return { primaryMatches: primary, potentialMatches: potential };
  }, [matches, sortBy, sortOrder]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const sortOpts = { latest: true as const, sortBy, order: sortOrder };
      const data = workspaceMode
        ? await getJobMatches(sortOpts)
        : await getCvMatches(cvId, sortOpts);
      setMatches(data.matches);
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בטעינת ההתאמות");
    } finally {
      setLoading(false);
    }
  }, [cvId, workspaceMode, sortBy, sortOrder]);

  const handleSortChange = (value: string) => {
    // Encoded as "field:order" so one dropdown covers the common sorts.
    const [field, direction] = value.split(":") as [MatchSortBy, MatchSortOrder];
    setSortBy(field);
    setSortOrder(direction);
  };

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    const fetchStatus = workspaceMode ? getJobMatchStatus : () => getCvScanStatus(cvId);
    fetchStatus()
      .then((status) => {
        if (cancelled) return;
        const parsed = parseScanSummary(status.latest_scan?.summary);
        if ((status.warnings?.length ?? 0) > 0) {
          parsed.warnings = status.warnings ?? parsed.warnings;
        }
        if (status.collection) {
          parsed.collection = status.collection;
        }
        setLastScanInfo(parsed);
      })
      .catch(() => {
        /* scan status optional */
      });
    return () => {
      cancelled = true;
    };
  }, [cvId, workspaceMode, running, scanStatus?.warnings, scanStatus?.collection]);

  useEffect(() => {
    if (prevRunning.current && !running) {
      load();
    }
    prevRunning.current = running;
  }, [running, load]);

  // Poll while any application is in progress.
  useEffect(() => {
    const hasActive = matches.some((m) =>
      isActiveApplication(m.job_application?.status)
    );
    if (!hasActive) return;

    const timer = window.setInterval(() => {
      load();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [matches, load]);

  const handleStatusChange = async (
    match: CvMatch,
    status: ApplicationStatus
  ) => {
    setSavingId(match.match_id);
    setMatches((prev) =>
      prev.map((m) =>
        m.match_id === match.match_id ? { ...m, application_status: status } : m
      )
    );
    try {
      if (workspaceMode) {
        await updateWorkspaceMatchStatus(match.match_id, status);
      } else {
        await updateMatchStatus(cvId, match.match_id, status);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בעדכון הסטטוס");
      load();
    } finally {
      setSavingId(null);
    }
  };

  const openConfirm = (match: CvMatch, force = false) => {
    setConfirmState({ match, force });
  };

  const handleApply = async (match: CvMatch, force = false) => {
    setApplyingId(match.job_id);
    setError(null);
    try {
      const result = await applyToJob(cvId, match.job_id, { force });
      setMatches((prev) =>
        prev.map((m) =>
          m.job_id === match.job_id
            ? { ...m, job_application: result.application }
            : m
        )
      );
      setConfirmState(null);
    } catch (e) {
      if (e instanceof DuplicateApplicationError) {
        openConfirm(match, true);
        setError(e.message);
      } else {
        setError(e instanceof Error ? e.message : "שגיאה בהגשת קורות החיים");
      }
    } finally {
      setApplyingId(null);
    }
  };

  const openApplicationLog = async (app: JobApplication) => {
    try {
      const full = await getJobApplication(cvId, app.application_id);
      setLogApplication(full);
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בטעינת יומן ההגשה");
    }
  };

  const trackSessionBest = (
    result: TailoredCvResponse,
    { resetSession = false }: { resetSession?: boolean } = {}
  ) => {
    const score = getTailoredScore(result) ?? 0;
    const current = bestSessionRef.current;
    if (
      resetSession ||
      !current ||
      current.jobId !== result.job_id ||
      score > current.score
    ) {
      bestSessionRef.current = {
        jobId: result.job_id,
        score,
        result,
      };
    }
    if (resetSession || !current || current.jobId !== result.job_id) {
      setStagnantAttempts(0);
      setMaxMatchReached(false);
      setInfoMessage(null);
    }
  };

  const applyTailoredResult = (
    result: TailoredCvResponse,
    { resetSession = false }: { resetSession?: boolean } = {}
  ) => {
    trackSessionBest(result, { resetSession });
    setTailoredCv(result);
    setPreviewAnimKey((k) => k + 1);
    setMatches((prev) =>
      prev.map((m) =>
        m.job_id === result.job_id
          ? {
              ...m,
              has_tailored_cv: true,
              tailored_cv_updated_at:
                result.generated_at ?? new Date().toISOString(),
            }
          : m
      )
    );
  };

  const handleTailorCv = async (match: CvMatch, force = false) => {
    setTailoringId(match.job_id);
    setError(null);
    setInfoMessage(null);
    setCopyDone(false);
    try {
      const result = workspaceMode
        ? await tailorWorkspaceJob(match.job_id, { force, sourceCvId: cvId })
        : await tailorCvForJob(cvId, match.job_id, { force });
      applyTailoredResult(result, { resetSession: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בהתאמת קורות החיים");
    } finally {
      setTailoringId(null);
    }
  };

  const handleCopyTailored = async () => {
    if (!tailoredCv?.markdown) return;
    const cvOnly = extractTailoredCvBody(
      tailoredCv.markdown,
      tailoredCv.cv_markdown
    );
    try {
      await navigator.clipboard.writeText(cvOnly);
      setCopyDone(true);
      window.setTimeout(() => setCopyDone(false), 2000);
    } catch {
      setError("לא ניתן להעתיק ללוח");
    }
  };

  const handleDownloadTailored = () => {
    if (!tailoredCv?.markdown) return;
    const cvOnly = extractTailoredCvBody(
      tailoredCv.markdown,
      tailoredCv.cv_markdown
    );
    const safeTitle = (tailoredCv.title || "job")
      .replace(/[^\w\u0590-\u05FF-]+/g, "_")
      .slice(0, 40);
    downloadTextFile(`cv-tailored-${safeTitle}-${tailoredCv.job_id}.md`, cvOnly);
  };

  const handleDownloadTailoredPdf = async () => {
    if (!tailoredCv) return;
    setPdfDownloading(true);
    setError(null);
    try {
      await downloadTailoredCvPdf(cvId, tailoredCv.job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בהורדת PDF");
    } finally {
      setPdfDownloading(false);
    }
  };

  const handleRegenerateOptimize = async () => {
    if (!tailoredCv || maxMatchReached || regenerating) return;

    const previous = tailoredCv;
    const sessionBest =
      bestSessionRef.current?.jobId === previous.job_id
        ? bestSessionRef.current
        : {
            jobId: previous.job_id,
            score: getTailoredScore(previous) ?? 0,
            result: previous,
          };
    bestSessionRef.current = sessionBest;

    setRegenerating(true);
    setError(null);
    setInfoMessage(null);
    setCopyDone(false);
    try {
      const result = workspaceMode
        ? await tailorWorkspaceJob(previous.job_id, {
            regenerate: true,
            sourceCvId: cvId,
          })
        : await tailorCvForJob(cvId, previous.job_id, {
            regenerate: true,
          });

      const newScore = getTailoredScore(result);
      const bestScore = sessionBest.score;
      const scoreDropped =
        newScore != null && Number.isFinite(bestScore) && newScore < bestScore;
      const scoreUnchanged =
        newScore != null && Number.isFinite(bestScore) && newScore === bestScore;
      const backendNoGain =
        Boolean(result.no_improvement) ||
        result.message === "לא הצלחתי לייצר גרסה יותר טובה";

      if (scoreDropped) {
        // Keep the session-best layout text; never overwrite with a degraded draft.
        setTailoredCv(sessionBest.result);
        setInfoMessage(
          "הגרסה החדשה הורידה את ציון ההתאמה — שמרנו את הגרסה הטובה ביותר מהסשן."
        );
        const nextStagnant = stagnantAttempts + 1;
        setStagnantAttempts(nextStagnant);
        if (nextStagnant >= STAGNANT_ATTEMPTS_BEFORE_MAX) {
          setMaxMatchReached(true);
        }
        return;
      }

      if (backendNoGain || scoreUnchanged) {
        // Backend may return the previous best; retain our session-best markdown.
        setTailoredCv(sessionBest.result);
        const nextStagnant = stagnantAttempts + 1;
        setStagnantAttempts(nextStagnant);
        if (nextStagnant >= STAGNANT_ATTEMPTS_BEFORE_MAX || backendNoGain) {
          setMaxMatchReached(true);
          setInfoMessage("הגעת להתאמה מקסימלית");
        } else {
          setInfoMessage(
            result.message || "הציון לא השתנה — ניתן לנסות שוב לשיפור קל."
          );
        }
        return;
      }

      setStagnantAttempts(0);
      applyTailoredResult(result);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "שגיאה בשיפור קורות החיים המותאמים"
      );
    } finally {
      setRegenerating(false);
    }
  };

  const handleForceRegenerate = async () => {
    if (!tailoredCv || regenerating || tailoringId != null) return;
    setTailoringId(tailoredCv.job_id);
    setError(null);
    setInfoMessage(null);
    setCopyDone(false);
    try {
      const result = workspaceMode
        ? await tailorWorkspaceJob(tailoredCv.job_id, {
            force: true,
            sourceCvId: cvId,
          })
        : await tailorCvForJob(cvId, tailoredCv.job_id, {
            force: true,
          });
      applyTailoredResult(result, { resetSession: true });
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "שגיאה בהתאמת קורות החיים"
      );
    } finally {
      setTailoringId(null);
    }
  };

  const renderApplyButton = (match: CvMatch) => {
    const app = match.job_application;
    const status = app?.status;
    const busy = applyingId === match.job_id || isActiveApplication(status);

    if (status === "submitted") {
      return (
        <div className="apply-status-group">
          <span className="apply-status apply-status-success">
            קורות החיים נשלחו
          </span>
          {app?.submitted_at && (
            <span className="apply-status-date">{formatDate(app.submitted_at)}</span>
          )}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => openConfirm(match, true)}
          >
            הגש שוב
          </button>
        </div>
      );
    }

    if (status === "failed") {
      return (
        <div className="apply-status-group">
          <span className="apply-status apply-status-failed">
            ההגשה נכשלה – נסה שוב
          </span>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={busy}
            onClick={() => openConfirm(match)}
          >
            נסה שוב
          </button>
        </div>
      );
    }

    if (status === "requires_user_action") {
      return (
        <div className="apply-status-group">
          <span className="apply-status apply-status-warning">
            נדרשת השלמה ידנית
          </span>
          {(app?.current_step_url || match.job_url) && (
            <a
              className="btn btn-primary btn-sm"
              href={app?.current_step_url || match.job_url || "#"}
              target="_blank"
              rel="noreferrer"
            >
              המשך ידנית ↗
            </a>
          )}
        </div>
      );
    }

    if (busy) {
      return (
        <button type="button" className="btn btn-primary btn-sm" disabled>
          מגיש…
        </button>
      );
    }

    return (
      <button
        type="button"
        className="btn btn-primary btn-sm"
        onClick={() => openConfirm(match)}
      >
        הגש קורות חיים
      </button>
    );
  };

  const renderMatchCard = (m: CvMatch) => {
    const expanded = expandedId === m.match_id;
    const app = m.job_application;
    const potential = isPotentialMatch(m) || Boolean(m.is_potential_junior_match);
    const label = formatScoreLabel(m.score_label, potential);
    const busyTailor = tailoringId === m.job_id;

    return (
      <li key={m.match_id} className={`cv-item job-item ${potential ? "job-item-potential" : ""}`}>
        <div
          className="job-row"
          onClick={() => setExpandedId(expanded ? null : m.match_id)}
        >
          <div className="job-row-main">
            <span className={`job-score ${scoreClass(m.match_score, potential)}`}>
              <span className="job-score-value">{m.match_score ?? "—"}</span>
              {label && <span className="score-label">{label}</span>}
            </span>
            <div className="cv-info">
              <div className="cv-name">{m.title}</div>
              <div className="cv-meta">
                {[m.company, m.location, m.source].filter(Boolean).join(" · ")}
              </div>
              {potential && (
                <span className="potential-pill">התאמה פוטנציאלית</span>
              )}
              <span className={`status-pill status-${m.application_status}`}>
                {STATUS_LABEL[m.application_status]}
              </span>
              {app && (
                <span className={`apply-pill apply-pill-${app.status}`}>
                  {JOB_APP_STATUS_LABEL[app.status]}
                </span>
              )}
              {app?.updated_at && (
                <span className="cv-meta apply-attempt-date">
                  ניסיון אחרון: {formatDate(app.updated_at)}
                </span>
              )}
            </div>
          </div>
          <div className="cv-actions" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className={`btn btn-ghost btn-sm ${busyTailor ? "btn-loading" : ""}`}
              disabled={busyTailor}
              onClick={() => handleTailorCv(m)}
              aria-busy={busyTailor}
            >
              {busyTailor ? (
                <>
                  <span className="btn-spinner" aria-hidden="true" />
                  מייצר קורות חיים...
                </>
              ) : (
                "ייצר קורות חיים"
              )}
            </button>
            {renderApplyButton(m)}
            {app && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => openApplicationLog(app)}
              >
                פרטי הגשה
              </button>
            )}
            <select
              className="status-select"
              value={m.application_status}
              disabled={savingId === m.match_id}
              onChange={(e) =>
                handleStatusChange(m, e.target.value as ApplicationStatus)
              }
            >
              {STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            {m.job_url && (
              <a
                className="btn btn-ghost"
                href={m.job_url}
                target="_blank"
                rel="noreferrer"
              >
                למשרה ↗
              </a>
            )}
          </div>
        </div>

        {busyTailor && !tailoredCv && (
          <div
            className="cv-generating-feedback cv-generating-feedback-card"
            role="status"
            aria-live="polite"
          >
            <div className="cv-generating-feedback-pulse" aria-hidden="true" />
            <p className="cv-generating-feedback-title">
              סוכן ה-AI מנתח את תיאור המשרה ומנסח עבורך קורות חיים מותאמים
              במיוחד...
            </p>
            <p className="cv-generating-feedback-sub">
              התהליך עשוי לקחת מספר שניות, אנא המתן בזמן שאנו משפרים את סיכויי
              הקבלה שלך.
            </p>
          </div>
        )}

        {expanded && (
          <div className="job-details">
            <div className="job-description-block">
              <h4 className="job-description-title">תיאור המשרה</h4>
              {m.description?.trim() ? (
                <pre className="job-description-text" dir="rtl" lang="he">
                  {m.description.trim()}
                </pre>
              ) : (
                <p className="cv-meta">אין תיאור מלא למשרה זו</p>
              )}
            </div>
            {m.has_tailored_cv && (
              <p className="cv-meta">
                קורות חיים מותאמים נשמרו
                {m.tailored_cv_updated_at
                  ? ` · עודכן ${formatDate(m.tailored_cv_updated_at)}`
                  : ""}
              </p>
            )}
            {app?.failure_reason && (
              <p className="apply-log-error">
                <b>שגיאת הגשה:</b> {app.failure_reason}
              </p>
            )}
            {m.updated_at && (
              <p className="cv-meta">עודכן: {formatDate(m.updated_at)}</p>
            )}
          </div>
        )}
      </li>
    );
  };

  const title = workspaceMode
    ? "התאמות משרה (פרופיל מאוחד)"
    : cv?.display_name || cv?.file_name || "קורות חיים";
  const liveWarnings = scanStatus?.warnings ?? [];
  const liveCollection = scanStatus?.collection ?? null;
  const activeScan =
    scanStatus?.running ||
    scanStatus?.error ||
    liveWarnings.length > 0 ||
    Boolean(liveCollection)
      ? scanStatus
      : null;
  const displayWarnings =
    liveWarnings.length > 0 ? liveWarnings : lastScanInfo.warnings;
  const displayCollection = liveCollection ?? lastScanInfo.collection;
  const showScanPanel =
    activeScan ??
    (displayWarnings.length > 0 || displayCollection
      ? ({
          running: false,
          started_at: null,
          finished_at: null,
          error: null,
          warnings: displayWarnings,
          collection: displayCollection,
          current_step: null,
          detail: null,
          steps: [],
          log: [],
          latest_scan: null,
        } satisfies CvScanStatus)
      : null);

  return (
    <section>
      <div className="details-topbar">
        <button className="btn btn-ghost" onClick={onBack}>
          <ArrowRight size={16} aria-hidden />
          חזרה
        </button>
        <div className="details-title">
          <h2>{title}</h2>
          {workspaceMode ? (
            <span className="cv-meta">מבוסס על כל קבצי קורות החיים שהועלו</span>
          ) : (
            cv?.last_scan_at && (
              <span className="cv-meta">סריקה אחרונה: {formatDate(cv.last_scan_at)}</span>
            )
          )}
        </div>
        <div className="details-topbar-spacer" />
      </div>

      <div className="details-tabs" role="tablist" aria-label="תצוגת קורות חיים">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "jobs"}
          className={`details-tab ${activeTab === "jobs" ? "active" : ""}`}
          onClick={() => setActiveTab("jobs")}
        >
          משרות
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "profile"}
          className={`details-tab ${activeTab === "profile" ? "active" : ""}`}
          onClick={() => setActiveTab("profile")}
        >
          פרופיל
        </button>
      </div>

      {activeTab === "profile" ? (
        <ProfileSettings cvId={cvId} />
      ) : (
        <>
      <PipelineProgress scanStatus={showScanPanel} />

      {error && (
        <div
          className={
            error === "לא הצלחתי לייצר גרסה יותר טובה" ||
            error === "הגעת להתאמה מקסימלית"
              ? "warning-box"
              : "error-box"
          }
          role="status"
        >
          {error}
        </div>
      )}
      {infoMessage && !error && (
        <div className="warning-box" role="status">
          {infoMessage}
        </div>
      )}

      {confirmState && (
        <div className="modal-overlay" role="dialog" aria-modal="true">
          <div className="modal apply-confirm-modal">
            <h3>אישור הגשת קורות חיים</h3>
            <p>
              המערכת עומדת לפתוח את אתר המשרה החיצוני ולהגיש את פרטיך וקורות החיים
              השמורים.
            </p>
            <div className="apply-confirm-details">
              <p><b>משרה:</b> {confirmState.match.title}</p>
              <p><b>חברה:</b> {confirmState.match.company || "—"}</p>
            </div>
            {confirmState.force && (
              <p className="apply-confirm-warning">
                כבר הוגשו קורות חיים למשרה זו. האם להמשיך בהגשה חוזרת?
              </p>
            )}
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setConfirmState(null)}
              >
                ביטול
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={applyingId !== null}
                onClick={() => handleApply(confirmState.match, confirmState.force)}
              >
                {confirmState.force ? "הגש שוב" : "הגש קורות חיים"}
              </button>
            </div>
          </div>
        </div>
      )}

      {logApplication && (
        <div className="modal-overlay" role="dialog" aria-modal="true">
          <div className="modal apply-log-modal">
            <h3>יומן הגשה</h3>
            <p className="cv-meta">
              סטטוס: {JOB_APP_STATUS_LABEL[logApplication.status]} · ניסיון{" "}
              {logApplication.attempt_number ?? 1}
            </p>
            {logApplication.failure_reason && (
              <p className="apply-log-error">{logApplication.failure_reason}</p>
            )}
            <ul className="apply-log-steps">
              {(logApplication.steps ?? []).map((step) => (
                <li key={step.id} className={`apply-log-step step-${step.status}`}>
                  <span className="apply-log-step-name">{step.step_name}</span>
                  <span className="apply-log-step-status">{step.status}</span>
                  {step.message && (
                    <span className="apply-log-step-message">{step.message}</span>
                  )}
                  <span className="apply-log-step-time">{formatDate(step.created_at)}</span>
                </li>
              ))}
            </ul>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setLogApplication(null)}
              >
                סגור
              </button>
            </div>
          </div>
        </div>
      )}

      {tailoredCv && (
        <div className="modal-overlay" role="dialog" aria-modal="true">
          <div className="modal tailored-cv-modal" dir="rtl">
            <div className="tailored-cv-header">
              <div>
                <h3>קורות חיים מותאמים למשרה</h3>
                <p className="cv-meta">
                  {[tailoredCv.title, tailoredCv.company].filter(Boolean).join(" · ")}
                  {tailoredCv.from_cache ? " · מטמון" : ""}
                </p>
              </div>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setTailoredCv(null);
                  setInfoMessage(null);
                }}
                disabled={isGenerating}
              >
                סגור
              </button>
            </div>
            {(tailoredCv.estimated_ats_score != null ||
              (tailoredCv.changes_breakdown?.length ?? 0) > 0) && (
              <p className="tailored-cv-meta">
                {tailoredCv.estimated_ats_score != null && (
                  <>
                    <b>ציון משוער:</b> {tailoredCv.estimated_ats_score}/100
                    {tailoredCv.matcher_feedback?.previous?.ats_score != null &&
                    tailoredCv.regenerated ? (
                      <span className="tailored-cv-score-delta">
                        {" "}
                        (קודם: {tailoredCv.matcher_feedback.previous.ats_score}
                        /100)
                      </span>
                    ) : null}
                    {(tailoredCv.changes_breakdown?.length ?? 0) > 0 ? " · " : ""}
                  </>
                )}
                {(tailoredCv.changes_breakdown?.length ?? 0) > 0 && (
                  <span className="cv-meta">פירוט השינויים בגוף המסמך למטה</span>
                )}
              </p>
            )}
            {(tailoredCv.matcher_feedback?.current?.missing_keywords?.length ??
              0) > 0 &&
              tailoredCv.regenerated && (
                <p className="tailored-cv-meta tailored-cv-gaps">
                  <b>פערים שנותרו:</b>{" "}
                  {tailoredCv.matcher_feedback?.current?.missing_keywords
                    ?.slice(0, 8)
                    .join(" · ")}
                </p>
              )}
            {(tailoredCv.caveats?.length ?? 0) > 0 && (
              <p className="tailored-cv-meta tailored-cv-caveats">
                <b>הערות כנות:</b> {tailoredCv.caveats.join(" · ")}
              </p>
            )}
            {infoMessage && (
              <p className="tailored-cv-meta tailored-cv-info" role="status">
                {infoMessage}
              </p>
            )}
            {isGenerating && (
              <div
                className="cv-generating-feedback"
                role="status"
                aria-live="polite"
              >
                <div className="cv-generating-feedback-pulse" aria-hidden="true" />
                <p className="cv-generating-feedback-title">
                  סוכן ה-AI מנתח את תיאור המשרה ומנסח עבורך קורות חיים מותאמים
                  במיוחד...
                </p>
                <p className="cv-generating-feedback-sub">
                  התהליך עשוי לקחת מספר שניות, אנא המתן בזמן שאנו משפרים את סיכויי
                  הקבלה שלך.
                </p>
              </div>
            )}
            <div
              key={previewAnimKey}
              className={`tailored-cv-body ${isGenerating ? "tailored-cv-body-dimmed" : "tailored-cv-body-fade-in"}`}
              dir="auto"
            >
              <Markdown>{tailoredCv.markdown}</Markdown>
            </div>
            <div className="improve-match-block">
              <div className="modal-actions modal-actions-improve">
                <button
                  type="button"
                  className={`btn btn-ghost btn-regenerate-optimize ${regenerating ? "btn-loading" : ""}`}
                  onClick={handleRegenerateOptimize}
                  title={IMPROVE_MATCH_HELPER}
                  aria-describedby="improve-match-helper"
                  disabled={
                    maxMatchReached ||
                    regenerating ||
                    pdfDownloading ||
                    tailoringId === tailoredCv.job_id
                  }
                  aria-busy={regenerating}
                >
                  <span className="btn-regen-icon" aria-hidden="true">
                    {regenerating ? (
                      <span className="btn-spinner" />
                    ) : (
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                        <path
                          d="M21 12a9 9 0 1 1-2.64-6.36"
                          stroke="currentColor"
                          strokeWidth="1.85"
                          strokeLinecap="round"
                        />
                        <path
                          d="M21 4v5h-5"
                          stroke="currentColor"
                          strokeWidth="1.85"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    )}
                  </span>
                  {maxMatchReached
                    ? "הגעת להתאמה מקסימלית"
                    : regenerating
                      ? "מנתח פערים ומייצר גרסה משופרת..."
                      : "שפר התאמה"}
                </button>
                <button
                  type="button"
                  className={`btn btn-ghost ${tailoringId === tailoredCv.job_id ? "btn-loading" : ""}`}
                  onClick={handleForceRegenerate}
                  disabled={
                    regenerating || tailoringId === tailoredCv.job_id
                  }
                  aria-busy={tailoringId === tailoredCv.job_id}
                >
                  {tailoringId === tailoredCv.job_id ? (
                    <>
                      <span className="btn-spinner" aria-hidden="true" />
                      מייצר גרסה חדשה...
                    </>
                  ) : (
                    "ייצר מחדש"
                  )}
                </button>
              </div>
              <p
                id="improve-match-helper"
                className="improve-match-helper"
                title={IMPROVE_MATCH_HELPER}
              >
                {IMPROVE_MATCH_HELPER}
              </p>
            </div>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={handleCopyTailored}
                disabled={isGenerating}
              >
                {copyDone ? "הועתק קו״ח!" : "העתק קורות חיים"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={handleDownloadTailored}
                disabled={isGenerating}
              >
                הורד Markdown
              </button>
              <button
                type="button"
                className="btn btn-primary btn-pdf-download"
                onClick={handleDownloadTailoredPdf}
                disabled={
                  pdfDownloading ||
                  regenerating ||
                  tailoringId === tailoredCv.job_id
                }
              >
                <span className="btn-pdf-icon" aria-hidden="true">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6z"
                      stroke="currentColor"
                      strokeWidth="1.75"
                      strokeLinejoin="round"
                    />
                    <path
                      d="M14 2v6h6"
                      stroke="currentColor"
                      strokeWidth="1.75"
                      strokeLinejoin="round"
                    />
                    <path
                      d="M8 13h8M8 17h5"
                      stroke="currentColor"
                      strokeWidth="1.75"
                      strokeLinecap="round"
                    />
                  </svg>
                </span>
                {pdfDownloading ? "מכין PDF..." : "הורד כ-PDF"}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="history-header">
        <h2>התאמות מהסריקה האחרונה</h2>
        <div className="matches-toolbar">
          <label className="sort-control">
            <span className="sort-label">מיין לפי</span>
            <select
              className="sort-select"
              value={`${sortBy}:${sortOrder}`}
              onChange={(e) => handleSortChange(e.target.value)}
              aria-label="מיין לפי תאריך או ציון"
            >
              <option value="score:desc">ציון התאמה (גבוה לנמוך)</option>
              <option value="score:asc">ציון התאמה (נמוך לגבוה)</option>
              <option value="date:desc">מיין לפי תאריך (חדש לישן)</option>
              <option value="date:asc">מיין לפי תאריך (ישן לחדש)</option>
              <option value="site:asc">אתר (א–ת)</option>
              <option value="site:desc">אתר (ת–א)</option>
            </select>
          </label>
          <span className="history-count">
            {loading ? "טוען..." : `${matches.length} משרות`}
          </span>
        </div>
      </div>

      {matches.length === 0 && !loading ? (
        <div className="empty-state">
          <div className="empty-icon" aria-hidden>
            <span className="icon-bubble icon-bubble-blue">
              <Search size={22} />
            </span>
          </div>
          <p>אין עדיין התאמות לקובץ הזה.</p>
          {displayWarnings.length > 0 ? (
            <p className="empty-hint">
              הסריקה הסתיימה, אך לא נמצאו משרות חדשות. ראו את ההודעות למעלה לפרטים.
            </p>
          ) : (
            <p className="empty-hint">
              לחץ על &quot;הרץ סוכן&quot; כדי לאסוף ולדרג משרות עבור קורות החיים האלה.
            </p>
          )}
        </div>
      ) : (
        <>
          {primaryMatches.length > 0 && (
            <ul className="cv-list">{primaryMatches.map(renderMatchCard)}</ul>
          )}

          {potentialMatches.length > 0 && (
            <div className="potential-matches-section">
              <div className="history-header">
                <h2>התאמות פוטנציאליות</h2>
                <span className="history-count">{potentialMatches.length} משרות</span>
              </div>
              <p className="potential-matches-hint">
                משרות ברף כניסה (1–3 שנים / Tech בסיסי) שלא קיבלו ציון מלא — ניתן להתאים
                להן קורות חיים ממוקדי ATS.
              </p>
              <ul className="cv-list">{potentialMatches.map(renderMatchCard)}</ul>
            </div>
          )}
        </>
      )}
        </>
      )}
    </section>
  );
}
