// vite.config.js – sysTelios KI-Dokumentation
// Build:  npm run build
// Output: dist/systelios.js  (single-file bundle für Confluence)
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../backend/static",   // direkt in Backend-Verzeichnis → von FastAPI ausgeliefert
    lib: {
      entry:    "klinische-dokumentation.jsx",
      name:     "SysTelios",
      fileName: () => "systelios.js",
      formats:  ["iife"],          // Sofort-ausführbar, kein ES-Modul nötig
    },
    rollupOptions: {
      // React als externe Abhängigkeit, falls Confluence es schon lädt –
      // oder eingebettet lassen (sicherer, daher auskommentiert):
      // external: ["react", "react-dom"],
    },
  },
});
