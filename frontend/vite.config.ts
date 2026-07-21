import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend runs on :8000. In dev we proxy /api to it so the frontend can use
// same-origin relative URLs.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.API_TARGET || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
