"use strict";

const EventEmitter = require("events");
const db = require("./db");
const logger = require("./logger");
const { normalizeIncomingMessage } = require("./messageUtils");
const { whatsappService } = require("./whatsappService");

/**
 * Monitor runtime: RUNNING | PAUSED | STOPPED
 * After full process restart, always STOPPED (require explicit START).
 */
class MonitorService extends EventEmitter {
  constructor() {
    super();
    this.monitorStatus = "STOPPED";
    this._bound = false;
  }

  getSnapshot() {
    const stats = db.messageStats();
    const monitored = db.getMonitoredGroups().filter((g) => g.enabled);
    const wa = whatsappService.getSnapshot();
    return {
      connection: wa,
      monitor_status: this.monitorStatus,
      monitored_groups_count: monitored.length,
      monitored_groups: monitored,
      messages_received: stats.total_messages,
      messages_today: stats.messages_today,
      total_messages: stats.total_messages,
      last_message_at: stats.last_message_at,
      groups: whatsappService.getGroups(),
    };
  }

  bindWhatsApp() {
    if (this._bound) return;
    this._bound = true;
    whatsappService.onMessage(async (msg) => {
      try {
        await this._handleMessage(msg);
      } catch (err) {
        logger.error("Unhandled message processing error", err?.message || String(err));
      }
    });
    whatsappService.on("status", () => this.emit("status", this.getSnapshot()));
    whatsappService.on("groups", () => this.emit("status", this.getSnapshot()));
  }

  async ensureClient() {
    this.bindWhatsApp();
    return whatsappService.start();
  }

  async start() {
    await this.ensureClient();
    const enabled = db.getEnabledGroupIds();
    if (enabled.size === 0) {
      logger.warn("Monitoring start requested with no selected groups");
    }
    this.monitorStatus = "RUNNING";
    logger.info("Monitoring started");
    for (const id of enabled) {
      const g = db.getMonitoredGroups().find((x) => x.group_id === id);
      logger.info(`Monitoring group: ${g?.group_name || id}`);
    }
    this.emit("status", this.getSnapshot());
    return this.getSnapshot();
  }

  pause() {
    if (this.monitorStatus === "STOPPED") {
      return this.getSnapshot();
    }
    this.monitorStatus = "PAUSED";
    logger.info("Monitoring paused");
    this.emit("status", this.getSnapshot());
    return this.getSnapshot();
  }

  stop() {
    this.monitorStatus = "STOPPED";
    logger.info("Monitoring stopped");
    this.emit("status", this.getSnapshot());
    return this.getSnapshot();
  }

  clearMessages() {
    const removed = db.clearMessages();
    logger.info("Message history cleared", { removed });
    this.emit("status", this.getSnapshot());
    this.emit("cleared", { removed });
    return { removed, ...this.getSnapshot() };
  }

  saveGroups(selection) {
    const saved = db.saveMonitoredGroups(selection);
    logger.info("Saved group selection", { count: saved.length });
    this.emit("status", this.getSnapshot());
    return this.getSnapshot();
  }

  async _handleMessage(msg) {
    if (this.monitorStatus !== "RUNNING") {
      return;
    }

    let chat;
    try {
      chat = await msg.getChat();
    } catch (err) {
      logger.warn("Could not load chat for message", err?.message || String(err));
      return;
    }

    // Groups only — ignore private/direct chats.
    if (!chat || !chat.isGroup) {
      return;
    }

    const groupId = chat.id?._serialized;
    if (!groupId) {
      logger.warn("Message missing group id — ignored");
      return;
    }

    const enabled = db.getEnabledGroupIds();
    if (!enabled.has(groupId)) {
      return;
    }

    logger.info("New message received", { group_id: groupId, type: msg.type });

    let row;
    try {
      row = await normalizeIncomingMessage(msg, chat);
    } catch (err) {
      logger.error("Malformed message — skipped", err?.message || String(err));
      return;
    }

    let inserted = false;
    try {
      inserted = db.insertMessage(row);
    } catch (err) {
      logger.error("Database unavailable or insert failed", err?.message || String(err));
      return;
    }

    if (!inserted) {
      logger.info("Duplicate message ignored", { message_id: row.message_id });
      return;
    }

    logger.info("Message saved", { message_id: row.message_id });
    const stored = {
      ...row,
      // Ensure links is array for SSE consumers
      links: row.links || [],
    };
    this.emit("message", stored);
    this.emit("status", this.getSnapshot());
  }
}

const monitorService = new MonitorService();

module.exports = { monitorService, MonitorService };
