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
    this.groupsCache = [];
    this.groupsLoadedAt = null;
    this._starting = false;
    this._intentionalStop = false;
    this._reconnectTimer = null;
    this._reconnectAttempts = 0;
  }

  getSnapshot() {
    return {
      status: this.status,
      qr: this.status === "AUTHENTICATION_REQUIRED" ? this.qrDataUrl : null,
      last_error: this.lastError,
      groups_count: this.groupsCache.length,
      groups_loaded_at: this.groupsLoadedAt,
      reconnect_attempts: this._reconnectAttempts,
    };
  }

  _setStatus(status, extra = {}) {
    this.status = status;
    if (status !== "AUTHENTICATION_REQUIRED") {
      // Keep last QR briefly while connecting after scan; clear when fully connected/error.
      if (status === "CONNECTED" || status === "SESSION_ERROR" || status === "DISCONNECTED") {
        this.qrDataUrl = null;
      }
    }
    this.emit("status", { ...this.getSnapshot(), ...extra });
  }

  async start() {
    if (this.client && (this.status === "CONNECTED" || this.status === "CONNECTING" || this.status === "AUTHENTICATION_REQUIRED" || this.status === "RECONNECTING")) {
      return this.getSnapshot();
    }
    if (this._starting) return this.getSnapshot();
    this._starting = true;
    this._intentionalStop = false;
    this.lastError = null;

    fs.mkdirSync(SESSION_DIR, { recursive: true });
    // Keep a .gitkeep out of session auth dumps — session dir itself is gitignored.
    logger.info("WhatsApp client starting");
    this._setStatus("CONNECTING");

    try {
      const puppeteerOpts = {
        headless: true,
        args: [
          "--no-sandbox",
          "--disable-setuid-sandbox",
          "--disable-dev-shm-usage",
          "--disable-gpu",
          "--no-first-run",
          "--no-default-browser-check",
        ],
      };
      if (PUPPETEER_EXECUTABLE_PATH && fs.existsSync(PUPPETEER_EXECUTABLE_PATH)) {
        puppeteerOpts.executablePath = PUPPETEER_EXECUTABLE_PATH;
      }

      this.client = new Client({
        authStrategy: new LocalAuth({
          dataPath: SESSION_DIR,
          clientId: "whatsapp-monitor",
        }),
        puppeteer: puppeteerOpts,
        restartOnAuthFail: false,
      });

      this._bindClientEvents(this.client);
      await this.client.initialize();
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
      this._setStatus("CONNECTING");
    });

    client.on("ready", async () => {
      logger.info("WhatsApp connected");
      this._reconnectAttempts = 0;
      this.qrDataUrl = null;
      this._setStatus("CONNECTED");
      try {
        await this.refreshGroups();
      } catch (err) {
        logger.warn("Group discovery after connect failed", err?.message || String(err));
      }
    });

    client.on("auth_failure", (msg) => {
      this.lastError = typeof msg === "string" ? msg : "Authentication failed";
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
      const entries = fs.readdirSync(SESSION_DIR);
      return { exists: true, entries: entries.length, path: path.basename(SESSION_DIR) };
    } catch {
      return { exists: false, entries: 0 };
    }
  },
};
