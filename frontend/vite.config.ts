/// <reference types="vitest/config" />
import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `__dirname` is not defined in an ESM config file, and the two entries below
// need absolute paths.
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// The backend runs on :8000. In dev we proxy /api to it so the frontend can use
// same-origin relative URLs.
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      // Two documents, two bundles. `index.html` is the product — the landing
      // page and the signed-in app. `operator.html` is PIE's own console, and
      // it is a separate entry rather than a route because its caller is not a
      // tenant: every screen in the app reads `session.organization_id`, and a
      // console mounted beside them would be one application whose components
      // sometimes have a tenant and sometimes do not. `docs/operator-console.md`
      // has the argument; the build-level half is that nothing in the app's
      // bundle can accidentally import a console panel, or the reverse.
      input: {
        index: path.resolve(__dirname, "index.html"),
        operator: path.resolve(__dirname, "operator.html"),
      },
    },
  },
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