import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const buildOutDir = process.env.BUILD_TO_LEGACY === "1" ? "../frontend" : "dist";

export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: buildOutDir,
    emptyOutDir: true,
  },
  server: {
    // Use 127.0.0.1 (not "localhost") so proxy matches ``[server].host`` in config.toml — on Windows
    // ``localhost`` can resolve to ::1 while aiohttp binds IPv4-only and WS stays "offline".
    proxy: {
      "/health": {
        target: "http://127.0.0.1:8080",
      },
      "/ws": {
        target: "http://127.0.0.1:8080",
        ws: true,
      },
    },
  },
});
