/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend runs on :8000. In dev we proxy /api to it so the frontend can use
// same-origin relative URLs.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // `src` only. Without this vitest walks node_modules looking for specs,
    // which is slow and occasionally finds someone else's.
    include: ["src/**/*.test.{ts,tsx}"],
    // The timezone tests pin the *business* zone explicitly and assert it wins.
    // Fixing the host zone to something that is neither UTC nor Asia/Kolkata
    // means a test that accidentally reads the machine's zone fails here rather
    // than passing on a laptop in Bengaluru and failing on a runner in UTC.
    env: { TZ: "America/New_York" },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    allowedHosts: [
      ".ngrok-free.app",
      ".ngrok.app",
    ],
    proxy: {
      "/api": {
        target: process.env.API_TARGET || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});