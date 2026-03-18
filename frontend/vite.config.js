import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../backend/static",
    emptyOutDir: false,
    lib: {
      entry:    "main.jsx",
      name:     "SysTelios",
      fileName: () => "systelios.js",
      formats:  ["iife"],
    },
  },
});
