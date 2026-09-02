import { authFetch, authJsonRequest } from "./api";

export type TailoredCvExperience = {
  company: string;
  role: string;
  dates: string;
  bullets: string[];
};

export type TailoredCvProject = {
  name: string;
  description: string;
  bullets: string[];
};

export type TailoredCvEducation = {
  institution: string;
  degree: string;
  dates: string;
};

export type TailoredCvData = {
  name: string;
  contact: string;
  professional_title?: string;
  summary: string;
  skills: string[];
  experience: TailoredCvExperience[];
  projects: TailoredCvProject[];
  education: TailoredCvEducation[];
  certifications: string[];
};

export type RequirementGap = {
  gap_id: string;
  title: string;
  requirement: string;
  job_requirement_text: string;
  cv_evidence: string;
  confirmation_text: string;
  status: string;
  explanation: string;
};

export type ResolvedRequirement = {
  requirement: string;
  title: string;
  status: "SUPPORTED" | "USER_CONFIRMED";
  note: string;
};

export type JobAnalysis = {
  target_job_title?: string;
  seniority_required?: string;
  must_have_technologies?: string[];
  nice_to_have?: string[];
  key_phrases?: string[];
  strong_matches: string[];
  gaps: RequirementGap[];
  resolved_requirements?: ResolvedRequirement[];
};

export type CandidateFact = {
  fact: string;
  normalized_fact: string;
  source: "original_cv" | "user_confirmed";
  gap_id?: string;
};

export type CvTailorGenerateResponse = {
  result_id: string;
  model: string;
  preview_text: string;
  tailored_cv: TailoredCvData;
  job_analysis: JobAnalysis;
  user_confirmed_facts?: CandidateFact[];
  saved_to_job?: boolean;
  job_version_id?: number | null;
};

export type CvTailorJobContext = {
  cvId?: string;
  jobId?: number;
};

export type GapConfirmationInput = {
  gap_id: string;
  confirmed: boolean;
  details: string;
};

export type RegenerateCvRequest = {
  gap_confirmations: GapConfirmationInput[];
  general_additional_info: string;
  cv_id?: string;
  job_id?: number;
};

function detailFromBody(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

async function parseBlobError(res: Response, fallback: string): Promise<string> {
  const text = await res.text();
  if (!text.trim()) return fallback;
  try {
    return detailFromBody(JSON.parse(text), fallback);
  } catch {
    return fallback;
  }
}

export async function generateTailoredCv(
  file: File,
  jobDescription: string,
  jobContext?: CvTailorJobContext
): Promise<CvTailorGenerateResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("job_description", jobDescription);
  if (jobContext?.cvId) {
    form.append("cv_id", jobContext.cvId);
  }
  if (jobContext?.jobId != null) {
    form.append("job_id", String(jobContext.jobId));
  }

  return authJsonRequest<CvTailorGenerateResponse>(
    "/api/cv-tailor/generate",
    { method: "POST", body: form },
    "יצירת קורות חיים מותאמים נכשלה"
  );
}

export async function regenerateTailoredCv(
  resultId: string,
  request: RegenerateCvRequest,
  jobContext?: CvTailorJobContext
): Promise<CvTailorGenerateResponse> {
  const payload: RegenerateCvRequest = { ...request };
  if (jobContext?.cvId) {
    payload.cv_id = jobContext.cvId;
  }
  if (jobContext?.jobId != null) {
    payload.job_id = jobContext.jobId;
  }
  return authJsonRequest<CvTailorGenerateResponse>(
    `/api/cv-tailor/regenerate/${encodeURIComponent(resultId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
    "עדכון קורות החיים נכשל"
  );
}

export async function downloadTailoredCv(resultId: string): Promise<Blob> {
  const res = await authFetch(`/api/cv-tailor/download/${encodeURIComponent(resultId)}`);
  if (!res.ok) {
    throw new Error(await parseBlobError(res, `הורדה נכשלה (${res.status})`));
  }
  return res.blob();
}

export type CvJobContext = {
  job_id: number;
  title: string | null;
  company: string | null;
  location: string | null;
  description: string;
};

/** Build a CV Tailor URL with optional prefill + auto-run query params. */
export function buildCvTailorUrl(options: {
  cvId: string;
  jobId: number;
  auto?: boolean;
}): string {
  const params = new URLSearchParams({
    cv_id: options.cvId,
    job_id: String(options.jobId),
  });
  if (options.auto !== false) {
    params.set("auto", "1");
  }
  return `/cv-tailor?${params.toString()}`;
}

/** Fetch the stored CV file for a CV record (for CV Tailor prefill). */
export async function fetchStoredCvFile(cvId: string): Promise<File> {
  const res = await authFetch(`/cvs/${encodeURIComponent(cvId)}/file`, {}, "לא ניתן לטעון את קובץ קורות החיים");
  if (!res.ok) {
    throw new Error(await parseBlobError(res, `טעינת קובץ קורות החיים נכשלה (${res.status})`));
  }
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const utfMatch = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  const plainMatch = /filename="([^"]+)"|filename=([^;]+)/i.exec(disposition);
  const rawName =
    (utfMatch && decodeURIComponent(utfMatch[1])) ||
    (plainMatch && (plainMatch[1] || plainMatch[2]).trim()) ||
    "resume.pdf";
  const filename = rawName.replace(/^["']|["']$/g, "");
  return new File([blob], filename, { type: blob.type || "application/octet-stream" });
}

/** Fetch job description and metadata for CV Tailor prefill. */
export async function fetchJobContext(cvId: string, jobId: number): Promise<CvJobContext> {
  return authJsonRequest<CvJobContext>(
    `/cvs/${encodeURIComponent(cvId)}/jobs/${jobId}/context`,
    {},
    "לא ניתן לטעון את פרטי המשרה"
  );
}
