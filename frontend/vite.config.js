// vite.config.js – sysTelios KI-Dokumentation
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../backend/static",
    rollupOptions: {
      input: "index.html",
      output: {
        // Alles in eine einzige Datei bündeln
        inlineDynamicImports: true,
        entryFileNames: "systelios.js",
        assetFileNames: "systelios[extname]",
      },
    },
  },
});
