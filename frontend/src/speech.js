// ────────────────────────────────────────────────────────────────────────────
// src/speech.js — Sprachausgabe des Interview-Dialogs (v19.24, B1).
//
// Ein Provider mit Warteschlange: say([teil1, teil2]) spricht die Teile
// nacheinander (Quittung -> Rueckfrage bzw. Ueberleitung -> naechste Frage),
// cancel() bricht alles ab (z.B. beim Start einer Aufnahme).
//
// Provider "browser" = speechSynthesis (kein Server, keine Klientendaten,
// funktioniert bei ausgeschaltetem Pod). Ein spaeterer Provider "server"
// ersetzt nur diese Datei: gleiche Schnittstelle, Audio vom Backend.
// Auswahl ueber window.SYSTELIOS_TTS ("browser" | "server"), Default browser.
// ────────────────────────────────────────────────────────────────────────────

function pickGermanVoice() {
  try {
    const voices = window.speechSynthesis?.getVoices?.() || [];
    return voices.find(v => /^de/i.test(v.lang) && /google|microsoft|premium|enhanced|siri/i.test(v.name))
        || voices.find(v => /^de/i.test(v.lang)) || null;
  } catch { return null; }
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
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "de-DE";
      u.rate = 1.0;
      const v = pickGermanVoice();
      if (v) u.voice = v;
      u.onend = () => resolve(true);
      u.onerror = () => resolve(false);
      try { window.speechSynthesis.speak(u); } catch { resolve(false); }
    });
  }
  async function say(parts) {
    cancel();
    const myToken = token;
    const list = (Array.isArray(parts) ? parts : [parts]).map(p => (p || "").trim()).filter(Boolean);
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
    let resolveDone = null;
    const done = new Promise(r => { resolveDone = r; });
    async function pump() {
      if (running) return;
      running = true;
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
  return { name: "browser", available: browserAvailable, say, sayStream, cancel };
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
  return { name: "none", available: () => false, say: async () => false, sayStream: noop, cancel: () => {} };
}

let _provider = null;
function getSpeechProvider() {
  if (_provider) return _provider;
  const wanted = (typeof window !== "undefined" && window.SYSTELIOS_TTS) || "browser";
  // "server" ist vorbereitet, aber nicht implementiert - faellt auf browser zurueck.
  _provider = (wanted === "browser" || wanted === "server") && browserAvailable()
    ? createBrowserProvider()
    : createNullProvider();
  return _provider;
}

// Fuer Tests: Provider ersetzen.
function _setSpeechProvider(p) { _provider = p; }

export { getSpeechProvider, _setSpeechProvider, pickGermanVoice, browserAvailable, splitSentences, PART_GAP_MS };
