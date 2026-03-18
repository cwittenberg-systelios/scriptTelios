// vite.config.js – sysTelios KI-Dokumentation
// Build:  npm run build
// Output: backend/static/systelios.js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../backend/static",
    emptyOutDir: false,
    rollupOptions: {
      input: "entry.jsx",
      output: {
        entryFileNames: "systelios.js",
        chunkFileNames: "systelios-[hash].js",
        assetFileNames: "systelios-[hash][extname]",
        format: "iife",
        name: "SysTelios",
        inlineDynamicImports: true,
      },
    },
  },
});
