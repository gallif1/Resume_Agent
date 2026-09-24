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
    this.loadingPercent = null;
    this.qrUpdatedAt = null;
    this._readyWatchdog = null;
  }

  getSnapshot() {
    const needsQr =
      this.status === "AUTHENTICATION_REQUIRED" ||
      this.status === "CONNECTING" ||
      this.status === "RECONNECTING";
    return {
      status: this.status,
      qr: needsQr && this.qrDataUrl ? this.qrDataUrl : null,
      qr_updated_at: this.qrUpdatedAt,
      last_error: this.lastError,
      chrome_path: this.chromePath,
      loading_percent: this.loadingPercent,
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
    // Prefer Puppeteer's bundled Chrome (matches whatsapp-web.js); Playwright Chromium
    // versions are often too old and hang after QR scan without emitting ready.
    try {
      const puppeteer = require("puppeteer");
      if (typeof puppeteer.executablePath === "function") {
        const p = puppeteer.executablePath();
        if (p) candidates.push(p);
      }
    } catch {
      // puppeteer may be nested under whatsapp-web.js
      try {
        const puppeteer = require("whatsapp-web.js/node_modules/puppeteer");
        if (typeof puppeteer.executablePath === "function") {
          const p = puppeteer.executablePath();
          if (p) candidates.push(p);
        }
      } catch {
        // ignore
      }
    }
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
      logger.activity("Cleared broken WhatsApp session files — will request a fresh QR");
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
        logger.activity("FAILED: Chromium browser missing on server — cannot show QR");
        logger.error(this.lastError);
        this._setStatus("SESSION_ERROR");
        return this.getSnapshot();
      }

      logger.activity("Starting WhatsApp connection… opening Chromium on the cloud server");
      logger.info("WhatsApp client starting", { chrome });
      this.loadingPercent = null;
      this._setStatus("CONNECTING");

      const puppeteerOpts = {
        // 'new' headless is required for current WhatsApp Web in many environments.
        headless: "new",
        executablePath: chrome,
        args: [
          "--no-sandbox",
          "--disable-setuid-sandbox",
          "--disable-dev-shm-usage",
          "--disable-gpu",
          "--no-first-run",
          "--no-default-browser-check",
          "--disable-blink-features=AutomationControlled",
          "--window-size=1280,720",
        ],
      };

      this.client = new Client({
        authStrategy: new LocalAuth({
          dataPath: SESSION_DIR,
          clientId: "whatsapp-monitor",
        }),
        puppeteer: puppeteerOpts,
        restartOnAuthFail: false,
        authTimeoutMs: 0,
        qrMaxRetries: 10,
      });

      this._bindClientEvents(this.client);

      // initialize() resolves after browser+inject (not after QR scan). Catch async failures.
      this._initPromise = this.client.initialize().catch((err) => {
        this.lastError = err?.message || String(err);
        logger.activity(`FAILED: browser/WhatsApp init error — ${this.lastError}`);
        logger.error("WhatsApp client initialize failed", this.lastError);
        this._setStatus("SESSION_ERROR");
        this.client = null;
      });

      logger.activity("Waiting for QR code from WhatsApp Web (can take up to ~45s)…");

      // Wait for QR / ready / error so Connect can return something useful.
      await this._waitForStatus(
        ["AUTHENTICATION_REQUIRED", "CONNECTED", "SESSION_ERROR"],
        45000
      );

      if (this.status === "CONNECTING" && !this.qrDataUrl) {
        this.lastError =
          this.lastError ||
          "Timed out waiting for WhatsApp QR. Click Connect again to reset the session.";
        logger.activity(`FAILED: timed out waiting for QR — ${this.lastError}`);
        logger.error(this.lastError);
        await this.destroyClient();
        this._setStatus("SESSION_ERROR");
      } else if (this.status === "AUTHENTICATION_REQUIRED") {
        logger.activity("QR is ready — scan it with your phone (Linked devices → Link a device)");
      } else if (this.status === "CONNECTED") {
        logger.activity("SUCCESS: WhatsApp is connected");
      }
    } catch (err) {
      this.lastError = err?.message || String(err);
      logger.activity(`FAILED: ${this.lastError}`);
      logger.error("WhatsApp client failed to start", this.lastError);
      this._setStatus("SESSION_ERROR");
      this.client = null;
    } finally {
      this._starting = false;
    }
    return this.getSnapshot();
  }

  _clearReadyWatchdog() {
    if (this._readyWatchdog) {
      clearTimeout(this._readyWatchdog);
      this._readyWatchdog = null;
    }
  }

  _armReadyWatchdog() {
    this._clearReadyWatchdog();
    this._readyWatchdog = setTimeout(async () => {
      if (this.status === "CONNECTED") return;
      this.lastError =
        "Phone scanned but WhatsApp Web never became ready (known library issue). Resetting session — click Connect and scan again.";
      logger.activity(`FAILED: ${this.lastError}`);
      logger.error(this.lastError);
      try {
        await this.destroyClient();
        this.clearSessionFiles();
      } catch {
        // ignore
      }
      this._setStatus("SESSION_ERROR");
    }, 120000);
  }

  _bindClientEvents(client) {
    client.on("qr", async (qr) => {
      try {
        this.qrDataUrl = await qrcode.toDataURL(qr, { margin: 1, width: 280 });
        this.qrUpdatedAt = new Date().toISOString();
        this.loadingPercent = null;
        logger.activity("QR code ready — scan NOW (codes expire ~20s; a new one appears automatically)");
        logger.info("QR code generated — authentication required");
        this.lastError = null;
        this._setStatus("AUTHENTICATION_REQUIRED");
      } catch (err) {
        this.lastError = err?.message || String(err);
        logger.activity(`FAILED: could not render QR — ${this.lastError}`);
        logger.error("Failed to render QR", this.lastError);
        this._setStatus("SESSION_ERROR");
      }
    });

    client.on("loading_screen", (percent, message) => {
      this.loadingPercent = Number(percent) || 0;
      logger.activity(`WhatsApp Web loading ${percent}%${message ? ` — ${message}` : ""}`);
      this.emit("status", this.getSnapshot());
    });

    client.on("authenticated", () => {
      logger.activity("Phone linked — finishing WhatsApp Web sync (can take 1–2 minutes)…");
      logger.info("Authentication successful");
      this.qrDataUrl = null;
      this.qrUpdatedAt = null;
      this.lastError = null;
      this._setStatus("CONNECTING");
      this._armReadyWatchdog();
    });

    client.on("ready", async () => {
      this._clearReadyWatchdog();
      logger.activity("SUCCESS: WhatsApp connected and ready");
      logger.info("WhatsApp connected");
      this._reconnectAttempts = 0;
      this.qrDataUrl = null;
      this.qrUpdatedAt = null;
      this.loadingPercent = 100;
      this.lastError = null;
      this._setStatus("CONNECTED");
      try {
        await this.refreshGroups();
        logger.activity(`Loaded ${this.groupsCache.length} WhatsApp groups`);
      } catch (err) {
        logger.warn("Group discovery after connect failed", err?.message || String(err));
        logger.activity(`Connected, but group discovery failed: ${err?.message || String(err)}`);
      }
    });

    client.on("auth_failure", (msg) => {
      this._clearReadyWatchdog();
      this.lastError = typeof msg === "string" ? msg : "Authentication failed — session reset needed";
      logger.activity(`FAILED: authentication expired/failed — ${this.lastError}`);
      logger.error("Authentication expired or failed", this.lastError);
      this._setStatus("SESSION_ERROR");
    });

    client.on("disconnected", (reason) => {
      this._clearReadyWatchdog();
      const why = reason || "unknown";
      logger.warn("WhatsApp disconnected", why);
      this.groupsCache = [];
      if (this._intentionalStop) {
        logger.activity("Disconnected intentionally (session files kept)");
        this._setStatus("DISCONNECTED");
        return;
      }
      logger.activity(`Connection dropped (${why}) — reconnecting…`);
      this._setStatus("RECONNECTING");
      this._scheduleReconnect();
    });

    client.on("change_state", (state) => {
      logger.info("WhatsApp state change", state);
      logger.activity(`WhatsApp state: ${state}`);
    });
  }

  _scheduleReconnect() {
    if (this._intentionalStop) return;
    if (this._reconnectTimer) return;
    this._reconnectAttempts += 1;
    const delay = Math.min(30000, 2000 * this._reconnectAttempts);
    logger.activity(`Reconnect attempt #${this._reconnectAttempts} in ${Math.round(delay / 1000)}s`);
    logger.info("Attempting reconnect", { attempt: this._reconnectAttempts, delay_ms: delay });
    this._reconnectTimer = setTimeout(async () => {
      this._reconnectTimer = null;
      try {
        await this.destroyClient();
        await this.start();
      } catch (err) {
        logger.error("Reconnect failed", err?.message || String(err));
        logger.activity(`FAILED: reconnect — ${err?.message || String(err)}`);
        this._setStatus("RECONNECTING");
        this._scheduleReconnect();
      }
    }, delay);
  }

  async destroyClient() {
    this._clearReadyWatchdog();
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
    this._clearReadyWatchdog();
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
