"use strict";

const fs = require("fs");
const express = require("express");
const cors = require("cors");
const {
  HOST,
  PORT,
  BASE_PATH,
  DATA_DIR,
  SESSION_DIR,
  LOG_DIR,
} = require("./config");
const logger = require("./logger");
const db = require("./db");
const { createApiRouter, mountFrontend } = require("./api");
const { monitorService } = require("./monitorService");
const { whatsappService } = require("./whatsappService");

function main() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.mkdirSync(SESSION_DIR, { recursive: true });
  fs.mkdirSync(LOG_DIR, { recursive: true });

  db.initDb();
  monitorService.bindWhatsApp();

  const app = express();
  app.disable("x-powered-by");
  app.use(cors({ origin: true }));
  app.use(express.json({ limit: "1mb" }));

  // Never expose session files via static routes.
  app.use((req, res, next) => {
    const p = req.path || "";
    if (p.includes("..") || p.includes("session") || p.includes(".wwebjs")) {
      if (p.includes("/api/")) return next();
      // Block accidental static probing of session paths.
      if (/\.(json|zip|db|dat)$/i.test(p) && /session|wwebjs|auth/i.test(p)) {
        return res.status(404).end();
      }
    }
    next();
  });

  const api = createApiRouter();
  app.use(`${BASE_PATH}/api`, api);
  // Convenience when proxy strips base path.
  app.use("/api", api);

  mountFrontend(app, BASE_PATH);

  app.get("/healthz", (_req, res) => {
    res.json({ ok: true, service: "whatsapp-monitor" });
  });

  const server = app.listen(PORT, HOST, () => {
    logger.info("WhatsApp Monitor listening", {
      host: HOST,
      port: PORT,
      base_path: BASE_PATH,
    });
  });

  // Restore WhatsApp session on boot (QR only if needed). Monitoring stays STOPPED.
  setTimeout(() => {
    whatsappService.start().catch((err) => {
      logger.warn("Auto session restore deferred", err?.message || String(err));
    });
  }, 500);

  const shutdown = async (signal) => {
    logger.info(`Shutting down (${signal})`);
    try {
      await whatsappService.stopClient();
    } catch {
      // ignore
    }
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 5000).unref();
  };
  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("SIGTERM", () => shutdown("SIGTERM"));
}

main();
