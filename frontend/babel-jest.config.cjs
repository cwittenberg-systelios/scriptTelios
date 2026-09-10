// Babel-Konfiguration NUR fuer Jest (JSX in Hook-/Komponenten-Tests).
// Bewusst nicht als babel.config.* benannt, damit Vite/@vitejs/plugin-react sie
// nicht automatisch aufnimmt (preset-env fuer Node wuerde das Bundle brechen).
module.exports = {
  presets: [
    ["@babel/preset-env", { targets: { node: "current" } }],
    ["@babel/preset-react", { runtime: "automatic" }],
  ],
};
