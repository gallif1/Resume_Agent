import { getStoredToken } from "./api";

const BASE_URL: string = (import.meta.env.VITE_API_URL as string | undefined) ?? "";

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
};

export type GapConfirmationInput = {
  gap_id: string;
  confirmed: boolean;
  details: string;
};

export type RegenerateCvRequest = {
  gap_confirmations: GapConfirmationInput[];
  general_additional_info: string;
};

function authHeaders(): Headers {
  const headers = new Headers();
  const token = getStoredToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return headers;
}

async function parseErrorResponse(res: Response, fallback: string): Promise<string> {
  let detail = fallback;
  try {
    const body = (await res.json()) as { detail?: string };
    if (body.detail) detail = body.detail;
  } catch {
    /* ignore */
  }
  return detail;
}

export async function generateTailoredCv(
  file: File,
  jobDescription: string
): Promise<CvTailorGenerateResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("job_description", jobDescription);

  const headers = authHeaders();
  const res = await fetch(`${BASE_URL}/api/cv-tailor/generate`, {
    method: "POST",
    headers,
    body: form,
  });

  if (!res.ok) {
    throw new Error(await parseErrorResponse(res, `Request failed (${res.status})`));
  }

  return res.json() as Promise<CvTailorGenerateResponse>;
}

export async function regenerateTailoredCv(
  resultId: string,
  request: RegenerateCvRequest
): Promise<CvTailorGenerateResponse> {
  const headers = authHeaders();
  headers.set("Content-Type", "application/json");

  const res = await fetch(`${BASE_URL}/api/cv-tailor/regenerate/${encodeURIComponent(resultId)}`, {
    method: "POST",
    headers,
    body: JSON.stringify(request),
  });

  if (!res.ok) {
    throw new Error(await parseErrorResponse(res, `Regeneration failed (${res.status})`));
  }

  return res.json() as Promise<CvTailorGenerateResponse>;
}

export async function downloadTailoredCv(resultId: string): Promise<Blob> {
  const headers = authHeaders();
  const res = await fetch(`${BASE_URL}/api/cv-tailor/download/${encodeURIComponent(resultId)}`, {
    headers,
  });

  if (!res.ok) {
    throw new Error(await parseErrorResponse(res, `Download failed (${res.status})`));
  }

  return res.blob();
}
