import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiPort = env.VITE_API_PORT || env.API_PORT || "8000";
  const apiTarget = `http://127.0.0.1:${apiPort}`;

  const lan =
    env.VITE_LAN === "1" ||
    env.VITE_LAN === "true" ||
    process.argv.includes("--host");

  return {
    plugins: [react()],
    server: {
      // 127.0.0.1 for desktop-only dev; 0.0.0.0 (npm run dev:lan) for phone on same Wi‑Fi
      host: lan ? true : "127.0.0.1",
      proxy: {
        "/api": apiTarget,
        "/cvs": apiTarget,
      },
    },
  };
});
