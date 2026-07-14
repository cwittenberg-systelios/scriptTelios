// ────────────────────────────────────────────────────────────────────────────
// src/dnd-guard.jsx — Globaler Drag&Drop-Guard (v19.7, S1).
//
// Problem: scriptTelios läuft als User Macro direkt im Confluence-Page-DOM.
// Confluence Server 7.4 registriert seitenweite Drop-Handler, die jede
// fallengelassene Datei als Seitenanhang hochladen. Ein Fehldrop neben einer
// Dropzone würde Klientendaten als Confluence-Attachment persistieren — das
// darf niemals passieren (DSGVO).
//
// Strategie (zweistufig):
//   1. Capture-Phase-Listener auf `window` für dragenter/dragover/drop:
//      feuern vor allen Confluence-Handlern (jQuery = Bubble-Phase auf
//      document/body). Datei-Drops AUSSERHALB einer .dropzone werden hart
//      gestoppt (preventDefault + stopImmediatePropagation) → weder
//      Confluence-Attachment noch Browser-Default (Datei im Tab öffnen).
//      Drops AUF einer .dropzone werden durchgelassen, damit die
//      React-Handler der Dropzone greifen.
//   2. Bubble-Backstop am React-Root (#systelios-app): Events, die aus einer
//      Dropzone nach oben bubblen, werden NACH der React-Verarbeitung
//      gestoppt, bevor sie document/body (= Confluence) erreichen. React 18
//      delegiert am Root-Container und registriert beim createRoot — unser
//      Listener wird später registriert und läuft daher garantiert danach.
//
// Nicht-Datei-DnD (Textselektion in Textareas ziehen etc.) bleibt unberührt.
// ────────────────────────────────────────────────────────────────────────────
import { useEffect } from "react";

const GUARD_EVENTS = ["dragenter", "dragover", "drop"];

/** true, wenn der DataTransfer Dateien enthält (Text-DnD ausgenommen). */
function dtHasFiles(e) {
  const types = e.dataTransfer && e.dataTransfer.types;
  if (!types) return false;
  // DOMStringList (alt) und Array (neu) unterstützen
  for (let i = 0; i < types.length; i++) {
    if (types[i] === "Files") return true;
  }
  return false;
}

/** true, wenn das Event innerhalb einer scriptTelios-Dropzone stattfindet. */
function inDropzone(e) {
  const t = e.target;
  return !!(t && t.closest && t.closest(".dropzone"));
}

/**
 * Installiert den seitenweiten DnD-Guard für die Lebensdauer der App.
 * Einmalig im App-Root aufrufen. Räumt bei Unmount vollständig auf.
 */
export function useDndGuard() {
  useEffect(() => {
    // Stufe 1: Capture auf window — fängt alles ab, bevor Confluence es sieht.
    const onCapture = (e) => {
      if (!dtHasFiles(e)) return;
      // Browser-Default immer unterbinden (sonst öffnet der Browser die
      // Datei bei Drop außerhalb registrierter Zonen im Tab).
      e.preventDefault();
      if (!inDropzone(e)) {
        // Harte Sperre: Confluence-Handler und alle weiteren Listener aus.
        e.stopImmediatePropagation();
        // „Verboten"-Cursor als visuelles Feedback außerhalb von Dropzones.
        if (e.type === "dragover" && e.dataTransfer) {
          try { e.dataTransfer.dropEffect = "none"; } catch (_) {}
        }
      }
    };

    // Stufe 2: Bubble-Backstop am React-Root — Dropzone-Events nach der
    // React-Verarbeitung stoppen, bevor sie zu Confluence hochbubblen.
    const root =
      document.getElementById("systelios-app") ||
      document.querySelector('[id^="systelios-root-"]');
    const onBubble = (e) => {
      if (dtHasFiles(e)) e.stopPropagation();
    };

    GUARD_EVENTS.forEach((t) => window.addEventListener(t, onCapture, true));
    if (root) GUARD_EVENTS.forEach((t) => root.addEventListener(t, onBubble, false));

    return () => {
      GUARD_EVENTS.forEach((t) => window.removeEventListener(t, onCapture, true));
      if (root) GUARD_EVENTS.forEach((t) => root.removeEventListener(t, onBubble, false));
    };
  }, []);
}
