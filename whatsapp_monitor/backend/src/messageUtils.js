"use strict";

const URL_RE = /\bhttps?:\/\/[^\s<>"'`)\]]+/gi;

function extractLinks(text) {
  if (!text) return [];
  const matches = String(text).match(URL_RE) || [];
  const cleaned = matches.map((u) => u.replace(/[.,;:!?)]+$/g, ""));
  return [...new Set(cleaned)];
}

function mediaTypeFromMessage(msg) {
  if (!msg) return null;
  const type = String(msg.type || "").toLowerCase();
  const map = {
    image: "image",
    video: "video",
    document: "document",
    audio: "audio",
    ptt: "audio",
    sticker: "sticker",
  };
  if (map[type]) return map[type];
  if (msg.hasMedia) return type || "media";
  return null;
}

function toIsoFromWhatsAppTs(ts) {
  if (ts == null || ts === "") return null;
  const n = Number(ts);
  if (!Number.isFinite(n)) return null;
  // WhatsApp timestamps are usually seconds.
  const ms = n > 1e12 ? n : n * 1000;
  try {
    return new Date(ms).toISOString();
  } catch {
    return null;
  }
}

async function normalizeIncomingMessage(msg, chat) {
  const groupId = chat?.id?._serialized || msg.from || null;
  const groupName = chat?.name || chat?.formattedTitle || groupId || "Unknown group";

  let senderId = null;
  let senderName = null;
  try {
    senderId = msg.author || msg.from || null;
    if (msg._data?.notifyName) {
      senderName = msg._data.notifyName;
    }
    if (!senderName && typeof msg.getContact === "function") {
      try {
        const contact = await msg.getContact();
        senderName =
          contact?.pushname ||
          contact?.name ||
          contact?.shortName ||
          contact?.number ||
          null;
        if (!senderId && contact?.id?._serialized) {
          senderId = contact.id._serialized;
        }
      } catch {
        // Missing sender info is OK — store what we have.
      }
    }
  } catch {
    // ignore
  }

  const messageText = typeof msg.body === "string" ? msg.body : "";
  const mediaType = mediaTypeFromMessage(msg);
  const hasMedia = Boolean(msg.hasMedia) || Boolean(mediaType && mediaType !== "chat");

  return {
    message_id: msg.id?._serialized || msg.id?.id || `${groupId}:${msg.timestamp}:${Math.random()}`,
    group_id: groupId,
    group_name: groupName,
    sender_id: senderId,
    sender_name: senderName || "Unknown",
    message_text: messageText,
    message_type: String(msg.type || "chat"),
    timestamp: toIsoFromWhatsAppTs(msg.timestamp) || new Date().toISOString(),
    received_at: new Date().toISOString(),
    links: extractLinks(messageText),
    has_media: hasMedia,
    media_type: mediaType,
  };
}

module.exports = {
  extractLinks,
  mediaTypeFromMessage,
  toIsoFromWhatsAppTs,
  normalizeIncomingMessage,
};
