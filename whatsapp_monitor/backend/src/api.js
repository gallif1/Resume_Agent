"use strict";

const express = require("express");
const path = require("path");
const fs = require("fs");
const db = require("./db");
const logger = require("./logger");
const { FRONTEND_DIST } = require("./config");
const { monitorService } = require("./monitorService");
const { whatsappService, sessionDirListingSafe } = require("./whatsappService");

function createApiRouter() {
  const router = express.Router();

  router.get("/health", (_req, res) => {
    res.json({
      ok: true,
      service: "whatsapp-monitor",
      ...monitorService.getSnapshot(),
      session: sessionDirListingSafe(),
    });
  });

  router.get("/status", (_req, res) => {
    res.json(monitorService.getSnapshot());
  });

  router.get("/logs", (req, res) => {
    const limit = Number(req.query.limit) || 100;
    res.json({ ok: true, logs: logger.recent(limit) });
  });

  router.post("/connect", async (req, res) => {
    try {
      const reset =
        req.query.reset === "1" ||
        req.query.reset === "true" ||
        req.body?.reset === true ||
        whatsappService.status === "SESSION_ERROR";
      if (reset) {
        await whatsappService.resetAndConnect();
      } else {
        await monitorService.ensureClient();
      }
      res.json(monitorService.getSnapshot());
    } catch (err) {
      logger.error("Connect failed", err?.message || String(err));
      res.status(500).json({ error: err?.message || "Connect failed" });
    }
  });

  router.post("/session/reset", async (_req, res) => {
    try {
      await whatsappService.resetAndConnect();
      res.json(monitorService.getSnapshot());
    } catch (err) {
      logger.error("Session reset failed", err?.message || String(err));
      res.status(500).json({ error: err?.message || "Session reset failed" });
    }
  });

  router.post("/disconnect", async (_req, res) => {
    try {
      // Stops the browser client but keeps LocalAuth session on disk.
      monitorService.stop();
      await whatsappService.stopClient();
      res.json(monitorService.getSnapshot());
    } catch (err) {
      res.status(500).json({ error: err?.message || "Disconnect failed" });
    }
  });

  router.get("/groups", async (req, res) => {
    try {
      if (whatsappService.status === "CONNECTED" && req.query.refresh === "1") {
        await whatsappService.refreshGroups();
      }
      res.json({
        groups: whatsappService.getGroups(),
        selected: db.getMonitoredGroups(),
        connection: whatsappService.getSnapshot(),
      });
    } catch (err) {
      res.status(500).json({ error: err?.message || "Failed to list groups" });
    }
  });

  router.post("/groups/selection", (req, res) => {
    try {
      const groups = Array.isArray(req.body?.groups) ? req.body.groups : [];
      const snap = monitorService.saveGroups(
        groups.map((g) => ({
          group_id: String(g.group_id),
          group_name: g.group_name ? String(g.group_name) : String(g.group_id),
          enabled: g.enabled !== false,
        }))
      );
      res.json(snap);
    } catch (err) {
      res.status(500).json({ error: err?.message || "Failed to save selection" });
    }
  });

  router.post("/monitor/start", async (_req, res) => {
    try {
      const snap = await monitorService.start();
      res.json(snap);
    } catch (err) {
      res.status(500).json({ error: err?.message || "Start failed" });
    }
  });

  router.post("/monitor/pause", (_req, res) => {
    res.json(monitorService.pause());
  });

  router.post("/monitor/stop", (_req, res) => {
    res.json(monitorService.stop());
  });

  router.post("/monitor/clear", (_req, res) => {
    // Clears stored messages only — never touches WhatsApp session files.
    res.json(monitorService.clearMessages());
  });

  router.get("/messages", (req, res) => {
    try {
      const messages = db.listMessages({
        search: req.query.search,
        groupId: req.query.group_id,
        sender: req.query.sender,
        messageType: req.query.message_type,
        limit: req.query.limit,
        offset: req.query.offset,
      });
      res.json({ messages, stats: db.messageStats() });
    } catch (err) {
      res.status(500).json({ error: err?.message || "Failed to list messages" });
    }
  });

  /** Server-Sent Events: status + live messages + activity log */
  router.get("/events", (req, res) => {
    const origin = req.headers.origin || "*";
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("Access-Control-Allow-Origin", origin === "null" ? "*" : origin);
    res.setHeader("Access-Control-Allow-Credentials", "true");
    res.setHeader("Access-Control-Allow-Private-Network", "true");
    res.flushHeaders?.();

    const send = (event, data) => {
      res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    };

    send("status", monitorService.getSnapshot());
    send("logs", { logs: logger.recent(80) });

    const onStatus = (snap) => send("status", snap);
    const onMessage = (msg) => send("message", msg);
    const onCleared = (payload) => send("cleared", payload);
    const onLog = (entry) => send("log", entry);

    monitorService.on("status", onStatus);
    monitorService.on("message", onMessage);
    monitorService.on("cleared", onCleared);
    const offLog = logger.onLog(onLog);

    const heartbeat = setInterval(() => {
      res.write(`: ping ${Date.now()}\n\n`);
    }, 15000);

    req.on("close", () => {
      clearInterval(heartbeat);
      monitorService.off("status", onStatus);
      monitorService.off("message", onMessage);
      monitorService.off("cleared", onCleared);
      offLog();
    });
  });

  return router;
}

function mountFrontend(app, basePath) {
  const dist = FRONTEND_DIST;
  const index = path.join(dist, "index.html");
  if (!fs.existsSync(index)) {
    logger.warn("Frontend dist not found — API only mode", { dist });
    app.get(basePath || "/", (_req, res) => {
      res
        .status(503)
        .type("html")
        .send(
          `<!doctype html><html><body style="font-family:sans-serif;padding:2rem">
           <h1>WhatsApp Monitor</h1>
           <p>Frontend build missing. Run <code>npm run build</code> in <code>whatsapp_monitor/frontend</code>.</p>
           <p>API health: <a href="${basePath}/api/health">${basePath}/api/health</a></p>
           </body></html>`
        );
    });
    return false;
  }

  const assets = path.join(dist, "assets");
  if (fs.existsSync(assets)) {
    app.use(`${basePath}/assets`, express.static(assets, { maxAge: "1h" }));
  }
  app.use(basePath, express.static(dist, { index: false }));

  app.get([basePath, `${basePath}/`], (_req, res) => {
    res.sendFile(index);
  });
  // SPA fallback under base path (not under /api)
  app.get(`${basePath}/*`, (req, res, next) => {
    if (req.path.includes("/api/")) return next();
    res.sendFile(index);
  });
  return true;
}

module.exports = { createApiRouter, mountFrontend };
