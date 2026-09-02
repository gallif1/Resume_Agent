/** Per-CV / workspace persistence for favorites, engagement, and viewed jobs. */

export interface JobInteractions {
  favorites: number[];
  engaged: number[];
  viewed: number[];
}

const STORAGE_PREFIX = "resume_agent_job_interactions:";

function storageKey(scope: string): string {
  return `${STORAGE_PREFIX}${scope}`;
}

function emptyInteractions(): JobInteractions {
  return { favorites: [], engaged: [], viewed: [] };
}

function normalizeIds(raw: unknown): number[] {
  if (!Array.isArray(raw)) return [];
  return [...new Set(raw.map((id) => Number(id)).filter((id) => Number.isFinite(id)))];
}

export function loadJobInteractions(scope: string): JobInteractions {
  if (typeof window === "undefined") return emptyInteractions();
  try {
    const raw = window.localStorage.getItem(storageKey(scope));
    if (!raw) return emptyInteractions();
    const parsed = JSON.parse(raw) as Partial<JobInteractions>;
    return {
      favorites: normalizeIds(parsed.favorites),
      engaged: normalizeIds(parsed.engaged),
      viewed: normalizeIds(parsed.viewed),
    };
  } catch {
    return emptyInteractions();
  }
}

function saveJobInteractions(scope: string, data: JobInteractions): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(storageKey(scope), JSON.stringify(data));
}

function toSet(ids: number[]): Set<number> {
  return new Set(ids);
}

export function interactionsToSets(data: JobInteractions): {
  favorites: Set<number>;
  engaged: Set<number>;
  viewed: Set<number>;
} {
  return {
    favorites: toSet(data.favorites),
    engaged: toSet(data.engaged),
    viewed: toSet(data.viewed),
  };
}

export function toggleFavorite(
  scope: string,
  jobId: number,
  current: JobInteractions
): JobInteractions {
  const favorites = new Set(current.favorites);
  if (favorites.has(jobId)) favorites.delete(jobId);
  else favorites.add(jobId);
  const next = { ...current, favorites: [...favorites] };
  saveJobInteractions(scope, next);
  return next;
}

export function markEngaged(
  scope: string,
  jobId: number,
  current: JobInteractions
): JobInteractions {
  if (current.engaged.includes(jobId)) return current;
  const next = { ...current, engaged: [...current.engaged, jobId] };
  saveJobInteractions(scope, next);
  return next;
}

export function markViewed(
  scope: string,
  jobId: number,
  current: JobInteractions
): JobInteractions {
  if (current.viewed.includes(jobId)) return current;
  const next = { ...current, viewed: [...current.viewed, jobId] };
  saveJobInteractions(scope, next);
  return next;
}

/** Case-insensitive match against common job fields. */
export function jobMatchesSearch(
  job: {
    title?: string | null;
    company?: string | null;
    location?: string | null;
    source?: string | null;
    description?: string | null;
    matched_skills?: string[] | null;
  },
  query: string
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const haystack = [
    job.title,
    job.company,
    job.location,
    job.source,
    job.description,
    ...(job.matched_skills ?? []),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(q);
}
