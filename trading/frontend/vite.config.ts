import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base must match the public path under Resume Agent (/trading/).
export default defineConfig({
  plugins: [react()],
  base: "/trading/",
  server: {
    host: "127.0.0.1",
    port: 5174,
    proxy: {
      "/trading/api": "http://127.0.0.1:8000",
      "/trading/ws": {
        target: "ws://127.0.0.1:8000",
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
