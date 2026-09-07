/** Clipboard helpers that work on HTTP (non-secure) origins too. */

export async function copyTextToClipboard(text: string): Promise<"ok" | "manual"> {
  if (!text) throw new Error("Nothing to copy");

  // Preferred path — only reliable on secure contexts (https / localhost).
  if (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    typeof navigator !== "undefined" &&
    navigator.clipboard?.writeText
  ) {
    try {
      await navigator.clipboard.writeText(text);
      return "ok";
    } catch {
      // Fall through to legacy path.
    }
  }

  // Legacy fallback for http://host:port production deploys.
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "0";
  ta.style.left = "0";
  ta.style.width = "1px";
  ta.style.height = "1px";
  ta.style.padding = "0";
  ta.style.border = "none";
  ta.style.outline = "none";
  ta.style.boxShadow = "none";
  ta.style.background = "transparent";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(ta);
  if (ok) return "ok";

  // Last resort: open a selectable dialog so the user can Ctrl/Cmd+C.
  return "manual";
}
