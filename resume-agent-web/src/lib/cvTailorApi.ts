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
  summary: string;
  skills: string[];
  experience: TailoredCvExperience[];
  projects: TailoredCvProject[];
  education: TailoredCvEducation[];
  certifications: string[];
};

export type CvTailorGenerateResponse = {
  result_id: string;
  model: string;
  preview_text: string;
  tailored_cv: TailoredCvData;
};

function authHeaders(): Headers {
  const headers = new Headers();
  const token = getStoredToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return headers;
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
    let detail = `Request failed (${res.status})`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }

  return res.json() as Promise<CvTailorGenerateResponse>;
}

export async function downloadTailoredCv(resultId: string): Promise<Blob> {
  const headers = authHeaders();
  const res = await fetch(`${BASE_URL}/api/cv-tailor/download/${encodeURIComponent(resultId)}`, {
    headers,
  });

  if (!res.ok) {
    let detail = `Download failed (${res.status})`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }

  return res.blob();
}
