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
  return { name: "browser", available: browserAvailable, say, cancel };
}

function createNullProvider() {
  return { name: "none", available: () => false, say: async () => false, cancel: () => {} };
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

export { getSpeechProvider, _setSpeechProvider, pickGermanVoice, browserAvailable, PART_GAP_MS };
