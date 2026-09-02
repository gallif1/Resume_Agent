import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "react-markdown";
import { ArrowRight, Loader2, RefreshCw, Search } from "lucide-react";
import {
  applyTailoredChangeDecisions,
  applyToJob,
  downloadTailoredCvDocx,
  downloadTailoredCvPdf,
  DuplicateApplicationError,
  getCvMatches,
  getCvScanStatus,
  getJobMatches,
  getJobMatchStatus,
  getJobApplication,
  getTailoredCvPreview,
  openTailoredCvPdfPreview,
  parseScanSummary,
  regenerateTailoredSection,
  scanStreamUrl,
  tailorCvForJob,
  tailorStreamUrl,
  tailorWorkspaceJob,
  updateMatchStatus,
  updateWorkspaceMatchStatus,
  type ApplicationStatus,
  type Cv,
  type CvMatch,
  type CvScanStatus,
  type GenerationReport,
  type JobApplication,
  type JobApplicationStatus,
  type MatchSortBy,
  type MatchSortOrder,
  type TailorDecision,
  type TailorStageEvent,
  type TailoredCvResponse,
} from "../lib/api";
import { formatJobDescription } from "../lib/formatJobDescription";
import PipelineProgress from "./PipelineProgress";
import GenerationLiveModal, { type CloseChoice } from "./GenerationLiveModal";
import ProfileSettings from "./ProfileSettings";
import type { CSSProperties } from "react";
import {
  isTailorGenerating,
  resolveActiveTailoredCv,
} from "../lib/generationProgress";
import {
  buildScoreBreakdown,
  formatScoreProgression,
  getPreviousTailoredScore,
  getTailoredScore,
} from "../lib/tailorScores";

interface Props {
  cvId: string;
  cv: Cv | undefined;
  scanStatus?: CvScanStatus | null;
  showScanPanel?: boolean;
  workspaceMode?: boolean;
  onBack?: () => void;
  emptyHint?: string;
  /** Called when SSE delivers a status_update during a live scan. */
  onStreamStatus?: (message: string) => void;
  /** Called when SSE delivers scan_complete (or the stream errors out). */
  onStreamComplete?: (payload?: { error?: string }) => void;
  /** Bump workspace match count as jobs stream in. */
  onStreamJobFound?: (job: CvMatch) => void;
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

/** Prefer LTR for Latin-heavy blocks so English stays readable in the RTL app. */
function textDirection(text: string): "ltr" | "rtl" {
  // Ignore markdown heading markers / bullets when counting script dominance.
  const sample = (text || "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^[-*•]\s+/gm, "")
    .slice(0, 1200);
  let hebrew = 0;
  let latin = 0;
  for (const ch of sample) {
    if (ch >= "\u0590" && ch <= "\u05FF") hebrew += 1;
    else if ((ch >= "A" && ch <= "Z") || (ch >= "a" && ch <= "z")) latin += 1;
  }
  // Prefer LTR when Latin is present and not clearly Hebrew-dominant.
  if (latin === 0 && hebrew === 0) return "rtl";
  return hebrew > latin * 1.2 ? "rtl" : "ltr";
}

/** Explicit direction props — class + inline style so parent RTL cannot win. */
function directionalAttrs(text: string): {
  dir: "ltr" | "rtl";
  lang: string;
  className: string;
  style: CSSProperties;
} {
  const dir = textDirection(text);
  const isLtr = dir === "ltr";
  return {
    dir,
    lang: isLtr ? "en" : "he",
    className: isLtr ? "is-ltr" : "is-rtl",
    style: {
      direction: dir,
      textAlign: isLtr ? "left" : "right",
      unicodeBidi: "isolate",
    },
  };
}

/** Rewrite legacy formula score lines into human Hebrew (for cached drafts). */
function humanizeLegacyScoreMarkdown(markdown: string): string {
  return (markdown || "").replace(
    /(\*{0,2})ציון בסיס(?:י)?\s*:\s*(\d+)\s*\/\s*100\s*(?:→|->|←)\s*ציון מותאם\s*:\s*(\d+)\s*\/\s*100\*{0,2}(?:\s*[—–-]\s*([^\n*]+))?/giu,
    (_full, _stars, before, after, label) => {
      const he = formatScoreLabel((label || "").trim() || null);
      const suffix = he ? ` — ${he}` : "";
      return `**שיפרנו את ההתאמה למשרה מ־${before} ל־${after}${suffix}**`;
    }
  );
}

/** Split markdown into ## sections so each block can pick its own text direction. */
function splitMarkdownSections(markdown: string): string[] {
  const text = (markdown || "").trim();
  if (!text) return [];
  const parts = text.split(/(?=^##\s+)/m).map((p) => p.trim()).filter(Boolean);
  return parts.length > 0 ? parts : [text];
}

/** Split tailor markdown into Hebrew meta preamble vs resume body. */
function splitTailoredPreview(markdown: string, cvMarkdown?: string | null): {
  preamble: string | null;
  body: string;
} {
  const body = extractTailoredCvBody(markdown, cvMarkdown);
  const text = (markdown || "").trim();
  if (!text || !body || body === text) {
    return { preamble: null, body: body || text };
  }
  const idx = text.indexOf(body);
  if (idx <= 0) {
    return { preamble: null, body };
  }
  let preamble = text.slice(0, idx).trim();
  preamble = preamble.replace(/\n---\s*$/u, "").trim();
  preamble = preamble
    .replace(/^##\s*קורות החיים המעודכנים\s*$/imu, "")
    .trim();
  return { preamble: preamble || null, body };
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
  showScanPanel = false,
  workspaceMode = false,
  onBack,
  emptyHint,
  onStreamStatus,
  onStreamComplete,
  onStreamJobFound,
}: Props) {
  const [matches, setMatches] = useState<CvMatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [listRefreshing, setListRefreshing] = useState(false);
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
  const [resultModalOpen, setResultModalOpen] = useState(false);
  const [activeMatchBaseline, setActiveMatchBaseline] = useState<number | null>(null);
  const [copyDone, setCopyDone] = useState(false);
  const [pdfDownloading, setPdfDownloading] = useState(false);
  const [pdfPreviewing, setPdfPreviewing] = useState(false);
  const [loadingSavedTailored, setLoadingSavedTailored] = useState<number | null>(
    null
  );
  const [regenerating, setRegenerating] = useState(false);
  const [infoMessage, setInfoMessage] = useState<string | null>(null);
  const [stagnantAttempts, setStagnantAttempts] = useState(0);
  const [maxMatchReached, setMaxMatchReached] = useState(false);
  const [previewAnimKey, setPreviewAnimKey] = useState(0);
  const [liveJobIds, setLiveJobIds] = useState<Set<number>>(() => new Set());
  const [lastScanInfo, setLastScanInfo] = useState(() =>
    parseScanSummary(null)
  );
  const [tailorStages, setTailorStages] = useState<TailorStageEvent[]>([]);
  const [tailorDecisions, setTailorDecisions] = useState<TailorDecision[]>([]);
  const [tailorStatusMessage, setTailorStatusMessage] = useState<string | null>(
    null
  );
  const [generationReport, setGenerationReport] =
    useState<GenerationReport | null>(null);
  const [generationUiOpen, setGenerationUiOpen] = useState(false);
  const [generationBackground, setGenerationBackground] = useState(false);
  const [generationStartedAt, setGenerationStartedAt] = useState<number | null>(
    null
  );
  const [elapsedSeconds, setElapsedSeconds] = useState<number | null>(null);
  const prevRunning = useRef(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const tailorEventSourceRef = useRef<EventSource | null>(null);
  const tailorAbortRef = useRef<AbortController | null>(null);
  const generationCancelledRef = useRef(false);
  const streamedJobIdsRef = useRef<Set<number>>(new Set());
  /** Session-best tailored draft so a lower-scoring regenerate never overwrites it. */
  const bestSessionRef = useRef<{
    jobId: number;
    score: number;
    result: TailoredCvResponse;
  } | null>(null);
  const running = scanStatus?.running ?? false;
  const runningRef = useRef(running);
  runningRef.current = running;
  const isGenerating = isTailorGenerating({ regenerating, tailoringId });
  const activeTailoredCv = resolveActiveTailoredCv(tailoredCv, tailoringId);

  useEffect(() => {
    if (!isGenerating || generationStartedAt == null) {
      setElapsedSeconds(null);
      return;
    }
    const tick = () =>
      setElapsedSeconds(
        Math.max(0, Math.round((Date.now() - generationStartedAt) / 1000))
      );
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [isGenerating, generationStartedAt]);

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
    // During an active scan the live SSE stream owns the list. Fetching
    // historical/latest-scan matches here races the stream and can flash
    // previous-scan jobs while collect is still running.
    if (runningRef.current) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const sortOpts = { latest: true as const, sortBy, order: sortOrder };
      const data = workspaceMode
        ? await getJobMatches(sortOpts)
        : await getCvMatches(cvId, sortOpts);
      if (runningRef.current) {
        return;
      }
      // Badge counts all matches; list defaults to latest scan. If the latest
      // scan filter yields nothing but the CV still has matches, fall back so
      // the Jobs tab is not empty while the card shows hundreds of matches.
      if (
        !workspaceMode &&
        data.matches.length === 0 &&
        (cv?.match_count ?? 0) > 0
      ) {
        const all = await getCvMatches(cvId, {
          latest: false,
          sortBy,
          order: sortOrder,
        });
        if (runningRef.current) {
          return;
        }
        setMatches(all.matches);
      } else {
        setMatches(data.matches);
      }
    } catch (e) {
      if (!runningRef.current) {
        setError(e instanceof Error ? e.message : "שגיאה בטעינת ההתאמות");
      }
    } finally {
      setLoading(false);
    }
  }, [cvId, workspaceMode, sortBy, sortOrder, cv?.match_count]);

  const refreshJobList = useCallback(async () => {
    if (listRefreshing || runningRef.current) return;
    setListRefreshing(true);
    setError(null);
    try {
      const sortOpts = { latest: true as const, sortBy, order: sortOrder };
      const data = workspaceMode
        ? await getJobMatches(sortOpts)
        : await getCvMatches(cvId, sortOpts);
      if (runningRef.current) {
        return;
      }
      if (
        !workspaceMode &&
        data.matches.length === 0 &&
        (cv?.match_count ?? 0) > 0
      ) {
        const all = await getCvMatches(cvId, {
          latest: false,
          sortBy,
          order: sortOrder,
        });
        if (runningRef.current) {
          return;
        }
        setMatches(all.matches);
      } else {
        setMatches(data.matches);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בטעינת ההתאמות");
    } finally {
      setListRefreshing(false);
    }
  }, [cvId, workspaceMode, sortBy, sortOrder, listRefreshing, cv?.match_count]);

  const handleSortChange = (value: string) => {
    // Encoded as "field:order" so one dropdown covers the common sorts.
    const [field, direction] = value.split(":") as [MatchSortBy, MatchSortOrder];
    setSortBy(field);
    setSortOrder(direction);
  };

  useEffect(() => {
    if (running) return;
    load();
  }, [load, running]);

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
      setLiveJobIds(new Set());
      streamedJobIdsRef.current = new Set();
    }
    prevRunning.current = running;
  }, [running, load]);

  // Live SSE stream: only fully-processed jobs (collect→enrich→match) are
  // emitted by the backend. Keep the list empty until those events arrive —
  // never mix in historical matches while the scan is running.
  useEffect(() => {
    if (!running) {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      return;
    }

    streamedJobIdsRef.current = new Set();
    setMatches([]);
    setLiveJobIds(new Set());
    setLoading(false);
    let closed = false;
    const source = new EventSource(scanStreamUrl());
    eventSourceRef.current = source;

    const closeStream = () => {
      if (closed) return;
      closed = true;
      source.close();
      if (eventSourceRef.current === source) {
        eventSourceRef.current = null;
      }
    };

    source.addEventListener("job_found", (ev) => {
      const message = ev as MessageEvent<string>;
      let job: CvMatch;
      try {
        job = JSON.parse(message.data) as CvMatch;
      } catch {
        return;
      }
      if (job?.job_id == null) return;

      const isNew = !streamedJobIdsRef.current.has(job.job_id);
      streamedJobIdsRef.current.add(job.job_id);

      setMatches((prev) => {
        const idx = prev.findIndex((m) => m.job_id === job.job_id);
        if (idx >= 0) {
          const next = [...prev];
          next[idx] = { ...next[idx], ...job };
          return next;
        }
        return [job, ...prev];
      });
      setLiveJobIds((prev) => {
        const next = new Set(prev);
        next.add(job.job_id);
        return next;
      });
      if (isNew) onStreamJobFound?.(job);
    });

    source.addEventListener("status_update", (ev) => {
      const message = ev as MessageEvent<string>;
      const text = (message.data || "").trim();
      if (text) onStreamStatus?.(text);
    });

    source.addEventListener("scan_complete", (ev) => {
      const message = ev as MessageEvent<string>;
      let payload: { error?: string } = {};
      try {
        payload = JSON.parse(message.data || "{}") as { error?: string };
      } catch {
        payload = {};
      }
      closeStream();
      onStreamComplete?.(payload);
    });

    source.onerror = () => {
      // EventSource auto-retries; only tear down if the scan already ended.
      if (!running) {
        closeStream();
        onStreamComplete?.();
      }
    };

    return () => {
      closeStream();
    };
  }, [running, onStreamStatus, onStreamComplete, onStreamJobFound]);

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
    if (result.generation_report) {
      setGenerationReport(result.generation_report);
    }
    if (result.decision_log?.length) {
      setTailorDecisions(result.decision_log);
    }
    setPreviewAnimKey((k) => k + 1);
    // The evaluated score replaces the scan estimate on the card too, so the
    // list and the tailored-CV view can never show two different numbers.
    const evaluated = getTailoredScore(result);
    setMatches((prev) =>
      prev.map((m) =>
        m.job_id === result.job_id
          ? {
              ...m,
              match_score: evaluated ?? m.match_score,
              score_label:
                result.matcher_feedback?.current?.score_label ?? m.score_label,
              has_tailored_cv: true,
              tailored_cv_updated_at:
                result.generated_at ?? new Date().toISOString(),
            }
          : m
      )
    );
  };

  const closeTailorStream = () => {
    if (tailorEventSourceRef.current) {
      tailorEventSourceRef.current.close();
      tailorEventSourceRef.current = null;
    }
  };

  const beginGenerationSession = (jobId: number) => {
    generationCancelledRef.current = false;
    if (tailorAbortRef.current) {
      tailorAbortRef.current.abort();
    }
    tailorAbortRef.current = new AbortController();
    // Drop a draft from a different job so the modal never flashes the old
    // completion screen when starting tailor for another match.
    setTailoredCv((prev) => (prev?.job_id === jobId ? prev : null));
    if (bestSessionRef.current?.jobId !== jobId) {
      bestSessionRef.current = null;
    }
    // Clear prior run chrome immediately (openTailorStream also resets these).
    setTailorStages([]);
    setTailorDecisions([]);
    setGenerationReport(null);
    setTailorStatusMessage("מתחבר לצוות ה-AI…");
    setResultModalOpen(false);
    setGenerationUiOpen(true);
    setGenerationBackground(false);
    setGenerationStartedAt(Date.now());
    setElapsedSeconds(0);
    openTailorStream(jobId);
  };

  const cancelGenerationRequest = () => {
    generationCancelledRef.current = true;
    tailorAbortRef.current?.abort();
    tailorAbortRef.current = null;
    closeTailorStream();
    setTailoringId(null);
    setRegenerating(false);
    setTailorStatusMessage("היצירה בוטלה");
  };

  const handleGenerationConfirmClose = (choice: CloseChoice) => {
    if (choice === "stay") return;
    if (choice === "background") {
      setGenerationUiOpen(false);
      setGenerationBackground(true);
      return;
    }
    cancelGenerationRequest();
    setGenerationUiOpen(false);
    setGenerationBackground(false);
  };

  const closeGenerationUi = () => {
    setGenerationUiOpen(false);
    setGenerationBackground(false);
    if (!isGenerating) {
      setGenerationStartedAt(null);
    }
    // Keep tailoredCv / generationReport — closing must not wipe the result.
  };

  const openResultModal = () => {
    setGenerationUiOpen(false);
    setGenerationBackground(false);
    setResultModalOpen(true);
  };

  const closeResultModal = () => {
    if (isGenerating) {
      setGenerationUiOpen(true);
      return;
    }
    // Hide only — saved result stays in state and can be reopened from the job card.
    setResultModalOpen(false);
  };

  const handleViewPdf = async (jobId?: number) => {
    const id = jobId ?? tailoredCv?.job_id;
    if (id == null) return;
    setPdfPreviewing(true);
    setError(null);
    try {
      await openTailoredCvPdfPreview(cvId, id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בפתיחת PDF");
    } finally {
      setPdfPreviewing(false);
    }
  };

  const handleOpenSavedTailored = async (match: CvMatch) => {
    setError(null);
    setInfoMessage(null);
    if (tailoredCv?.job_id === match.job_id && tailoredCv.markdown) {
      setActiveMatchBaseline(match.match_score);
      setResultModalOpen(true);
      return;
    }
    setLoadingSavedTailored(match.job_id);
    try {
      const result = await getTailoredCvPreview(cvId, match.job_id);
      applyTailoredResult(result);
      setActiveMatchBaseline(match.match_score);
      setResultModalOpen(true);
      setGenerationUiOpen(false);
      setGenerationBackground(false);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "לא ניתן לטעון את קורות החיים השמורים"
      );
    } finally {
      setLoadingSavedTailored(null);
    }
  };

  const openTailorStream = (jobId: number) => {
    closeTailorStream();
    setTailorStages([]);
    setTailorDecisions([]);
    setTailorStatusMessage("מתחבר לצוות ה-AI…");
    setGenerationReport(null);
    try {
      const es = new EventSource(
        tailorStreamUrl({ cvId, jobId })
      );
      tailorEventSourceRef.current = es;
      es.addEventListener("tailor_stage", (ev) => {
        try {
          const data = JSON.parse((ev as MessageEvent).data) as TailorStageEvent;
          if (data.job_id != null && Number(data.job_id) !== jobId) return;
          setTailorStages((prev) => [...prev, data]);
          if (data.message) setTailorStatusMessage(data.message);
          if (data.decision?.text) {
            setTailorDecisions((prev) => [
              ...prev,
              { ...data.decision!, stage: data.stage },
            ]);
          }
        } catch {
          /* ignore malformed */
        }
      });
      es.addEventListener("tailor_decision", (ev) => {
        try {
          const data = JSON.parse((ev as MessageEvent).data) as TailorStageEvent;
          if (data.job_id != null && Number(data.job_id) !== jobId) return;
          const decision = data.decision || {
            text: data.message || "",
            stage: data.stage,
          };
          if (decision.text) {
            setTailorDecisions((prev) => [...prev, decision as TailorDecision]);
          }
          if (data.message) setTailorStatusMessage(data.message);
        } catch {
          /* ignore */
        }
      });
      es.addEventListener("tailor_complete", (ev) => {
        try {
          const data = JSON.parse((ev as MessageEvent).data) as {
            generation_report?: GenerationReport;
            error?: string;
          };
          if (data.generation_report) {
            setGenerationReport(data.generation_report);
          }
          if (data.error) setTailorStatusMessage(data.error);
        } catch {
          /* ignore */
        }
        closeTailorStream();
      });
      es.onerror = () => {
        // Keep POST as source of truth; stream is best-effort UX.
      };
    } catch {
      // EventSource unavailable — generation still works via POST.
    }
  };

  const handleTailorCv = async (match: CvMatch, force = false) => {
    setTailoringId(match.job_id);
    setActiveMatchBaseline(match.match_score);
    setError(null);
    setInfoMessage(null);
    setCopyDone(false);
    beginGenerationSession(match.job_id);
    try {
      const signal = tailorAbortRef.current?.signal;
      const result = workspaceMode
        ? await tailorWorkspaceJob(match.job_id, {
            force,
            sourceCvId: cvId,
            signal,
          })
        : await tailorCvForJob(cvId, match.job_id, { force, signal });
      if (generationCancelledRef.current) return;
      if (result.generation_report) {
        setGenerationReport(result.generation_report);
      }
      if (result.decision_log?.length) {
        setTailorDecisions(result.decision_log);
      }
      applyTailoredResult(result, { resetSession: true });
      setTailorStatusMessage(
        result.from_cache
          ? "נטענו קורות חיים שמורים למשרה זו"
          : "קורות החיים נוצרו בהצלחה"
      );
      setGenerationUiOpen(true);
      setGenerationBackground(false);
    } catch (e) {
      if (
        generationCancelledRef.current ||
        (e instanceof DOMException && e.name === "AbortError")
      ) {
        return;
      }
      setError(e instanceof Error ? e.message : "שגיאה בהתאמת קורות החיים");
      setTailorStatusMessage(null);
    } finally {
      closeTailorStream();
      setTailoringId(null);
      tailorAbortRef.current = null;
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

  const handleDownloadTailoredDocx = async () => {
    if (!tailoredCv) return;
    setPdfDownloading(true);
    setError(null);
    try {
      await downloadTailoredCvDocx(cvId, tailoredCv.job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בהורדת DOCX");
    } finally {
      setPdfDownloading(false);
    }
  };

  const handleAcceptAllChanges = async () => {
    if (!tailoredCv?.change_log?.length) return;
    setError(null);
    try {
      const decisions = tailoredCv.change_log.map((_, index) => ({
        index,
        accepted: true,
      }));
      const result = await applyTailoredChangeDecisions(
        cvId,
        tailoredCv.job_id,
        decisions
      );
      setTailoredCv(result);
      setInfoMessage("כל השינויים אושרו");
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה באישור שינויים");
    }
  };

  const handleRejectChange = async (index: number) => {
    if (!tailoredCv?.change_log?.length) return;
    setError(null);
    try {
      const result = await applyTailoredChangeDecisions(cvId, tailoredCv.job_id, [
        { index, accepted: false },
      ]);
      setTailoredCv(result);
      setInfoMessage("השינוי נדחה והנוסח המקורי שוחזר");
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בדחיית שינוי");
    }
  };

  const handleRegenerateSection = async (section: string) => {
    if (!tailoredCv) return;
    setError(null);
    try {
      const result = await regenerateTailoredSection(
        cvId,
        tailoredCv.job_id,
        section
      );
      setTailoredCv(result);
      setInfoMessage(`הקטע ${section} נוצר מחדש ועבר בקרת טענות`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה ביצירת קטע מחדש");
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
    beginGenerationSession(previous.job_id);
    try {
      const signal = tailorAbortRef.current?.signal;
      const result = workspaceMode
        ? await tailorWorkspaceJob(previous.job_id, {
            regenerate: true,
            sourceCvId: cvId,
            signal,
          })
        : await tailorCvForJob(cvId, previous.job_id, {
            regenerate: true,
            signal,
          });
      if (generationCancelledRef.current) return;
      if (result.generation_report) setGenerationReport(result.generation_report);
      if (result.decision_log?.length) setTailorDecisions(result.decision_log);

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
      setGenerationUiOpen(true);
      setGenerationBackground(false);
    } catch (e) {
      if (
        generationCancelledRef.current ||
        (e instanceof DOMException && e.name === "AbortError")
      ) {
        return;
      }
      setError(
        e instanceof Error ? e.message : "שגיאה בשיפור קורות החיים המותאמים"
      );
    } finally {
      closeTailorStream();
      setRegenerating(false);
      tailorAbortRef.current = null;
    }
  };

  const handleForceRegenerate = async () => {
    if (!tailoredCv || regenerating || tailoringId != null) return;
    setTailoringId(tailoredCv.job_id);
    setError(null);
    setInfoMessage(null);
    setCopyDone(false);
    beginGenerationSession(tailoredCv.job_id);
    try {
      const signal = tailorAbortRef.current?.signal;
      const result = workspaceMode
        ? await tailorWorkspaceJob(tailoredCv.job_id, {
            force: true,
            sourceCvId: cvId,
            signal,
          })
        : await tailorCvForJob(cvId, tailoredCv.job_id, {
            force: true,
            signal,
          });
      if (generationCancelledRef.current) return;
      if (result.generation_report) setGenerationReport(result.generation_report);
      if (result.decision_log?.length) setTailorDecisions(result.decision_log);
      applyTailoredResult(result, { resetSession: true });
      setGenerationUiOpen(true);
      setGenerationBackground(false);
    } catch (e) {
      if (
        generationCancelledRef.current ||
        (e instanceof DOMException && e.name === "AbortError")
      ) {
        return;
      }
      setError(
        e instanceof Error ? e.message : "שגיאה בהתאמת קורות החיים"
      );
    } finally {
      closeTailorStream();
      setTailoringId(null);
      tailorAbortRef.current = null;
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
      <li
        key={m.match_id ?? m.job_id}
        className={`cv-item job-item ${potential ? "job-item-potential" : ""} ${
          liveJobIds.has(m.job_id) ? "job-item-live-in" : ""
        }`}
      >
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
              onClick={() => handleTailorCv(m, /* force */ true)}
              aria-busy={busyTailor}
            >
              {busyTailor ? (
                <>
                  <span className="btn-spinner" aria-hidden="true" />
                  מייצר קורות חיים...
                </>
              ) : m.has_tailored_cv ? (
                "ייצר מחדש"
              ) : (
                "ייצר קורות חיים"
              )}
            </button>
            {m.has_tailored_cv && (
              <button
                type="button"
                className={`btn btn-primary btn-sm ${
                  loadingSavedTailored === m.job_id ? "btn-loading" : ""
                }`}
                disabled={busyTailor || loadingSavedTailored === m.job_id}
                onClick={() => {
                  void handleOpenSavedTailored(m);
                }}
                aria-busy={loadingSavedTailored === m.job_id}
              >
                {loadingSavedTailored === m.job_id ? (
                  <>
                    <span className="btn-spinner" aria-hidden="true" />
                    טוען...
                  </>
                ) : (
                  "צפה בתוצאה"
                )}
              </button>
            )}
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

        {busyTailor && generationBackground && (
          <div className="generation-background-banner" role="status">
            <span>יצירת קורות החיים ממשיכה ברקע…</span>
            <button
              type="button"
              className="btn btn-ghost btn-sm touch-target"
              onClick={() => {
                setGenerationUiOpen(true);
                setGenerationBackground(false);
              }}
            >
              חזרה למעקב
            </button>
          </div>
        )}

        {expanded && (
          <div className="job-details">
            <div className="job-description-block">
              <h4 className="job-description-title">תיאור המשרה</h4>
              {m.description?.trim() ? (
                (() => {
                  const formatted = formatJobDescription(m.description);
                  const dirAttrs = directionalAttrs(m.description);
                  return (
                    <div
                      className={`job-description-text ${dirAttrs.className}`}
                      dir={dirAttrs.dir}
                      lang={dirAttrs.lang}
                      style={dirAttrs.style}
                    >
                      <Markdown>{formatted}</Markdown>
                    </div>
                  );
                })()
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
                {" · "}
                <button
                  type="button"
                  className="btn-link-touch"
                  onClick={() => {
                    void handleOpenSavedTailored(m);
                  }}
                >
                  פתח שוב
                </button>
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
  const displayWarnings =
    liveWarnings.length > 0 ? liveWarnings : lastScanInfo.warnings;
  const panelVisible = showScanPanel && running;
  const panelScanStatus = panelVisible ? scanStatus : null;

  return (
    <section>
      <div className="details-topbar">
        {onBack ? (
          <button className="btn btn-ghost" onClick={onBack}>
            <ArrowRight size={16} aria-hidden />
            חזרה
          </button>
        ) : null}
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
      <div
        className={`scan-panel-wrapper ${panelVisible ? "scan-panel-wrapper--visible" : "scan-panel-wrapper--hidden"}`}
        aria-hidden={!panelVisible}
      >
        <div className="scan-panel-wrapper-inner">
          <PipelineProgress
            scanStatus={panelScanStatus}
            matchCount={
              running
                ? Math.max(
                    typeof scanStatus?.match_count === "number"
                      ? scanStatus.match_count
                      : 0,
                    matches.length,
                    liveJobIds.size
                  )
                : typeof scanStatus?.match_count === "number"
                  ? scanStatus.match_count
                  : matches.length > 0
                    ? matches.length
                    : cv?.match_count ?? 0
            }
          />
        </div>
      </div>

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

      <GenerationLiveModal
        open={generationUiOpen}
        active={isGenerating}
        jobLabel={(() => {
          const activeJob =
            (tailoringId != null
              ? matches.find((m) => m.job_id === tailoringId)
              : null) ||
            (activeTailoredCv
              ? {
                  title: activeTailoredCv.title,
                  company: activeTailoredCv.company,
                }
              : null);
          if (!activeJob) return null;
          return [activeJob.title, activeJob.company].filter(Boolean).join(" · ");
        })()}
        stages={tailorStages}
        decisions={
          tailorDecisions.length
            ? tailorDecisions
            : activeTailoredCv?.decision_log || []
        }
        statusMessage={tailorStatusMessage}
        generationReport={
          generationReport || activeTailoredCv?.generation_report || null
        }
        result={activeTailoredCv}
        originalBaseline={activeMatchBaseline}
        elapsedSeconds={elapsedSeconds}
        supportsBackground
        onRequestClose={closeGenerationUi}
        onConfirmClose={handleGenerationConfirmClose}
        onPreview={openResultModal}
        onViewPdf={() => {
          void handleViewPdf();
        }}
        pdfBusy={pdfPreviewing}
        onContinueWatching={() => {
          /* stay on sheet — focus remains in modal */
        }}
      />

      {tailoredCv && resultModalOpen && !generationUiOpen && (
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
                className="btn btn-ghost btn-sm touch-target"
                onClick={closeResultModal}
                aria-label="סגור"
              >
                סגור
              </button>
            </div>
            {(() => {
              const breakdown = buildScoreBreakdown({
                result: tailoredCv,
                generationReport:
                  generationReport || tailoredCv.generation_report || null,
                originalBaseline: activeMatchBaseline,
                isGenerating,
              });
              const original = breakdown.original_score;
              const tailored = breakdown.tailored_score;
              const labelHe = formatScoreLabel(
                tailoredCv.matcher_feedback?.current?.score_label ?? null
              );
              if (
                original == null &&
                tailored == null &&
                !(tailoredCv.changes_breakdown?.length ?? 0)
              ) {
                return null;
              }
              return (
                <div className="tailored-cv-meta tailor-score-lifecycle">
                  <div className="tailor-score-row">
                    <span className="tailor-score-label">מקורי</span>
                    <strong>
                      {original != null ? `${original}%` : "—"}
                    </strong>
                  </div>
                  <div className="tailor-score-row">
                    <span className="tailor-score-label">מותאם</span>
                    <strong>
                      {isGenerating && tailored == null
                        ? "מחשב אחרי אימות סופי…"
                        : tailored != null
                          ? `${tailored}%`
                          : "—"}
                    </strong>
                  </div>
                  {!isGenerating &&
                    breakdown.score_delta != null && (
                      <div className="tailor-score-row tailor-score-delta">
                        <span className="tailor-score-label">שיפור</span>
                        <strong>
                          {breakdown.score_delta > 0
                            ? `+${breakdown.score_delta}`
                            : `${breakdown.score_delta}`}
                        </strong>
                      </div>
                    )}
                  {!isGenerating &&
                    tailored != null &&
                    (() => {
                      const previous = getPreviousTailoredScore(
                        tailoredCv,
                        activeMatchBaseline
                      );
                      const progression = tailoredCv.regenerated
                        ? formatScoreProgression(previous, tailored)
                        : formatScoreProgression(original, tailored, {
                            original: true,
                          });
                      if (!progression && !labelHe) return null;
                      return (
                        <p className="tailor-score-note">
                          {progression}
                          {labelHe ? ` · ${labelHe}` : ""}
                        </p>
                      );
                    })()}
                  {(tailoredCv.changes_breakdown?.length ?? 0) > 0 && (
                    <span className="cv-meta">
                      פירוט השינויים בגוף המסמך למטה
                    </span>
                  )}
                </div>
              );
            })()}
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
            <div className="tailored-review-panel" dir="rtl">
              <p className="tailored-cv-meta tailored-truthfulness">
                {tailoredCv.truthfulness_statement ||
                  "לא נוספה שום חוויה שאינה מגובה בקורות החיים המקוריים. רק טענות Explicit / Strongly Inferred עברו את בודק הטענות."}
              </p>
              {(tailoredCv.quality_report?.overall_tailoring_score != null ||
                tailoredCv.extraction_coverage?.extraction_coverage_score != null) && (
                <div className="tailored-review-section">
                  <strong>איכות התאמה וכיסוי מקור</strong>
                  <p className="cv-meta">
                    {tailoredCv.quality_report?.overall_tailoring_score != null && (
                      <>
                        ציון התאמה: {tailoredCv.quality_report.overall_tailoring_score}
                        {" · "}
                      </>
                    )}
                    {tailoredCv.extraction_coverage?.extracted_fact_count != null && (
                      <>
                        עובדות שחולצו: {tailoredCv.extraction_coverage.extracted_fact_count}
                        {tailoredCv.extraction_coverage.extraction_coverage_score != null && (
                          <>
                            {" "}
                            (כיסוי{" "}
                            {Math.round(
                              (tailoredCv.extraction_coverage.extraction_coverage_score || 0) *
                                100
                            )}
                            %)
                          </>
                        )}
                      </>
                    )}
                  </p>
                  {(tailoredCv.quality_report?.warnings?.length ?? 0) > 0 && (
                    <ul>
                      {tailoredCv.quality_report!.warnings!.slice(0, 4).map((w, i) => (
                        <li key={`qw-${i}`}>{w}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
              {(tailoredCv.change_log?.length ?? 0) > 0 && (
                <div className="tailored-review-section">
                  <div className="tailored-review-section-header">
                    <strong>שינויים חשובים</strong>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={handleAcceptAllChanges}
                      disabled={isGenerating}
                    >
                      אשר הכל
                    </button>
                  </div>
                  <ul className="tailored-change-list">
                    {tailoredCv.change_log!.slice(0, 12).map((item, index) => (
                      <li key={`change-${index}`} className="tailored-change-card">
                        <div className="tailored-change-card-body">
                          <div className="tailored-change-tags">
                            {item.section ? (
                              <span className="tailored-change-category">{item.section}</span>
                            ) : null}
                            {item.change_type ? (
                              <span className="tailored-change-category tailored-change-type">
                                {item.change_type}
                              </span>
                            ) : null}
                            <span className="tailored-change-category">
                              {item.inference_category || item.evidence_type || "Explicit"}
                            </span>
                          </div>
                          {item.original_text ? (
                            <p
                              className="tailored-change-original cv-meta"
                              dir="auto"
                              lang={/[A-Za-z]/.test(item.original_text) ? "en" : undefined}
                            >
                              מקור: {item.original_text}
                            </p>
                          ) : null}
                          {item.new_text ? (
                            <p
                              className="tailored-change-new"
                              dir="auto"
                              lang={/[A-Za-z]/.test(item.new_text) ? "en" : undefined}
                            >
                              {item.new_text}
                            </p>
                          ) : (
                            <p className="tailored-change-new cv-meta">
                              {item.reason || "הוסר / הורד בעדיפות"}
                            </p>
                          )}
                          {item.reason && item.new_text ? (
                            <p className="cv-meta" dir="auto">
                              סיבה: {item.reason}
                            </p>
                          ) : null}
                          {item.supporting_evidence ? (
                            <p
                              className="cv-meta"
                              dir="auto"
                              lang={
                                /[A-Za-z]/.test(item.supporting_evidence) ? "en" : undefined
                              }
                            >
                              ראיה: {item.supporting_evidence}
                            </p>
                          ) : null}
                        </div>
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => handleRejectChange(index)}
                          disabled={isGenerating || item.accepted === false}
                        >
                          {item.accepted === false ? "נדחה" : "דחה / שחזר מקורי"}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {(tailoredCv.inferred_competencies?.length ?? 0) > 0 && (
                <div className="tailored-review-section">
                  <strong>כישורים שהוסקו בביטחון</strong>
                  <ul>
                    {tailoredCv.inferred_competencies!.slice(0, 8).map((item, i) => (
                      <li key={`inf-${i}`}>
                        {item.statement}
                        {item.supporting_evidence ? (
                          <span className="cv-meta">
                            {" "}
                            (ראיה: {item.supporting_evidence})
                          </span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {(tailoredCv.removed_or_deprioritized_content?.length ?? 0) > 0 && (
                <div className="tailored-review-section">
                  <strong>תוכן שהוסר / הורד בעדיפות</strong>
                  <ul>
                    {tailoredCv.removed_or_deprioritized_content!
                      .slice(0, 6)
                      .map((item, i) => (
                        <li key={`rm-${i}`}>{item}</li>
                      ))}
                  </ul>
                </div>
              )}
              {(tailoredCv.missing_requirements?.length ?? 0) > 0 && (
                <div className="tailored-review-section">
                  <strong>דרישות משרה חסרות</strong>
                  <p className="cv-meta">
                    {tailoredCv.missing_requirements!.slice(0, 8).join(" · ")}
                  </p>
                </div>
              )}
              {(tailoredCv.validation_warnings?.length ?? 0) > 0 && (
                <div className="tailored-review-section tailored-warnings">
                  <strong>אזהרות ביטחון נמוך</strong>
                  <ul>
                    {tailoredCv.validation_warnings!.slice(0, 6).map((w, i) => (
                      <li key={`warn-${i}`}>
                        {w.statement}
                        {w.reason ? ` — ${w.reason}` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="tailored-review-section tailored-section-regen">
                <strong>צור מחדש קטע</strong>
                <div className="modal-actions modal-actions-improve">
                  {(
                    [
                      ["professional_summary", "תקציר"],
                      ["skills", "כישורים"],
                      ["experience", "ניסיון"],
                    ] as const
                  ).map(([section, label]) => (
                    <button
                      key={section}
                      type="button"
                      className="btn btn-ghost btn-sm"
                      onClick={() => handleRegenerateSection(section)}
                      disabled={isGenerating}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            {isGenerating && !generationUiOpen && (
              <div className="generation-background-banner" role="status">
                <span>יצירה פעילה — ניתן לחזור למעקב החי</span>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm touch-target"
                  onClick={() => {
                    setGenerationUiOpen(true);
                    setGenerationBackground(false);
                  }}
                >
                  פתח מעקב
                </button>
              </div>
            )}
            <div
              key={previewAnimKey}
              className={`tailored-cv-body ${isGenerating ? "tailored-cv-body-dimmed" : "tailored-cv-body-fade-in"}`}
            >
              {(() => {
                const { preamble, body } = splitTailoredPreview(
                  humanizeLegacyScoreMarkdown(tailoredCv.markdown),
                  tailoredCv.cv_markdown
                );
                const bodyAttrs = directionalAttrs(body);
                return (
                  <>
                    {preamble
                      ? splitMarkdownSections(preamble).map((section, i) => {
                          const sectionAttrs = directionalAttrs(section);
                          return (
                            <div
                              key={`preamble-${i}`}
                              className={`tailored-cv-preamble-section ${sectionAttrs.className}`}
                              dir={sectionAttrs.dir}
                              lang={sectionAttrs.lang}
                              style={sectionAttrs.style}
                            >
                              <Markdown>{section}</Markdown>
                            </div>
                          );
                        })
                      : null}
                    <div
                      className={`tailored-cv-resume ${bodyAttrs.className}`}
                      dir={bodyAttrs.dir}
                      lang={bodyAttrs.lang}
                      style={bodyAttrs.style}
                    >
                      <Markdown>{body}</Markdown>
                    </div>
                  </>
                );
              })()}
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
                onClick={() => {
                  void handleViewPdf(tailoredCv.job_id);
                }}
                disabled={
                  pdfPreviewing ||
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
                {pdfPreviewing ? "מכין PDF..." : "הצג PDF"}
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-pdf-download"
                onClick={handleDownloadTailoredPdf}
                disabled={
                  pdfDownloading ||
                  regenerating ||
                  tailoringId === tailoredCv.job_id ||
                  Boolean(tailoredCv.download_blocked)
                }
                title={
                  tailoredCv.download_blocked
                    ? "ההורדה חסומה — מצב סקירה עקב שערי איכות"
                    : undefined
                }
              >
                {pdfDownloading ? "מכין PDF..." : "הורד כ-PDF"}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={handleDownloadTailoredDocx}
                disabled={
                  pdfDownloading ||
                  regenerating ||
                  tailoringId === tailoredCv.job_id
                }
              >
                הורד DOCX
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
          <div className="history-count-group">
            <span className="history-count">
              {loading && matches.length === 0 ? "טוען..." : `${matches.length} משרות`}
            </span>
            <button
              type="button"
              className="btn-count-refresh"
              disabled={loading || listRefreshing || running}
              onClick={() => void refreshJobList()}
              title="רענון רשימת המשרות השמורות"
              aria-label="רענון רשימת המשרות"
            >
              <RefreshCw
                size={15}
                className={loading || listRefreshing ? "spin" : undefined}
                aria-hidden
              />
            </button>
          </div>
        </div>
      </div>

      {loading && matches.length === 0 ? (
        <div className="empty-state" role="status">
          <div className="empty-icon" aria-hidden>
            <span className="icon-bubble icon-bubble-blue">
              <Loader2 size={22} className="domain-analyzing-spinner" />
            </span>
          </div>
          <p>טוען משרות…</p>
        </div>
      ) : matches.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon" aria-hidden>
            <span className="icon-bubble icon-bubble-blue">
              {scanStatus?.running ? (
                <Loader2 size={22} className="domain-analyzing-spinner" />
              ) : (
                <Search size={22} />
              )}
            </span>
          </div>
          <p>
            {scanStatus?.running
              ? "הסריקה רצה — משרה תופיע כאן רק אחרי שאיסוף, העשרה וחישוב התאמה הושלמו עבורה"
              : "לא נמצאו משרות עדיין"}
          </p>
          {!scanStatus?.running &&
            (displayWarnings.length > 0 ? (
              <p className="empty-hint">
                הסריקה הסתיימה, אך לא נמצאו משרות חדשות. ראו את ההודעות למעלה לפרטים.
              </p>
            ) : (
              <p className="empty-hint">
                {emptyHint ??
                  'לחצו על "סרוק עכשיו" בסרגל העליון כדי לאסוף ולדרג משרות עבור קורות החיים האלה.'}
              </p>
            ))}
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
