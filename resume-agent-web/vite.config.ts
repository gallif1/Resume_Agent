import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiPort = env.VITE_API_PORT || env.API_PORT || "8000";
  const apiTarget = `http://127.0.0.1:${apiPort}`;

  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      allowedHosts: true,
      proxy: {
        "/api": apiTarget,
        "/cvs": apiTarget,
      },
    },
  };
});
