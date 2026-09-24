// ────────────────────────────────────────────────────────────────────────────
// src/speech.js — Sprachausgabe des Interview-Dialogs (v19.24, B1).
//
// Ein Provider mit Warteschlange: say([teil1, teil2]) spricht die Teile
// nacheinander (Quittung -> Rueckfrage bzw. Ueberleitung -> naechste Frage),
// cancel() bricht alles ab (z.B. beim Start einer Aufnahme).
//
// Provider "browser" = speechSynthesis (kein Server, keine Klientendaten,
// funktioniert bei ausgeschaltetem Pod). v19.35: Provider "server" holt je
// Satz ein WAV vom Backend (Chatterbox, POST /interview/tts). v19.39: die
// Browser-Stimme ist nur noch letzte Option ("schnell") und Ersatz, wenn die
// Server-Stimme ausfaellt. Welche Stimme gilt, bestimmt tts-select.jsx
// (setTtsEngine = Nutzerwahl in localStorage, setActiveTtsEngine = wirksam).
// ────────────────────────────────────────────────────────────────────────────
import { interviewTts } from "./api.js";

// v19.33: NUR Stimmen, die im Rechner selbst laufen (localService). "Google
// Deutsch" (Chrome) und "… Online (Natural)" (Edge) rechnen in der Cloud -
// der vorgelesene Text (Kuerzel, Inhalte der Antworten) ginge an Google bzw.
// Microsoft. Ohne lokale deutsche Stimme wird nicht vorgelesen (kein
// Rueckfall auf die Browser-Standardstimme, die ebenfalls remote sein kann).
function isLocalVoice(v) {
  if (!v) return false;
  if (v.localService === false) return false;
  return !/online|natural|google/i.test(v.name || "");
}

function localGermanVoices() {
  try {
    const voices = window.speechSynthesis?.getVoices?.() || [];
    return voices.filter(v => /^de/i.test(v.lang || "") && isLocalVoice(v));
  } catch { return []; }
}

function pickGermanVoice() {
  const local = localGermanVoices();
  return local.find(v => /premium|enhanced|siri/i.test(v.name)) || local[0] || null;
}

// "ok" | "none" (keine lokale deutsche Stimme) | "loading" (Liste noch leer)
function voiceStatus() {
  try {
    const all = window.speechSynthesis?.getVoices?.() || [];
    if (!all.length) return "loading";
    return pickGermanVoice() ? "ok" : "none";
  } catch { return "none"; }
}

// v19.33: Das erste Wort ging oft verloren. Zwei Ursachen, beide abgefangen:
// (1) speak() direkt nach cancel() verschluckt in Chrome den Anfang ->
//     kurze Pause vor dem ersten Satz; (2) die Audioausgabe (v.a. Bluetooth)
//     schlaeft nach Stille ein und wacht verzoegert auf -> nach laengerer
//     Pause zuerst 300 ms Stille ueber WebAudio abspielen.
const LEAD_GAP_MS = 150;
const WAKE_AFTER_IDLE_MS = 3000;
const WAKE_SILENCE_MS = 300;
let _lastSpokeAt = 0;
let _audioCtx = null;

function _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function wakeAudio() {
  const idle = Date.now() - _lastSpokeAt > WAKE_AFTER_IDLE_MS;
  if (!idle) { await _sleep(LEAD_GAP_MS); return false; }
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) { await _sleep(LEAD_GAP_MS); return false; }
    _audioCtx = _audioCtx || new Ctx();
    if (_audioCtx.state === "suspended" && _audioCtx.resume) await _audioCtx.resume();
    const len = Math.max(1, Math.floor(_audioCtx.sampleRate * WAKE_SILENCE_MS / 1000));
    const buf = _audioCtx.createBuffer(1, len, _audioCtx.sampleRate);
    const src = _audioCtx.createBufferSource();
    src.buffer = buf; src.connect(_audioCtx.destination); src.start();
  } catch { /* ohne WebAudio nur Pause */ }
  await _sleep(WAKE_SILENCE_MS);
  return true;
}

function browserAvailable() {
  try { return typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined"; }
  catch { return false; }
}

// Pause zwischen zwei Teilen (Quittung -> Rueckfrage) in ms.
const PART_GAP_MS = 350;

function createBrowserProvider() {
  let token = 0;
  function cancel() {
    token += 1;
    try { window.speechSynthesis.cancel(); } catch { /* ignoriert */ }
  }
  function speakOne(text, myToken) {
    return new Promise((resolve) => {
      if (myToken !== token || !text) return resolve(false);
      const v = pickGermanVoice();
      if (!v) return resolve(false);          // nie auf Cloud-/Standardstimme ausweichen
      const u = new SpeechSynthesisUtterance(text);
      u.lang = v.lang || "de-DE";
      u.rate = 1.0;
      u.voice = v;
      u.onend = () => { _lastSpokeAt = Date.now(); resolve(true); };
      u.onerror = () => resolve(false);
      try { window.speechSynthesis.speak(u); } catch { resolve(false); }
    });
  }
  async function say(parts) {
    cancel();
    const myToken = token;
    const list = (Array.isArray(parts) ? parts : [parts]).map(p => (p || "").trim()).filter(Boolean);
    if (list.length && pickGermanVoice()) await wakeAudio();
    for (let i = 0; i < list.length; i++) {
      if (myToken !== token) return false;
      const ok = await speakOne(list[i], myToken);
      if (!ok) return false;
      if (i < list.length - 1) await new Promise(r => setTimeout(r, PART_GAP_MS));
    }
    return myToken === token;
  }
  // v19.31 (G5): Streaming-Vorlesen. push(delta) sammelt Text, spricht jeden
  // fertigen Satz sofort; end() spricht den Rest. Saetze werden ueber eine
  // Warteschlange nacheinander gesprochen, cancel() bricht alles ab.
  function sayStream() {
    cancel();
    const myToken = token;
    let buf = "";
    const queue = [];
    let running = false;
    let ended = false;
    let first = true;
    let resolveDone = null;
    const done = new Promise(r => { resolveDone = r; });
    async function pump() {
      if (running) return;
      running = true;
      if (first) { first = false; if (pickGermanVoice()) await wakeAudio(); }
      while (queue.length) {
        if (myToken !== token) { queue.length = 0; break; }
        const s = queue.shift();
        await speakOne(s, myToken);
      }
      running = false;
      if (ended && !queue.length) resolveDone(myToken === token);
    }
    function push(delta) {
      if (myToken !== token || !delta) return;
      buf += delta;
      const parts = splitSentences(buf);
      buf = parts.rest;
      for (const s of parts.sentences) queue.push(s);
      if (parts.sentences.length) pump();
    }
    function end() {
      if (ended) return done;
      ended = true;
      const rest = buf.trim();
      buf = "";
      if (rest) queue.push(rest);
      if (!running && !queue.length) resolveDone(myToken === token); else pump();
      return done;
    }
    return { push, end, done };
  }
  return { name: "browser", available: browserAvailable, say, sayStream, cancel, voiceStatus };
}

// Satzgrenzen: . ! ? gefolgt von Leerzeichen/Ende; Abkuerzungen wie "z.B."
// oder "Frau K." bleiben zusammen (Punkt nach einzelnem Grossbuchstaben).
function splitSentences(text) {
  const sentences = [];
  let rest = text;
  const re = /([^.!?]*?[.!?]+)(?=\s)/g;
  let m; let last = 0;
  while ((m = re.exec(text)) !== null) {
    const cand = text.slice(last, m.index + m[1].length).trim();
    if (/(^|\s)[A-ZÄÖÜ]\.$/.test(cand) || /z\.B\.$/i.test(cand)) continue;
    if (cand) sentences.push(cand);
    last = m.index + m[1].length;
  }
  rest = text.slice(last);
  return { sentences, rest };
}

function createNullProvider() {
  const noop = () => ({ push: () => {}, end: async () => false, done: Promise.resolve(false) });
  return { name: "none", available: () => false, say: async () => false, sayStream: noop, cancel: () => {}, voiceStatus: () => "none" };
}

// ── v19.35: Server-Vorlesen ─────────────────────────────────────────────────

// v19.39: Nur noch Server-Stimmen (Chatterbox mit Referenzstimmen); Browser
// und Piper sind aus der Auswahl. _stored = Wahl des Nutzers (localStorage,
// nur bei aktiver Auswahl geschrieben), _active = tatsaechlich genutzte
// Stimme (gespeicherte Wahl, sonst Server-Default, sonst erste verfuegbare) -
// setzt tts-select.jsx nach dem Laden der Liste, ohne die Wahl zu
// ueberschreiben. Leer = kein Vorlesen.
const LS_TTS_ENGINE = "st_tts_engine";
const TTS_FALLBACK_EVENT = "st-tts-fallback";
let _stored = (() => { try { return localStorage.getItem(LS_TTS_ENGINE) || ""; } catch { return ""; } })();
let _active = "";
function getTtsEngine() { return _active; }
function getStoredTtsEngine() { return _stored; }
function setTtsEngine(e) {
  _stored = e || ""; _active = _stored;
  try { localStorage.setItem(LS_TTS_ENGINE, _stored); } catch { /* ignoriert */ }
}
function setActiveTtsEngine(e) { _active = e || ""; }
function _serverActive() { return !!_active && _active !== "browser"; }
const BROWSER_ENGINE = { key: "browser", label: "Browser (schnell, einfache Stimme)", available: true, reason: "" };

function _notifyFallback(msg) {
  try { window.dispatchEvent(new CustomEvent(TTS_FALLBACK_EVENT, { detail: { message: msg } })); } catch { /* ignoriert */ }
}

// fetchAudio(text, engine, signal) -> Promise<Blob>; fallback = Browser-Provider
// fuer Saetze, die der Server nicht liefern kann (Dienst aus, Fehler).
function createServerProvider({ fetchAudio = interviewTts, fallback = null, engine = () => _active } = {}) {
  let token = 0;
  let current = null;           // laufendes <audio>
  let controllers = [];
  function cancel() {
    token += 1;
    controllers.forEach(c => { try { c.abort(); } catch { /* ignoriert */ } });
    controllers = [];
    if (current) { try { current.pause(); } catch { /* ignoriert */ } current = null; }
    if (fallback) fallback.cancel();
  }
  function request(text) {
    const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    if (ctrl) controllers.push(ctrl);
    return fetchAudio(text, engine(), { signal: ctrl ? ctrl.signal : undefined })
      .then(blob => ({ blob }), err => ({ err }));
  }
  function play(blob, myToken) {
    return new Promise((resolve) => {
      if (myToken !== token) return resolve(false);
      let url = null;
      try { url = URL.createObjectURL(blob); } catch { return resolve(false); }
      const a = new Audio(url);
      current = a;
      const done = (ok) => { try { URL.revokeObjectURL(url); } catch { /* ignoriert */ } if (current === a) current = null; _lastSpokeAt = Date.now(); resolve(ok); };
      a.onended = () => done(true);
      a.onerror = () => done(false);
      const p = a.play();
      if (p && typeof p.catch === "function") p.catch(() => done(false));
    });
  }
  async function speakItem(item, myToken) {
    const res = await item.pending;
    if (myToken !== token) return false;
    if (res.blob) return play(res.blob, myToken);
    if (res.err && res.err.name === "AbortError") return false;
    _notifyFallback(fallback
      ? `Server-Stimme nicht verfügbar (${res.err?.message || "Fehler"}) – Browser-Stimme übernimmt.`
      : `Vorlesen gerade nicht möglich (${res.err?.message || "Fehler"}).`);
    return fallback ? fallback.say([item.text]) : false;
  }
  function sayStream() {
    cancel();
    const myToken = token;
    let buf = "";
    const queue = [];
    let running = false; let ended = false; let first = true;
    let resolveDone = null;
    const done = new Promise(r => { resolveDone = r; });
    async function pump() {
      if (running) return;
      running = true;
      if (first) { first = false; await wakeAudio(); }
      while (queue.length) {
        if (myToken !== token) { queue.length = 0; break; }
        await speakItem(queue.shift(), myToken);
      }
      running = false;
      if (ended && !queue.length) resolveDone(myToken === token);
    }
    // Abruf sofort starten (Satz n+1 rechnet, waehrend Satz n laeuft)
    const enqueue = (text) => queue.push({ text, pending: request(text) });
    function push(delta) {
      if (myToken !== token || !delta) return;
      buf += delta;
      const parts = splitSentences(buf);
      buf = parts.rest;
      parts.sentences.forEach(enqueue);
      if (parts.sentences.length) pump();
    }
    function end() {
      if (ended) return done;
      ended = true;
      const rest = buf.trim(); buf = "";
      if (rest) enqueue(rest);
      if (!running && !queue.length) resolveDone(myToken === token); else pump();
      return done;
    }
    // add(text): ganzer Teil als ein Eintrag (fuer say(), ohne Satzsplit)
    function add(text) { if (myToken !== token || !text) return; enqueue(text); pump(); }
    return { push, end, done, add };
  }
  async function say(parts) {
    const s = sayStream();
    (Array.isArray(parts) ? parts : [parts]).map(p => (p || "").trim()).filter(Boolean).forEach(p => s.add(p));
    return s.end();
  }
  return { name: "server", available: () => true, say, sayStream, cancel, voiceStatus: () => "ok" };
}

// Umschalter: aktive Server-Stimme -> Server; "browser" -> Browser-Stimme
// (schnelle Notloesung, letzte Option in der Auswahl); leer -> stumm.
// Faellt die Server-Stimme waehrend des Vorlesens aus, uebernimmt der
// Browser den Satz (letzte Option, v19.39).
function createSwitchingProvider(browser, server) {
  const pick = () => (_active === "browser" ? browser : (_serverActive() && server ? server : null));
  const none = createNullProvider();
  return {
    name: "switch",
    available: () => browser.available() || !!server,
    say: (p) => (pick() || none).say(p),
    sayStream: () => (pick() || none).sayStream(),
    cancel: () => { browser.cancel(); if (server) server.cancel(); },
    voiceStatus: () => (_active === "browser" ? browser.voiceStatus() : (_serverActive() ? "ok" : "none")),
  };
}

let _provider = null;
function getSpeechProvider() {
  if (_provider) return _provider;
  const browser = browserAvailable() ? createBrowserProvider() : createNullProvider();
  _provider = createSwitchingProvider(browser, createServerProvider({ fallback: browser }));
  return _provider;
}

// Fuer Tests: Provider ersetzen.
function _setSpeechProvider(p) { _provider = p; }

// Fuer Tests: Zeitpunkt des letzten gesprochenen Satzes setzen.
function _setLastSpokeAt(t) { _lastSpokeAt = t; }

export { BROWSER_ENGINE, getSpeechProvider, _setSpeechProvider, createServerProvider, createSwitchingProvider, createBrowserProvider, getTtsEngine, getStoredTtsEngine, setTtsEngine, setActiveTtsEngine, LS_TTS_ENGINE, TTS_FALLBACK_EVENT, pickGermanVoice, localGermanVoices, isLocalVoice, voiceStatus, wakeAudio, _setLastSpokeAt, browserAvailable, splitSentences, PART_GAP_MS, LEAD_GAP_MS, WAKE_SILENCE_MS };
