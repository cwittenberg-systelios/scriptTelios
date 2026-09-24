// ────────────────────────────────────────────────────────────────────────────
// src/tts-select.jsx — Auswahl der Vorlese-Stimme (v19.35, v19.39).
//
// v19.39: Chatterbox-Stimmen (Referenzstimmen auf dem Pod), dazu als letzte
// Option die Browser-Stimme ("schnell"). Die Liste kommt vom Backend
// (GET /interview/tts/engines -> {engines, default}). Wirksame Stimme:
// gespeicherte Wahl des Nutzers (localStorage) - sonst der Server-Default
// (TTS_DEFAULT_VOICE) - sonst die erste verfuegbare (Browser zuletzt). Eine
// automatisch gewaehlte Stimme ueberschreibt die gespeicherte Wahl NICHT.
// Unvollstaendige Listen (Dienst startet noch) werden nicht gemerkt und
// erneut abgefragt (v19.37.2).
// ────────────────────────────────────────────────────────────────────────────
import { useEffect, useRef, useState } from "react";
import { fetchTtsEngines } from "./api.js";
import { BROWSER_ENGINE, browserAvailable, getStoredTtsEngine, getSpeechProvider, setActiveTtsEngine, setTtsEngine, TTS_FALLBACK_EVENT } from "./speech.js";

let _enginesPromise = null;
const RETRY_MS = 20000;
const RETRY_MAX = 15;

function _norm(data) {
  if (Array.isArray(data)) return { engines: data, default: null, reason: "" };   // alte Backends
  return { engines: Array.isArray(data?.engines) ? data.engines : [], default: data?.default || null, reason: data?.reason || "" };
}
function _complete(d) {
  const server = d ? d.engines.filter(e => e.key !== "browser") : [];
  return server.length > 0 && server.every(e => e.available);
}
// Browser-Stimme als letzte Option anhaengen (falls der Browser sprechen kann)
function _withBrowser(d) {
  if (!browserAvailable() || d.engines.some(e => e.key === "browser")) return d;
  return { ...d, engines: [...d.engines, BROWSER_ENGINE] };
}
function loadEngines(force = false) {
  if (!_enginesPromise || force) {
    let p;
    try { p = Promise.resolve(fetchTtsEngines()).catch(() => null); }
    catch (_) { p = Promise.resolve(null); }
    _enginesPromise = p.then(raw => { const d = _withBrowser(_norm(raw)); if (!_complete(d)) _enginesPromise = null; return d; });
  }
  return _enginesPromise;
}
// Fuer Tests
function _resetTtsEngines() { _enginesPromise = null; }

// Wirksame Stimme aus Liste, Nutzerwahl und Server-Default
function chooseEngine(d, stored) {
  const ok = d.engines.filter(e => e.available).map(e => e.key);
  if (stored && ok.includes(stored)) return stored;
  if (d.default && ok.includes(d.default)) return d.default;
  return ok[0] || "";
}

function TtsSelect({ onChange, disabled = false }) {
  const [data, setData] = useState(null);
  const [engine, setEngine] = useState("");
  const [note, setNote] = useState("");
  const aliveRef = useRef(true);

  function apply(d) {
    if (!aliveRef.current) return;
    setData(d);
    const k = chooseEngine(d, getStoredTtsEngine());
    setActiveTtsEngine(k);
    setEngine(k);
  }
  function refresh() { loadEngines(true).then(apply); }

  useEffect(() => {
    aliveRef.current = true;
    let tries = 0;
    let timer = null;
    const tick = () => {
      loadEngines(tries > 0).then(d => {
        apply(d);
        tries += 1;
        if (aliveRef.current && !_complete(d) && tries < RETRY_MAX) timer = setTimeout(tick, RETRY_MS);
      });
    };
    tick();
    const onFb = (ev) => { if (aliveRef.current) setNote(ev?.detail?.message || "Vorlesen gerade nicht möglich."); };
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

  const soft = { fontSize: 11, color: "var(--st-text-soft)" };
  if (!data) return null;
  if (!data.engines.some(e => e.available)) {
    return <span style={soft} data-testid="tts-none" title={data.reason || ""}>
      Vorlesen gerade nicht verfügbar{data.reason ? ` (${data.reason})` : ""}
    </span>;
  }
  function change(e) {
    const k = e.target.value;
    setTtsEngine(k); setEngine(k); setNote("");
    onChange && onChange(k);
  }
  return (
    <span style={{ ...soft, display: "inline-flex", alignItems: "center", gap: 4 }}>
      Stimme
      <select value={engine} onChange={change} disabled={disabled} data-testid="tts-engine"
        onMouseDown={() => { if (!_complete(data)) refresh(); }} onFocus={() => { if (!_complete(data)) refresh(); }}
        style={{ fontSize: 11, padding: "2px 4px", borderRadius: 3, border: "1px solid var(--st-gray-border)", background: "var(--st-bg)", color: "var(--st-text)" }}>
        {data.engines.map(e => (
          <option key={e.key} value={e.key} disabled={!e.available} title={e.reason || ""}>
            {e.label}{!e.available ? " (nicht verfügbar)" : ""}
          </option>
        ))}
      </select>
      {!data.engines.some(e => e.key !== "browser" && e.available) && data.reason &&
        <span data-testid="tts-server-reason">Server-Stimmen gerade nicht verfügbar ({data.reason})</span>}
      {engine === "browser" && getSpeechProvider().voiceStatus?.() === "none" &&
        <span data-testid="tts-browser-hint">keine lokale deutsche Stimme – es wird nicht vorgelesen</span>}
      {note && <span style={{ color: "var(--st-red)" }} data-testid="tts-fallback">{note}</span>}
    </span>
  );
}

export { TtsSelect, chooseEngine, _resetTtsEngines };
