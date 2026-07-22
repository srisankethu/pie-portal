import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend runs on :8000. In dev we proxy /api to it so the frontend can use
// same-origin relative URLs.
export default defineConfig({
  plugins: [react()],
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