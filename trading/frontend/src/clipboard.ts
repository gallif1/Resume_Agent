/** Clipboard helpers that work on HTTP (non-secure) origins too.

Avoids crashing the tab when payloads are large (mobile Safari / Chrome).
*/

const MAX_CLIPBOARD_CHARS = 180_000;

function clampText(text: string): string {
  if (text.length <= MAX_CLIPBOARD_CHARS) return text;
  return (
    text.slice(0, MAX_CLIPBOARD_CHARS) +
    `\n\n… נחתך ב-${MAX_CLIPBOARD_CHARS.toLocaleString()} תווים.\n`
  );
}

export async function copyTextToClipboard(text: string): Promise<"ok" | "manual"> {
  if (!text) throw new Error("Nothing to copy");
  const payload = clampText(text);

  // Preferred path — only reliable on secure contexts (https / localhost).
  if (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    typeof navigator !== "undefined" &&
    navigator.clipboard?.writeText
  ) {
    try {
      await navigator.clipboard.writeText(payload);
      return "ok";
    } catch {
      // Fall through to legacy path.
    }
  }

  // Legacy fallback for http://host:port production deploys.
  // Keep the element off-DOM-size and remove it even if copy throws.
  let ta: HTMLTextAreaElement | null = null;
  try {
    ta = document.createElement("textarea");
    ta.value = payload;
    ta.setAttribute("readonly", "");
    ta.setAttribute("aria-hidden", "true");
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "-9999px";
    ta.style.width = "1px";
    ta.style.height = "1px";
    ta.style.padding = "0";
    ta.style.border = "none";
    ta.style.outline = "none";
    ta.style.boxShadow = "none";
    ta.style.background = "transparent";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus({ preventScroll: true });
    ta.select();
    ta.setSelectionRange(0, Math.min(payload.length, 1_000_000));
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    if (ok) return "ok";
  } catch {
    // Fall through to manual.
  } finally {
    if (ta && ta.parentNode) {
      try {
        ta.parentNode.removeChild(ta);
      } catch {
        /* ignore */
      }
    }
  }

  // Last resort: caller shows a selectable box (keep small).
  return "manual";
}
