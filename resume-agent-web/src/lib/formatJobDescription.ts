/**
 * Turn scraped job-description plain text into readable markdown.
 * Scrapers often flatten HTML into a single wall of text; this restores
 * paragraphs, section headings, and bullet lists for display.
 */

const SECTION_HEADERS = [
  // Hebrew
  "תיאור המשרה",
  "תיאור משרה",
  "דרישות המשרה",
  "דרישות התפקיד",
  "דרישות",
  "אודות התפקיד",
  "אודות החברה",
  "על התפקיד",
  "על החברה",
  "מה כולל התפקיד",
  "תחומי אחריות",
  "ניסיון נדרש",
  "השכלה",
  "יתרונות",
  "תנאים",
  "מיקום",
  // English
  "job description",
  "about the role",
  "about the job",
  "about us",
  "about the company",
  "the role",
  "what you'll do",
  "what you will do",
  "what youll do",
  "responsibilities",
  "key responsibilities",
  "your responsibilities",
  "requirements",
  "job requirements",
  "qualifications",
  "required qualifications",
  "preferred qualifications",
  "must have",
  "nice to have",
  "skills",
  "tech stack",
  "technologies",
  "benefits",
  "what we offer",
  "who you are",
  "who we're looking for",
  "who we are looking for",
  "experience",
  "education",
  "location",
];

const SECTION_HEADER_SET = new Set(SECTION_HEADERS.map((h) => h.toLowerCase()));

/** Lines that are already the page section title (UI shows its own heading). */
const REDUNDANT_TITLE_RE =
  /^(?:📅\s*)?(?:תיאור(?:\s+ה)?משרה|job\s*description)\s*:?\s*$/i;

const POSTED_DATE_RE = /^📅\s*תאריך פרסום:\s*.+$/;

const BULLET_RE = /^(?:[-*•●▪◦–—]|[+])\s+/;
const NUMBERED_RE = /^(?:\d+[\.\)]|[א-ת][\.\)])\s+/;

/** Sentences that usually open a new paragraph in job ads. */
const NEW_PARAGRAPH_START_RE =
  /^(?:You'll|You will|You can|You're|You are|This is|This role|The role|The work|The ideal|We are|We're|We offer|To succeed|As a|In this|In addition|Additionally|Also,|Our |About |Responsibilities|Requirements|Qualifications|What you|Who you|Benefits|התפקיד|אנחנו|בנוסף|הדרישות|החברה)/i;

const SENTENCE_SPLIT_RE = /(?<=[.!?…])\s+(?=[A-Z״"«א-ת])/u;

function normalizeWhitespace(text: string): string {
  return text
    .replace(/\r\n?/g, "\n")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function isSectionHeader(line: string): boolean {
  const cleaned = line.replace(/[:：]\s*$/, "").trim();
  if (!cleaned || cleaned.length > 60) return false;
  if (SECTION_HEADER_SET.has(cleaned.toLowerCase())) return true;
  // Short Title Case / ALL CAPS line ending with colon often marks a section.
  if (/[:：]\s*$/.test(line) && cleaned.length <= 40 && !/[.!?]$/.test(cleaned)) {
    return true;
  }
  return false;
}

function toBulletMarkdown(line: string): string {
  if (BULLET_RE.test(line)) {
    return `- ${line.replace(BULLET_RE, "").trim()}`;
  }
  if (NUMBERED_RE.test(line)) {
    return `- ${line.replace(NUMBERED_RE, "").trim()}`;
  }
  return line;
}

function splitWallOfText(block: string): string[] {
  const trimmed = block.trim();
  if (!trimmed) return [];

  // Already structured enough — keep as-is.
  if (trimmed.includes("\n") || trimmed.length < 220) {
    return [trimmed];
  }

  const parts = trimmed
    .split(SENTENCE_SPLIT_RE)
    .map((p) => p.trim())
    .filter(Boolean);
  if (parts.length <= 1) return [trimmed];

  const paragraphs: string[] = [];
  let buf = "";
  for (const sentence of parts) {
    const shouldBreak =
      Boolean(buf) &&
      (NEW_PARAGRAPH_START_RE.test(sentence) || buf.length >= 180);
    if (shouldBreak) {
      paragraphs.push(buf);
      buf = sentence;
    } else {
      buf = buf ? `${buf} ${sentence}` : sentence;
    }
  }
  if (buf) paragraphs.push(buf);
  return paragraphs;
}

function formatBlock(block: string): string {
  const lines = block
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

  if (lines.length === 0) return "";

  // Single long line / wall of text → split into paragraphs.
  if (lines.length === 1) {
    const only = lines[0];
    if (REDUNDANT_TITLE_RE.test(only)) return "";
    if (POSTED_DATE_RE.test(only)) return `*${only}*`;
    if (isSectionHeader(only)) {
      return `### ${only.replace(/[:：]\s*$/, "").trim()}`;
    }
    return splitWallOfText(only).join("\n\n");
  }

  const out: string[] = [];
  let listBuf: string[] = [];

  const flushList = () => {
    if (listBuf.length) {
      out.push(listBuf.join("\n"));
      listBuf = [];
    }
  };

  for (const rawLine of lines) {
    if (REDUNDANT_TITLE_RE.test(rawLine)) {
      continue;
    }
    if (POSTED_DATE_RE.test(rawLine)) {
      flushList();
      out.push(`*${rawLine}*`);
      continue;
    }
    if (isSectionHeader(rawLine)) {
      flushList();
      const title = rawLine.replace(/[:：]\s*$/, "").trim();
      out.push(`### ${title}`);
      continue;
    }
    if (BULLET_RE.test(rawLine) || NUMBERED_RE.test(rawLine)) {
      listBuf.push(toBulletMarkdown(rawLine));
      continue;
    }
    flushList();
    out.push(...splitWallOfText(rawLine));
  }
  flushList();
  return out.join("\n\n");
}

/**
 * Convert raw scraped job description text into display markdown.
 */
export function formatJobDescription(raw: string | null | undefined): string {
  const text = normalizeWhitespace(raw || "");
  if (!text) return "";

  const blocks = text.split(/\n{2,}/);
  const formatted = blocks.map(formatBlock).filter(Boolean);
  return formatted.join("\n\n").replace(/\n{3,}/g, "\n\n").trim();
}
