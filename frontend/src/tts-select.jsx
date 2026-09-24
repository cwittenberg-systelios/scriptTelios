// ────────────────────────────────────────────────────────────────────────────
// src/tts-select.jsx — Auswahl der Vorlese-Stimme (v19.35, Testphase).
//
// Browser | Piper | Chatterbox. Die Liste kommt vom Backend
// (GET /interview/tts/engines); nicht verfuegbare Engines sind ausgegraut,
// der Grund steht im Tooltip. Faellt die Server-Stimme waehrend des Vorlesens
// aus, uebernimmt die Browser-Stimme und hier erscheint ein Hinweis.
// ────────────────────────────────────────────────────────────────────────────
import { useEffect, useRef, useState } from "react";
import { fetchTtsEngines } from "./api.js";
import { getTtsEngine, setTtsEngine, TTS_FALLBACK_EVENT } from "./speech.js";

// v19.37.2: Nur eine VOLLSTAENDIGE Liste (alle Server-Stimmen verfuegbar)
// wird gemerkt. Vorher blieb eine beim Pod-Start geholte Liste ("Dienst nicht
// erreichbar") bis zum Neuladen der Seite stehen.
let _enginesPromise = null;
const RETRY_MS = 20000;
const RETRY_MAX = 15;

function _complete(list) {
  return Array.isArray(list) && list.length > 1 && list.every(e => e.available);
}
function loadEngines(force = false) {
  if (!_enginesPromise || force) {
    let p;
    try { p = Promise.resolve(fetchTtsEngines()).catch(() => null); }
    catch (_) { p = Promise.resolve(null); }
    _enginesPromise = p.then(list => { if (!_complete(list)) _enginesPromise = null; return list; });
  }
  return _enginesPromise;
}
// Fuer Tests
function _resetTtsEngines() { _enginesPromise = null; }

function TtsSelect({ onChange, disabled = false }) {
  const [engines, setEngines] = useState(null);
  const [engine, setEngine] = useState(getTtsEngine());
  const [note, setNote] = useState("");
  const aliveRef = useRef(true);

  function apply(list) {
    if (!aliveRef.current) return;
    const l = Array.isArray(list) && list.length ? list : [{ key: "browser", label: "Browser", available: true }];
    setEngines(l);
    // gespeicherte Wahl nicht (mehr) verfuegbar -> zurueck auf Browser
    const cur = l.find(e => e.key === getTtsEngine());
    if (!cur || !cur.available) { setTtsEngine("browser"); setEngine("browser"); }
  }
  function refresh() { loadEngines(true).then(apply); }

  useEffect(() => {
    aliveRef.current = true;
    let tries = 0;
    let timer = null;
    const tick = () => {
      loadEngines(tries > 0).then(list => {
        apply(list);
        tries += 1;
        if (aliveRef.current && !_complete(list) && tries < RETRY_MAX) timer = setTimeout(tick, RETRY_MS);
      });
    };
    tick();
    const onFb = (ev) => { if (aliveRef.current) setNote(ev?.detail?.message || "Server-Stimme nicht verfügbar – Browser-Stimme übernimmt."); };
    const onOk = () => refresh();
    window.addEventListener(TTS_FALLBACK_EVENT, onFb);
    window.addEventListener("st-health-ok", onOk);
    return () => {
      aliveRef.current = false;
      if (timer) clearTimeout(timer);
      window.removeEventListener(TTS_FALLBACK_EVENT, onFb);
      window.removeEventListener("st-health-ok", onOk);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
        onMouseDown={() => { if (!_complete(engines)) refresh(); }} onFocus={() => { if (!_complete(engines)) refresh(); }}
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
