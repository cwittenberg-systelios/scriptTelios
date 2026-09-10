// ESLint Flat Config (v19.21). Aufruf: npm run lint  (Teil des Lint-Gates fuer
// Frontend-Patches). Regelumfang bewusst schlank: Recommended + React-Hooks;
// Formatierungsregeln uebernehmen wir nicht (gewachsener Code, kein Prettier).
import js from "@eslint/js";
import globals from "globals";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  { ignores: ["node_modules/**", "coverage/**", "../backend/static/**"] },
  js.configs.recommended,
  {
    files: ["**/*.{js,jsx}"],
    plugins: { react, "react-hooks": reactHooks },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.es2021 },
    },
    settings: { react: { version: "18.3" } },
    rules: {
      ...react.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      "react/react-in-jsx-scope": "off",   // Vite/automatic runtime
      "react/prop-types": "off",           // kein PropTypes-Einsatz im Projekt
      "react/no-unescaped-entities": "off",
      // React-Compiler-Regeln (react-hooks >= 6) sind fuer den gewachsenen
      // Code (Effekte mit setState, Ref-Schreibzugriffe im Render) zu streng
      // und ohne Compiler ohne Nutzen - bewusst aus. Rules-of-Hooks bleibt.
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/refs": "off",
      "react-hooks/immutability": "off",
      "react-hooks/static-components": "off",
      "react-hooks/preserve-manual-memoization": "off",
      "react-hooks/exhaustive-deps": "warn",
      "no-unused-vars": ["error", { "argsIgnorePattern": "^_", "varsIgnorePattern": "^_", "caughtErrors": "none" }],
      "no-empty": ["error", { "allowEmptyCatch": true }],
    },
  },
  {
    files: ["tests/**/*.{js,jsx}", "jest.setup.js"],
    languageOptions: { globals: { ...globals.jest, ...globals.node } },
  },
  {
    files: ["vite.config.js", "eslint.config.js", "babel.config.cjs"],
    languageOptions: { globals: { ...globals.node } },
  },
];
