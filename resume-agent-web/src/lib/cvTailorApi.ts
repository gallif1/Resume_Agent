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
  jobDescription: string
): Promise<CvTailorGenerateResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("job_description", jobDescription);

  return authJsonRequest<CvTailorGenerateResponse>(
    "/api/cv-tailor/generate",
    { method: "POST", body: form },
    "יצירת קורות חיים מותאמים נכשלה"
  );
}

export async function regenerateTailoredCv(
  resultId: string,
  request: RegenerateCvRequest
): Promise<CvTailorGenerateResponse> {
  return authJsonRequest<CvTailorGenerateResponse>(
    `/api/cv-tailor/regenerate/${encodeURIComponent(resultId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
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
