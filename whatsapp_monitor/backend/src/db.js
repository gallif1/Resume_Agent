"use strict";

const fs = require("fs");
const Database = require("better-sqlite3");
const { DATA_DIR, DB_PATH } = require("./config");
const logger = require("./logger");

let db;

function initDb() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  db = new Database(DB_PATH);
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");

  db.exec(`
    CREATE TABLE IF NOT EXISTS whatsapp_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      message_id TEXT NOT NULL,
      group_id TEXT NOT NULL,
      group_name TEXT,
      sender_id TEXT,
      sender_name TEXT,
      message_text TEXT,
      message_type TEXT,
      timestamp TEXT,
      received_at TEXT NOT NULL,
      links TEXT,
      has_media INTEGER NOT NULL DEFAULT 0,
      media_type TEXT
    );

    CREATE UNIQUE INDEX IF NOT EXISTS idx_whatsapp_messages_message_id
      ON whatsapp_messages(message_id);
    CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_group_id
      ON whatsapp_messages(group_id);
    CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_timestamp
      ON whatsapp_messages(timestamp);

    CREATE TABLE IF NOT EXISTS monitored_groups (
      group_id TEXT PRIMARY KEY,
      group_name TEXT,
      enabled INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS monitor_state (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
  `);

  logger.info("Database ready", { path: DB_PATH });
  return db;
}

function getDb() {
  if (!db) return initDb();
  return db;
}

function insertMessage(row) {
  const stmt = getDb().prepare(`
    INSERT OR IGNORE INTO whatsapp_messages (
      message_id, group_id, group_name, sender_id, sender_name,
      message_text, message_type, timestamp, received_at, links,
      has_media, media_type
    ) VALUES (
      @message_id, @group_id, @group_name, @sender_id, @sender_name,
      @message_text, @message_type, @timestamp, @received_at, @links,
      @has_media, @media_type
    )
  `);
  const result = stmt.run({
    message_id: row.message_id,
    group_id: row.group_id,
    group_name: row.group_name || null,
    sender_id: row.sender_id || null,
    sender_name: row.sender_name || null,
    message_text: row.message_text || "",
    message_type: row.message_type || "chat",
    timestamp: row.timestamp || null,
    received_at: row.received_at,
    links: JSON.stringify(row.links || []),
    has_media: row.has_media ? 1 : 0,
    media_type: row.media_type || null,
  });
  return result.changes > 0;
}

function listMessages({ search, groupId, sender, messageType, limit = 200, offset = 0 } = {}) {
  const clauses = [];
  const params = {};

  if (search && search.trim()) {
    clauses.push("message_text LIKE @search");
    params.search = `%${search.trim()}%`;
  }
  if (groupId) {
    clauses.push("group_id = @groupId");
    params.groupId = groupId;
  }
  if (sender && sender.trim()) {
    clauses.push("(sender_name LIKE @sender OR sender_id LIKE @sender)");
    params.sender = `%${sender.trim()}%`;
  }
  if (messageType) {
    clauses.push("message_type = @messageType");
    params.messageType = messageType;
  }

  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  params.limit = Math.min(Math.max(Number(limit) || 200, 1), 500);
  params.offset = Math.max(Number(offset) || 0, 0);

  const rows = getDb()
    .prepare(
      `SELECT * FROM whatsapp_messages ${where}
       ORDER BY COALESCE(timestamp, received_at) DESC, id DESC
       LIMIT @limit OFFSET @offset`
    )
    .all(params);

  return rows.map(hydrateMessage);
}

function hydrateMessage(row) {
  let links = [];
  try {
    links = JSON.parse(row.links || "[]");
  } catch {
    links = [];
  }
  return {
    id: row.id,
    message_id: row.message_id,
    group_id: row.group_id,
    group_name: row.group_name,
    sender_id: row.sender_id,
    sender_name: row.sender_name,
    message_text: row.message_text,
    message_type: row.message_type,
    timestamp: row.timestamp,
    received_at: row.received_at,
    links,
    has_media: Boolean(row.has_media),
    media_type: row.media_type,
  };
}

function clearMessages() {
  const result = getDb().prepare("DELETE FROM whatsapp_messages").run();
  return result.changes;
}

function messageStats() {
  const total = getDb().prepare("SELECT COUNT(*) AS c FROM whatsapp_messages").get().c;
  const todayStart = new Date();
  todayStart.setUTCHours(0, 0, 0, 0);
  const todayIso = todayStart.toISOString();
  const today = getDb()
    .prepare(
      `SELECT COUNT(*) AS c FROM whatsapp_messages
       WHERE received_at >= @todayIso OR timestamp >= @todayIso`
    )
    .get({ todayIso }).c;
  const last = getDb()
    .prepare(
      `SELECT received_at, timestamp FROM whatsapp_messages
       ORDER BY COALESCE(timestamp, received_at) DESC, id DESC LIMIT 1`
    )
    .get();
  return {
    total_messages: total,
    messages_today: today,
    last_message_at: last ? last.timestamp || last.received_at : null,
  };
}

function getMonitoredGroups() {
  return getDb()
    .prepare("SELECT * FROM monitored_groups ORDER BY group_name COLLATE NOCASE ASC")
    .all()
    .map((g) => ({
      group_id: g.group_id,
      group_name: g.group_name,
      enabled: Boolean(g.enabled),
      created_at: g.created_at,
      updated_at: g.updated_at,
    }));
}

function getEnabledGroupIds() {
  return new Set(
    getDb()
      .prepare("SELECT group_id FROM monitored_groups WHERE enabled = 1")
      .all()
      .map((r) => r.group_id)
  );
}

function saveMonitoredGroups(groups) {
  const now = new Date().toISOString();
  const upsert = getDb().prepare(`
    INSERT INTO monitored_groups (group_id, group_name, enabled, created_at, updated_at)
    VALUES (@group_id, @group_name, @enabled, @created_at, @updated_at)
    ON CONFLICT(group_id) DO UPDATE SET
      group_name = excluded.group_name,
      enabled = excluded.enabled,
      updated_at = excluded.updated_at
  `);
  const disableAll = getDb().prepare("UPDATE monitored_groups SET enabled = 0, updated_at = @now");
  const tx = getDb().transaction((items) => {
    disableAll.run({ now });
    for (const g of items) {
      upsert.run({
        group_id: g.group_id,
        group_name: g.group_name || g.group_id,
        enabled: g.enabled === false ? 0 : 1,
        created_at: now,
        updated_at: now,
      });
    }
  });
  tx(groups || []);
  return getMonitoredGroups().filter((g) => g.enabled);
}

function getState(key, fallback = null) {
  const row = getDb().prepare("SELECT value FROM monitor_state WHERE key = ?").get(key);
  return row ? row.value : fallback;
}

function setState(key, value) {
  getDb()
    .prepare(
      `INSERT INTO monitor_state (key, value) VALUES (@key, @value)
       ON CONFLICT(key) DO UPDATE SET value = excluded.value`
    )
    .run({ key, value: String(value) });
}

module.exports = {
  initDb,
  getDb,
  insertMessage,
  listMessages,
  clearMessages,
  messageStats,
  getMonitoredGroups,
  getEnabledGroupIds,
  saveMonitoredGroups,
  getState,
  setState,
  DB_PATH,
};
