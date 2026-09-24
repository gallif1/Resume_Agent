"use strict";

const EventEmitter = require("events");
const fs = require("fs");
const path = require("path");
const { LOG_DIR } = require("./config");

const LOG_FILE = path.join(LOG_DIR, "monitor.log");
const MAX_RECENT = 200;
const bus = new EventEmitter();
bus.setMaxListeners(50);

let seq = 0;
/** @type {{ id: number, at: string, level: string, message: string, detail?: string|null }[]} */
const recent = [];

function ensureLogDir() {
  fs.mkdirSync(LOG_DIR, { recursive: true });
}

function stamp() {
  return new Date().toISOString();
}

function sanitizeMeta(meta) {
  if (meta === undefined || meta === null) return null;
  if (typeof meta === "string") {
    // Never leak QR payloads / huge base64 into the UI log.
    if (meta.startsWith("data:image") || meta.length > 400) {
      return `${meta.slice(0, 80)}…`;
    }
    return meta;
  }
  try {
    const raw = JSON.stringify(meta);
    if (raw.length > 400) return `${raw.slice(0, 400)}…`;
    return raw;
  } catch {
    return String(meta);
  }
}

function pushRecent(level, message, meta) {
  const entry = {
    id: ++seq,
    at: stamp(),
    level,
    message: String(message || ""),
    detail: sanitizeMeta(meta),
  };
  recent.push(entry);
  if (recent.length > MAX_RECENT) recent.shift();
  bus.emit("log", entry);
  return entry;
}

function write(level, message, meta) {
  ensureLogDir();
  const entry = pushRecent(level, message, meta);
  const suffix = entry.detail ? ` ${entry.detail}` : "";
  const line = `[${entry.at}] [${level}] ${entry.message}${suffix}`;
  try {
    fs.appendFileSync(LOG_FILE, `${line}\n`, "utf8");
  } catch {
    // Ignore filesystem errors; still print to stdout.
  }
  const out = level === "ERROR" ? console.error : console.log;
  out(line);
  return entry;
}

module.exports = {
  LOG_FILE,
  info: (msg, meta) => write("INFO", msg, meta),
  warn: (msg, meta) => write("WARNING", msg, meta),
  error: (msg, meta) => write("ERROR", msg, meta),
  /** User-facing milestone (shown prominently in the UI activity feed). */
  activity: (msg, meta) => write("INFO", msg, meta),
  recent: (limit = 100) => {
    const n = Math.max(1, Math.min(Number(limit) || 100, MAX_RECENT));
    return recent.slice(-n);
  },
  onLog: (fn) => {
    bus.on("log", fn);
    return () => bus.off("log", fn);
  },
};
