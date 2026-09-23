import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Public path under Resume Agent: /whatsapp-monitor/
export default defineConfig({
  plugins: [react()],
  base: "/whatsapp-monitor/",
  server: {
    host: "127.0.0.1",
    port: 5175,
    proxy: {
      "/whatsapp-monitor/api": {
        target: "http://127.0.0.1:3100",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
