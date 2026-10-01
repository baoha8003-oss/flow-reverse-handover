import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      // import.meta.dirname, not __dirname: Vite's native config loader
      // (already the default in a future major) cannot evaluate __dirname
      // and warns on every run.
      "@": path.resolve(import.meta.dirname, "src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8101",
      "/media": "http://localhost:8101",
      "/ws": {
        target: "ws://localhost:8101",
        ws: true,
      },
    },
  },
  // Node environment: the store under test is plain TypeScript over fetch,
  // so there is nothing to gain from paying for a DOM.
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
