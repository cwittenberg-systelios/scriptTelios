// main.jsx – einziger Einstiegspunkt des Bundles (v19.21 S3).
// Vorher gab es drei Mount-Varianten (entry.jsx, main.jsx, Inline-Mount am
// Ende von klinische-dokumentation.jsx); nur der Inline-Mount auf
// #systelios-app war produktiv (misc/confluence-user-macro.html).
import { createRoot } from "react-dom/client";
import App from "./klinische-dokumentation.jsx";

const container = document.getElementById("systelios-app");
if (container) createRoot(container).render(<App />);
