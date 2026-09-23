"use strict";

/**
 * Lightweight self-tests for WhatsApp Monitor storage + parsing.
 * Run: node backend/scripts/selftest.js
 */
const fs = require("fs");
const os = require("os");
const path = require("path");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wa-monitor-"));
process.env.WHATSAPP_DATA_DIR = path.join(tmp, "data");
process.env.WHATSAPP_SESSION_DIR = path.join(tmp, "session");
process.env.WHATSAPP_LOG_DIR = path.join(tmp, "logs");

// Re-require after env is set
delete require.cache[require.resolve("../src/config")];
delete require.cache[require.resolve("../src/db")];
delete require.cache[require.resolve("../src/messageUtils")];

const db = require("../src/db");
const { extractLinks } = require("../src/messageUtils");

function assert(cond, msg) {
  if (!cond) throw new Error(msg || "assertion failed");
}

db.initDb();

assert(extractLinks("see https://a.com/x and http://b.co/y.").length === 2, "links");
assert(extractLinks("no links") .length === 0, "no links");

const row = {
  message_id: "msg-1",
  group_id: "g1",
  group_name: "Jobs",
  sender_id: "s1",
  sender_name: "Ada",
  message_text: "Hi https://jobs.example/1",
  message_type: "chat",
  timestamp: new Date().toISOString(),
  received_at: new Date().toISOString(),
  links: extractLinks("Hi https://jobs.example/1"),
  has_media: false,
  media_type: null,
};

assert(db.insertMessage(row) === true, "insert");
assert(db.insertMessage(row) === false, "duplicate ignored");
assert(db.listMessages().length === 1, "list");

db.saveMonitoredGroups([
  { group_id: "g1", group_name: "Jobs", enabled: true },
  { group_id: "g2", group_name: "Other", enabled: false },
]);
assert(db.getEnabledGroupIds().has("g1"), "enabled");
assert(!db.getEnabledGroupIds().has("g2"), "disabled");

const cleared = db.clearMessages();
assert(cleared === 1, "clear");
assert(db.listMessages().length === 0, "empty after clear");
assert(db.getEnabledGroupIds().has("g1"), "selection survives clear");

console.log("whatsapp_monitor selftest OK");
