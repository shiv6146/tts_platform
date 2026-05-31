import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/",
  server: {
    port: 5173,
    proxy: {
      "/v1": { target: "http://localhost:8080", changeOrigin: true },
      "/health": { target: "http://localhost:8080" },
      "/livez": { target: "http://localhost:8080" },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
