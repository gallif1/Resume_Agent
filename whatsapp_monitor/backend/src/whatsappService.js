"use strict";

const EventEmitter = require("events");
const fs = require("fs");
const path = require("path");
const qrcode = require("qrcode");
const { Client, LocalAuth } = require("whatsapp-web.js");
const { SESSION_DIR, PUPPETEER_EXECUTABLE_PATH } = require("./config");
const logger = require("./logger");

/**
 * Connection statuses shown in the UI.
 * CONNECTED | CONNECTING | DISCONNECTED | AUTHENTICATION_REQUIRED |
 * SESSION_ERROR | RECONNECTING
 */
class WhatsAppService extends EventEmitter {
  constructor() {
    super();
    this.client = null;
    this.status = "DISCONNECTED";
    this.qrDataUrl = null;
    this.lastError = null;
    this.chromePath = null;
    this.groupsCache = [];
    this.groupsLoadedAt = null;
    this._starting = false;
    this._intentionalStop = false;
    this._reconnectTimer = null;
    this._reconnectAttempts = 0;
    this._initPromise = null;
  }

  getSnapshot() {
    const needsQr =
      this.status === "AUTHENTICATION_REQUIRED" ||
      this.status === "CONNECTING" ||
      this.status === "RECONNECTING";
    return {
      status: this.status,
      qr: needsQr && this.qrDataUrl ? this.qrDataUrl : null,
      last_error: this.lastError,
      chrome_path: this.chromePath,
      groups_count: this.groupsCache.length,
      groups_loaded_at: this.groupsLoadedAt,
      reconnect_attempts: this._reconnectAttempts,
    };
  }

  _setStatus(status, extra = {}) {
    this.status = status;
    if (status === "CONNECTED" || status === "SESSION_ERROR" || status === "DISCONNECTED") {
      this.qrDataUrl = null;
    }
    this.emit("status", { ...this.getSnapshot(), ...extra });
  }

  resolveChromePath() {
    const candidates = [];
    if (PUPPETEER_EXECUTABLE_PATH) candidates.push(PUPPETEER_EXECUTABLE_PATH);
    const hintFile = path.join(path.dirname(SESSION_DIR), "..", ".chrome_path");
    // Prefer explicit module hint written by deploy/entrypoint.
    const moduleHint = "/app/whatsapp_monitor/.chrome_path";
    for (const hint of [moduleHint, hintFile]) {
      try {
        if (fs.existsSync(hint)) {
          const p = fs.readFileSync(hint, "utf8").trim();
          if (p) candidates.push(p);
        }
      } catch {
        // ignore
      }
    }
    candidates.push(
      "/usr/bin/chromium",
      "/usr/bin/chromium-browser",
      "/usr/bin/google-chrome",
      "/usr/bin/google-chrome-stable",
      "/usr/local/bin/google-chrome"
    );
    try {
      const ms = "/ms-playwright";
      if (fs.existsSync(ms)) {
        const walk = (dir, depth) => {
          if (depth > 4) return;
          let entries = [];
          try {
            entries = fs.readdirSync(dir, { withFileTypes: true });
          } catch {
            return;
          }
          for (const ent of entries) {
            const full = path.join(dir, ent.name);
            if (ent.isDirectory()) walk(full, depth + 1);
            else if (
              ent.isFile() &&
              (ent.name === "chrome" || ent.name === "chromium" || ent.name === "chrome-headless-shell")
            ) {
              candidates.push(full);
            }
          }
        };
        walk(ms, 0);
      }
    } catch {
      // ignore
    }

    for (const c of candidates) {
      if (c && fs.existsSync(c)) {
        try {
          fs.accessSync(c, fs.constants.X_OK);
          this.chromePath = c;
          return c;
        } catch {
          // not executable
        }
      }
    }
    this.chromePath = null;
    return null;
  }

  clearSessionFiles() {
    const target = SESSION_DIR;
    if (!fs.existsSync(target)) return { cleared: false, reason: "missing" };
    try {
      for (const name of fs.readdirSync(target)) {
        if (name === ".gitkeep") continue;
        fs.rmSync(path.join(target, name), { recursive: true, force: true });
      }
      logger.info("WhatsApp session files cleared", { session_dir: path.basename(target) });
      return { cleared: true };
    } catch (err) {
      logger.error("Failed to clear session files", err?.message || String(err));
      return { cleared: false, reason: err?.message || String(err) };
    }
  }

  _waitForStatus(wanted, timeoutMs) {
    if (wanted.includes(this.status)) {
      return Promise.resolve(this.getSnapshot());
    }
    return new Promise((resolve) => {
      const onStatus = () => {
        if (wanted.includes(this.status)) {
          cleanup();
          resolve(this.getSnapshot());
        }
      };
      const timer = setTimeout(() => {
        cleanup();
        resolve(this.getSnapshot());
      }, timeoutMs);
      const cleanup = () => {
        clearTimeout(timer);
        this.off("status", onStatus);
      };
      this.on("status", onStatus);
    });
  }

  async start({ resetSession = false } = {}) {
    if (
      !resetSession &&
      this.client &&
      (this.status === "CONNECTED" ||
        this.status === "CONNECTING" ||
        this.status === "AUTHENTICATION_REQUIRED" ||
        this.status === "RECONNECTING")
    ) {
      // Already bringing up / waiting for QR — return current snapshot (incl. QR via SSE).
      if (this.status === "CONNECTING" && !this.qrDataUrl) {
        await this._waitForStatus(
          ["AUTHENTICATION_REQUIRED", "CONNECTED", "SESSION_ERROR"],
          20000
        );
      }
      return this.getSnapshot();
    }
    if (this._starting && !resetSession) {
      await this._waitForStatus(
        ["AUTHENTICATION_REQUIRED", "CONNECTED", "SESSION_ERROR", "DISCONNECTED"],
        20000
      );
      return this.getSnapshot();
    }

    this._starting = true;
    this._intentionalStop = false;
    this.lastError = null;

    try {
      if (resetSession || this.status === "SESSION_ERROR") {
        await this.destroyClient();
        this.clearSessionFiles();
      } else if (this.client && this.status === "DISCONNECTED") {
        await this.destroyClient();
      }

      fs.mkdirSync(SESSION_DIR, { recursive: true });

      const chrome = this.resolveChromePath();
      if (!chrome) {
        this.lastError =
          "Chromium not found on the server. Redeploy with the Playwright image (rebuild_image=true) so WhatsApp can open a browser for the QR code.";
        logger.error(this.lastError);
        this._setStatus("SESSION_ERROR");
        return this.getSnapshot();
      }

      logger.info("WhatsApp client starting", { chrome });
      this._setStatus("CONNECTING");

      const puppeteerOpts = {
        headless: true,
        executablePath: chrome,
        args: [
          "--no-sandbox",
          "--disable-setuid-sandbox",
          "--disable-dev-shm-usage",
          "--disable-gpu",
          "--no-first-run",
          "--no-default-browser-check",
          "--disable-blink-features=AutomationControlled",
        ],
      };

      this.client = new Client({
        authStrategy: new LocalAuth({
          dataPath: SESSION_DIR,
          clientId: "whatsapp-monitor",
        }),
        puppeteer: puppeteerOpts,
        restartOnAuthFail: false,
      });

      this._bindClientEvents(this.client);

      // initialize() resolves after browser+inject (not after QR scan). Catch async failures.
      this._initPromise = this.client.initialize().catch((err) => {
        this.lastError = err?.message || String(err);
        logger.error("WhatsApp client initialize failed", this.lastError);
        this._setStatus("SESSION_ERROR");
        this.client = null;
      });

      // Wait for QR / ready / error so Connect can return something useful.
      await this._waitForStatus(
        ["AUTHENTICATION_REQUIRED", "CONNECTED", "SESSION_ERROR"],
        45000
      );

      if (this.status === "CONNECTING" && !this.qrDataUrl) {
        this.lastError =
          this.lastError ||
          "Timed out waiting for WhatsApp QR. Click Connect again to reset the session.";
        logger.error(this.lastError);
        await this.destroyClient();
        this._setStatus("SESSION_ERROR");
      }
    } catch (err) {
      this.lastError = err?.message || String(err);
      logger.error("WhatsApp client failed to start", this.lastError);
      this._setStatus("SESSION_ERROR");
      this.client = null;
    } finally {
      this._starting = false;
    }
    return this.getSnapshot();
  }

  _bindClientEvents(client) {
    client.on("qr", async (qr) => {
      try {
        this.qrDataUrl = await qrcode.toDataURL(qr, { margin: 1, width: 280 });
        logger.info("QR code generated — authentication required");
        this.lastError = null;
        this._setStatus("AUTHENTICATION_REQUIRED");
      } catch (err) {
        this.lastError = err?.message || String(err);
        logger.error("Failed to render QR", this.lastError);
        this._setStatus("SESSION_ERROR");
      }
    });

    client.on("authenticated", () => {
      logger.info("Authentication successful");
      this.qrDataUrl = null;
      this.lastError = null;
      this._setStatus("CONNECTING");
    });

    client.on("ready", async () => {
      logger.info("WhatsApp connected");
      this._reconnectAttempts = 0;
      this.qrDataUrl = null;
      this.lastError = null;
      this._setStatus("CONNECTED");
      try {
        await this.refreshGroups();
      } catch (err) {
        logger.warn("Group discovery after connect failed", err?.message || String(err));
      }
    });

    client.on("auth_failure", (msg) => {
      this.lastError = typeof msg === "string" ? msg : "Authentication failed — session reset needed";
      logger.error("Authentication expired or failed", this.lastError);
      this._setStatus("SESSION_ERROR");
    });

    client.on("disconnected", (reason) => {
      logger.warn("WhatsApp disconnected", reason || "");
      this.groupsCache = [];
      if (this._intentionalStop) {
        this._setStatus("DISCONNECTED");
        return;
      }
      this._setStatus("RECONNECTING");
      this._scheduleReconnect();
    });

    client.on("change_state", (state) => {
      logger.info("WhatsApp state change", state);
    });
  }

  _scheduleReconnect() {
    if (this._intentionalStop) return;
    if (this._reconnectTimer) return;
    this._reconnectAttempts += 1;
    const delay = Math.min(30000, 2000 * this._reconnectAttempts);
    logger.info("Attempting reconnect", { attempt: this._reconnectAttempts, delay_ms: delay });
    this._reconnectTimer = setTimeout(async () => {
      this._reconnectTimer = null;
      try {
        await this.destroyClient();
        await this.start();
      } catch (err) {
        logger.error("Reconnect failed", err?.message || String(err));
        this._setStatus("RECONNECTING");
        this._scheduleReconnect();
      }
    }, delay);
  }

  async destroyClient() {
    const c = this.client;
    this.client = null;
    this._initPromise = null;
    if (!c) return;
    try {
      await c.destroy();
    } catch (err) {
      logger.warn("Client destroy error", err?.message || String(err));
    }
  }

  async stopClient() {
    this._intentionalStop = true;
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
    await this.destroyClient();
    this._setStatus("DISCONNECTED");
    logger.info("WhatsApp client stopped (session files kept)");
    return this.getSnapshot();
  }

  async resetAndConnect() {
    return this.start({ resetSession: true });
  }

  async refreshGroups() {
    if (!this.client || this.status !== "CONNECTED") {
      return this.groupsCache;
    }
    const chats = await this.client.getChats();
    const groups = [];
    for (const chat of chats) {
      try {
        if (!chat.isGroup) continue;
        groups.push({
          group_id: chat.id?._serialized || String(chat.id),
          group_name: chat.name || chat.formattedTitle || "Unnamed group",
          participant_count: Array.isArray(chat.participants) ? chat.participants.length : null,
        });
      } catch (err) {
        logger.warn("Skipping malformed group chat", err?.message || String(err));
      }
    }
    groups.sort((a, b) =>
      String(a.group_name).localeCompare(String(b.group_name), undefined, { sensitivity: "base" })
    );
    this.groupsCache = groups;
    this.groupsLoadedAt = new Date().toISOString();
    logger.info(`Loaded ${groups.length} groups`);
    this.emit("groups", groups);
    return groups;
  }

  getGroups() {
    return this.groupsCache;
  }

  onMessage(handler) {
    const attach = () => {
      if (!this.client) return;
      this.client.removeAllListeners("message");
      this.client.on("message", handler);
    };
    attach();
    this.on("status", (snap) => {
      if (snap.status === "CONNECTED") attach();
    });
  }
}

const whatsappService = new WhatsAppService();

module.exports = {
  whatsappService,
  WhatsAppService,
  sessionDirListingSafe() {
    // For diagnostics — never return file contents.
    try {
      if (!fs.existsSync(SESSION_DIR)) return { exists: false, entries: 0 };
      const entries = fs.readdirSync(SESSION_DIR).filter((n) => n !== ".gitkeep");
      return { exists: entries.length > 0, entries: entries.length, path: path.basename(SESSION_DIR) };
    } catch {
      return { exists: false, entries: 0 };
    }
  },
};
