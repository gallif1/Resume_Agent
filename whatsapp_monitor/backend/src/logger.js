"use strict";

const fs = require("fs");
const path = require("path");
const { LOG_DIR } = require("./config");

const LOG_FILE = path.join(LOG_DIR, "monitor.log");

function ensureLogDir() {
  fs.mkdirSync(LOG_DIR, { recursive: true });
}

function stamp() {
  return new Date().toISOString();
}

function write(level, message, meta) {
  ensureLogDir();
  const suffix =
    meta !== undefined ? ` ${typeof meta === "string" ? meta : JSON.stringify(meta)}` : "";
  const line = `[${stamp()}] [${level}] ${message}${suffix}`;
  // Never log session tokens / cookies / auth payloads.
  try {
    fs.appendFileSync(LOG_FILE, `${line}\n`, "utf8");
  } catch {
    // Ignore filesystem errors; still print to stdout.
  }
  const out = level === "ERROR" ? console.error : console.log;
  out(line);
}

module.exports = {
  LOG_FILE,
  info: (msg, meta) => write("INFO", msg, meta),
  warn: (msg, meta) => write("WARNING", msg, meta),
  error: (msg, meta) => write("ERROR", msg, meta),
};
