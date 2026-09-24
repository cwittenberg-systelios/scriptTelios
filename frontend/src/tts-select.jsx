// ────────────────────────────────────────────────────────────────────────────
// src/tts-select.jsx — Auswahl der Vorlese-Stimme (v19.35, Testphase).
//
// Browser | Piper | Chatterbox. Die Liste kommt vom Backend
// (GET /interview/tts/engines); nicht verfuegbare Engines sind ausgegraut,
// der Grund steht im Tooltip. Faellt die Server-Stimme waehrend des Vorlesens
// aus, uebernimmt die Browser-Stimme und hier erscheint ein Hinweis.
// ────────────────────────────────────────────────────────────────────────────
import { useEffect, useState } from "react";
import { fetchTtsEngines } from "./api.js";
import { getTtsEngine, setTtsEngine, TTS_FALLBACK_EVENT } from "./speech.js";

let _enginesPromise = null;
function loadEngines() {
  if (!_enginesPromise) {
    try { _enginesPromise = Promise.resolve(fetchTtsEngines()).catch(() => null); }
    catch (_) { _enginesPromise = Promise.resolve(null); }
  }
  return _enginesPromise;
}
// Fuer Tests
function _resetTtsEngines() { _enginesPromise = null; }

function TtsSelect({ onChange, disabled = false }) {
  const [engines, setEngines] = useState(null);
  const [engine, setEngine] = useState(getTtsEngine());
  const [note, setNote] = useState("");

  useEffect(() => {
    let alive = true;
    loadEngines().then(list => {
      if (!alive) return;
      const l = Array.isArray(list) && list.length ? list : [{ key: "browser", label: "Browser", available: true }];
      setEngines(l);
      // gespeicherte Wahl nicht mehr verfuegbar -> zurueck auf Browser
      const cur = l.find(e => e.key === getTtsEngine());
      if (!cur || !cur.available) { setTtsEngine("browser"); setEngine("browser"); }
    });
    const onFb = (ev) => { if (alive) setNote(ev?.detail?.message || "Server-Stimme nicht verfügbar – Browser-Stimme übernimmt."); };
    window.addEventListener(TTS_FALLBACK_EVENT, onFb);
    return () => { alive = false; window.removeEventListener(TTS_FALLBACK_EVENT, onFb); };
  }, []);

  if (!engines || engines.length < 2) return null;   // nur Browser -> keine Auswahl zeigen
  function change(e) {
    const k = e.target.value;
    setTtsEngine(k); setEngine(k); setNote("");
    onChange && onChange(k);
  }
  const soft = { fontSize: 11, color: "var(--st-text-soft)" };
  return (
    <span style={{ ...soft, display: "inline-flex", alignItems: "center", gap: 4 }}>
      Stimme
      <select value={engine} onChange={change} disabled={disabled} data-testid="tts-engine"
        style={{ fontSize: 11, padding: "2px 4px", borderRadius: 3, border: "1px solid var(--st-gray-border)", background: "var(--st-bg)", color: "var(--st-text)" }}>
        {engines.map(e => (
          <option key={e.key} value={e.key} disabled={!e.available} title={e.reason || ""}>
            {e.label}{!e.available ? " (nicht verfügbar)" : ""}
          </option>
        ))}
      </select>
      {note && <span style={{ color: "var(--st-red)" }} data-testid="tts-fallback">{note}</span>}
    </span>
  );
}

export { TtsSelect, _resetTtsEngines };
