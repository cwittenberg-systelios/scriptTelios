// vite.config.js – sysTelios KI-Dokumentation
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../backend/static",
    lib: {
      entry:    "klinische-dokumentation.jsx",
      name:     "SysTelios",
      fileName: () => "systelios.js",
      formats:  ["umd"],
    },
  },
});
