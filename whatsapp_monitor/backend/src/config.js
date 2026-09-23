"use strict";

const path = require("path");

const MODULE_ROOT = path.resolve(__dirname, "..", "..");

function resolveDir(envName, fallbackRelative) {
  const fromEnv = process.env[envName];
  if (fromEnv && fromEnv.trim()) {
    return path.resolve(fromEnv.trim());
  }
  return path.join(MODULE_ROOT, fallbackRelative);
}

const DATA_DIR = resolveDir("WHATSAPP_DATA_DIR", "data");
const SESSION_DIR = resolveDir("WHATSAPP_SESSION_DIR", "session");
const LOG_DIR = resolveDir("WHATSAPP_LOG_DIR", "logs");
const DB_PATH = path.join(DATA_DIR, "whatsapp_messages.db");
const FRONTEND_DIST = path.resolve(
  process.env.WHATSAPP_FRONTEND_DIST || path.join(MODULE_ROOT, "frontend", "dist")
);

const HOST = process.env.WHATSAPP_MONITOR_HOST || "127.0.0.1";
const PORT = Number(process.env.WHATSAPP_MONITOR_PORT || 3100);
const BASE_PATH = (process.env.WHATSAPP_MONITOR_BASE_PATH || "/whatsapp-monitor").replace(
  /\/$/,
  ""
);

const PUPPETEER_EXECUTABLE_PATH =
  process.env.PUPPETEER_EXECUTABLE_PATH ||
  process.env.CHROME_PATH ||
  process.env.GOOGLE_CHROME_BIN ||
  undefined;

module.exports = {
  MODULE_ROOT,
  DATA_DIR,
  SESSION_DIR,
  LOG_DIR,
  DB_PATH,
  FRONTEND_DIST,
  HOST,
  PORT,
  BASE_PATH,
  PUPPETEER_EXECUTABLE_PATH,
};
