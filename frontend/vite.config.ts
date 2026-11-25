import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Relative base: the app is served behind Home Assistant Ingress under a changing path.
export default defineConfig({
  base: "./",
  plugins: [svelte()],
  build: { outDir: "dist", assetsInlineLimit: 0, sourcemap: false },
  server: { proxy: { "/api": { target: "http://localhost:8099", ws: true } } },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
