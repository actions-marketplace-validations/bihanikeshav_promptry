import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// DEMO=1 builds the SAME app as a static, backend-less demo for GitHub Pages:
// base path /demo/, output to docs/demo, and VITE_DEMO flips on the mock-fetch
// layer + hash routing (so deep links work without server rewrites).
export default defineConfig(() => {
  const demo = process.env.DEMO === "1";
  return {
    plugins: [react()],
    base: demo ? "/demo/" : "/",
    define: { "import.meta.env.VITE_DEMO": JSON.stringify(demo ? "1" : "") },
    build: {
      outDir: demo ? "../docs/demo" : "../promptry/dashboard/static",
      emptyOutDir: true,
    },
    server: {
      proxy: {
        "/api": "http://localhost:8420",
      },
    },
  };
});
