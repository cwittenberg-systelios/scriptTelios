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
        entryFileNames: "systelios.js",
        chunkFileNames: "systelios-[hash].js",
        assetFileNames: "systelios-[hash][extname]",
      },
    },
  },
});
