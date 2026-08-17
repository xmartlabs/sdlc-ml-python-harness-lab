import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Default targets the docker-compose "api" service; override with
// VITE_API_TARGET=http://localhost:8009 when running the API on the host
// (host worker mode for the codex/claude harnesses).
const apiTarget = process.env.VITE_API_TARGET ?? "http://api:8008";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": apiTarget
    }
  }
});
