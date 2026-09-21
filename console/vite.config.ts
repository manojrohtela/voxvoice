import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  // The API the dev server proxies to (override with VOXVOICE_API_ORIGIN). Same-origin by
  // construction: the browser only talks to the Vite origin, so the console needs no CORS policy
  // (security model §4).
  const apiOrigin = loadEnv(mode, ".", "VOXVOICE_").VOXVOICE_API_ORIGIN ?? "http://127.0.0.1:8000";

  return {
    // Served under /console/ both by Vite (dev) and by the API (local product mode).
    base: "/console/",
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        "/v1": apiOrigin,
        "/health": apiOrigin,
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
    },
  };
});
