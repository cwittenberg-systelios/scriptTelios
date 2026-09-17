// ────────────────────────────────────────────────────────────────────────────
// src/interview.jsx — Interview-Modus der Gespraechsdokumentation (v19.23).
//
// Dialog nach der Sitzung: Frage -> Antwort (Diktat oder Text) -> ggf. EINE
// Rueckfrage (Backend, D1=C) -> Weiter -> naechste Frage. Am Ende entsteht
// ein Protokoll, das P1 als `interviewProtokoll` an /jobs/generate schickt.
//
// Zustand lebt komplett im Draft von P1 (D4=A) und ueberlebt Reload ueber
// den Draft-Cache: `value` ist ein reines JSON-Objekt, `onChange(patch)`
// merged. Kein serverseitiger Session-State.
//
// Vorlesen (D2 = beides): Browser-TTS (speechSynthesis). Kein Server-TTS -
// die Fragen enthalten keine Klientendaten und muessen auch bei
// ausgeschaltetem Pod vorlesbar sein.
// ────────────────────────────────────────────────────────────────────────────
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchInterviewSets, interviewTranscribe, interviewTurn } from "./api.js";
import { friendlyError } from "./shared.js";

const LS_SET_KEY = "st_interview_set";       // gemerktes Set je Nutzer (E3)
const LS_VORLESEN = "st_interview_vorlesen"; // Schalter "Vorlesen"

// ── Leerer Interview-Zustand (Default fuer den Draft-Cache) ────────────────
const INTERVIEW_DEFAULT = {
  setKey: "",
  setLabel: "",
  fragen: [],      // editierbare Kopie der Server-Defaults: {key,text,ziel_abschnitt,pflicht,pflichtaspekte,hinweis}
  eintraege: [],   // {key, frage, antwort, rueckfrage, rueckfrage_antwort, ziel_abschnitt}
  idx: 0,          // aktuelle Frage
  phase: "start",  // start | antwort | rueckfrage | fertig
};

function emptyInterview() { return { ...INTERVIEW_DEFAULT, fragen: [], eintraege: [] }; }

// Protokoll fuer /jobs/generate. null solange das Interview nicht fertig ist.
function buildInterviewProtokoll(v) {
  if (!v || v.phase !== "fertig" || !v.eintraege?.length) return null;
  return {
    set: v.setKey,
    set_label: v.setLabel || v.setKey,
    eintraege: v.eintraege.map(e => ({
      key: e.key, frage: e.frage, antwort: e.antwort || "",
      rueckfrage: e.rueckfrage || "", rueckfrage_antwort: e.rueckfrage_antwort || "",
      ziel_abschnitt: e.ziel_abschnitt || undefined,
    })),
  };
}

function interviewHasContent(v) {
  return !!(v && v.phase && v.phase !== "start" && v.eintraege?.some(e => (e.antwort || e.rueckfrage_antwort || "").trim()));
}

// Eintraege aus der (ggf. editierten) Fragenliste aufbauen.
function eintraegeFromFragen(fragen) {
  return fragen.map(f => ({
    key: f.key, frage: f.text, antwort: "", rueckfrage: "", rueckfrage_antwort: "",
    ziel_abschnitt: f.ziel_abschnitt,
  }));
}

// ── Browser-TTS ─────────────────────────────────────────────────────────────
function pickGermanVoice() {
  try {
    const voices = window.speechSynthesis?.getVoices?.() || [];
    return voices.find(v => /^de/i.test(v.lang) && /google|microsoft|premium|enhanced/i.test(v.name))
        || voices.find(v => /^de/i.test(v.lang)) || null;
  } catch { return null; }
}

function speak(text) {
  try {
    if (!("speechSynthesis" in window) || !text) return false;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "de-DE";
    u.rate = 1.0;
    const v = pickGermanVoice();
    if (v) u.voice = v;
    window.speechSynthesis.speak(u);
    return true;
  } catch { return false; }
}

function stopSpeaking() {
  try { window.speechSynthesis?.cancel(); } catch { /* ignoriert */ }
}

// ── Push-to-talk (ein Clip je Antwort, ohne Pegel-Meter/Heartbeat) ─────────
function useDictation({ onText, onError }) {
  const [state, setState] = useState("idle"); // idle | recording | transcribing
  const recRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);
  const [seconds, setSeconds] = useState(0);
  const timerRef = useRef(null);

  const stopTracks = () => {
    if (streamRef.current) { streamRef.current.getTracks().forEach(t => t.stop()); streamRef.current = null; }
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
  };

  async function start() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      onError?.("Mikrofon-Aufnahme wird von diesem Browser nicht unterstützt (HTTPS nötig). Du kannst die Antwort auch tippen.");
      return;
    }
    try {
      stopSpeaking();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      streamRef.current = stream;
      const mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm"]
        .find(m => MediaRecorder.isTypeSupported(m)) || "";
      const rec = new MediaRecorder(stream, { mimeType: mime || undefined, audioBitsPerSecond: 24000 });
      chunksRef.current = [];
      rec.ondataavailable = (e) => { if (e.data && e.data.size > 0) chunksRef.current.push(e.data); };
      rec.onstop = async () => {
        stopTracks();
        const blob = new Blob(chunksRef.current, { type: mime || "audio/webm" });
        chunksRef.current = [];
        if (blob.size < 200) { setState("idle"); return; }
        const ext = mime.includes("ogg") ? "ogg" : "webm";
        const file = new File([blob], `antwort.${ext}`, { type: blob.type });
        setState("transcribing");
        try {
          const d = await interviewTranscribe(file);
          onText?.((d.transcript || "").trim());
        } catch (e) {
          onError?.("Transkription fehlgeschlagen: " + friendlyError(e));
        } finally {
          setState("idle");
        }
      };
      rec.start(1000);
      recRef.current = rec;
      setSeconds(0);
      timerRef.current = setInterval(() => setSeconds(s => s + 1), 1000);
      setState("recording");
    } catch (err) {
      stopTracks();
      onError?.(err?.name === "NotAllowedError"
        ? "Mikrofon-Zugriff verweigert. Bitte in den Browser-Einstellungen erlauben."
        : `Aufnahme fehlgeschlagen: ${err?.message || err}`);
    }
  }

  function stop() {
    const rec = recRef.current;
    if (rec && rec.state !== "inactive") { try { rec.stop(); } catch { /* ignoriert */ } }
    recRef.current = null;
  }

  useEffect(() => () => { stopTracks(); }, []);

  return { state, seconds, start, stop };
}

// ── Komponente ──────────────────────────────────────────────────────────────
function InterviewDialog({ value, onChange, toast, model }) {
  const v = value && value.fragen ? value : emptyInterview();
  const [manifest, setManifest] = useState(null);
  const [loadErr, setLoadErr] = useState(null);
  const [busy, setBusy] = useState(null);        // null | "turn"
  const [editFragen, setEditFragen] = useState(false);
  const [vorlesen, setVorlesen] = useState(() => {
    try { return localStorage.getItem(LS_VORLESEN) !== "0"; } catch { return true; }
  });
  const [draftText, setDraftText] = useState("");   // Textfeld der aktuellen Antwort
  const lastSpokenRef = useRef("");

  const patch = useCallback((p) => onChange({ ...v, ...p }), [onChange, v]);

  // Manifest einmal laden
  useEffect(() => {
    let alive = true;
    fetchInterviewSets().then(m => { if (alive) setManifest(m); })
      .catch(e => { if (alive) setLoadErr(friendlyError(e)); });
    return () => { alive = false; };
  }, []);

  // Set initialisieren: gemerktes Set oder Server-Default
  useEffect(() => {
    if (!manifest || v.fragen.length) return;
    let key = "";
    try { key = localStorage.getItem(LS_SET_KEY) || ""; } catch { /* ignoriert */ }
    const set = manifest.sets.find(s => s.key === key) || manifest.sets.find(s => s.key === manifest.default_set) || manifest.sets[0];
    if (set) patch({ setKey: set.key, setLabel: set.label, fragen: set.fragen.map(f => ({ ...f })), eintraege: [], idx: 0, phase: "start" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manifest]);

  const current = v.eintraege[v.idx] || null;
  const frage = v.fragen[v.idx] || null;
  const total = v.eintraege.length;

  // Textfeld an Phase/Frage koppeln
  useEffect(() => {
    if (!current) { setDraftText(""); return; }
    setDraftText(v.phase === "rueckfrage" ? (current.rueckfrage_antwort || "") : (current.antwort || ""));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [v.idx, v.phase]);

  // Vorlesen der aktuellen Frage bzw. Rueckfrage
  const spokenText = !current ? "" : (v.phase === "rueckfrage" ? current.rueckfrage : (v.phase === "antwort" ? current.frage : ""));
  useEffect(() => {
    if (!vorlesen || !spokenText || spokenText === lastSpokenRef.current) return;
    lastSpokenRef.current = spokenText;
    speak(spokenText);
  }, [spokenText, vorlesen]);
  useEffect(() => () => stopSpeaking(), []);

  const dict = useDictation({
    onText: (t) => { if (t) setDraftText(prev => (prev.trim() ? prev.trim() + " " + t : t)); },
    onError: (m) => toast && toast(m),
  });

  function toggleVorlesen() {
    const next = !vorlesen;
    setVorlesen(next);
    try { localStorage.setItem(LS_VORLESEN, next ? "1" : "0"); } catch { /* ignoriert */ }
    if (!next) stopSpeaking();
  }

  function chooseSet(key) {
    const set = manifest?.sets.find(s => s.key === key);
    if (!set) return;
    try { localStorage.setItem(LS_SET_KEY, key); } catch { /* ignoriert */ }
    patch({ setKey: set.key, setLabel: set.label, fragen: set.fragen.map(f => ({ ...f })), eintraege: [], idx: 0, phase: "start" });
  }

  function resetFragen() {
    const set = manifest?.sets.find(s => s.key === v.setKey);
    if (set) patch({ fragen: set.fragen.map(f => ({ ...f })) });
  }

  function startInterview() {
    stopSpeaking();
    lastSpokenRef.current = "";
    patch({ eintraege: eintraegeFromFragen(v.fragen), idx: 0, phase: "antwort" });
  }

  function commitEintrag(i, p) {
    const eintraege = v.eintraege.map((e, k) => (k === i ? { ...e, ...p } : e));
    return eintraege;
  }

  function advance(eintraege) {
    stopSpeaking();
    if (v.idx + 1 >= eintraege.length) patch({ eintraege, phase: "fertig" });
    else patch({ eintraege, idx: v.idx + 1, phase: "antwort" });
  }

  async function weiter() {
    if (!current || !frage) return;
    if (dict.state === "recording") { dict.stop(); return; }
    const text = draftText.trim();
    if (v.phase === "rueckfrage") {
      advance(commitEintrag(v.idx, { rueckfrage_antwort: text }));
      return;
    }
    // Phase "antwort": Rueckfrage-Check (max. eine je Frage)
    if (current.rueckfrage) {           // kam schon mal vor (Zurueck-Navigation)
      advance(commitEintrag(v.idx, { antwort: text }));
      return;
    }
    if (!text && frage.pflicht) {
      toast && toast("Diese Frage ist eine Pflichtfrage – bitte kurz beantworten.");
      return;
    }
    setBusy("turn");
    let rueckfrage = null;
    try {
      const d = await interviewTurn({
        set: v.setKey, frage_key: frage.key, frage_text: current.frage,
        antwort: text, pflicht: !!frage.pflicht, pflichtaspekte: frage.pflichtaspekte || [],
        rueckfrage_bereits: false,
        bisherige: v.eintraege.slice(0, v.idx).map(e => ({ frage: e.frage, antwort: e.antwort })),
        model: model || null,
      });
      rueckfrage = d.rueckfrage || null;
    } catch (e) {
      // Rueckfrage-Check darf den Dialog nie blockieren.
      toast && toast("Rückfrage-Prüfung nicht möglich (" + friendlyError(e) + ") – weiter ohne Rückfrage.");
    } finally {
      setBusy(null);
    }
    if (rueckfrage) {
      patch({ eintraege: commitEintrag(v.idx, { antwort: text, rueckfrage }), phase: "rueckfrage" });
    } else {
      advance(commitEintrag(v.idx, { antwort: text }));
    }
  }

  function ueberspringen() {
    if (!frage || frage.pflicht) return;
    advance(commitEintrag(v.idx, v.phase === "rueckfrage" ? { rueckfrage_antwort: "" } : { antwort: "" }));
  }

  function zurueck() {
    if (v.idx === 0 && v.phase === "antwort") return;
    stopSpeaking();
    const text = draftText.trim();
    const eintraege = commitEintrag(v.idx, v.phase === "rueckfrage" ? { rueckfrage_antwort: text } : { antwort: text });
    if (v.phase === "rueckfrage") patch({ eintraege, phase: "antwort" });
    else patch({ eintraege, idx: v.idx - 1, phase: "antwort" });
  }

  function bearbeiten(i) {
    stopSpeaking();
    patch({ idx: i, phase: "antwort" });
  }

  function neuStarten() {
    if (interviewHasContent(v) && !confirm("Interview verwerfen und neu beginnen?")) return;
    stopSpeaking();
    lastSpokenRef.current = "";
    patch({ eintraege: [], idx: 0, phase: "start" });
  }

  // ── Render ───────────────────────────────────────────────────────────────
  const soft = { fontSize: 11, color: "var(--st-text-soft)" };
  const btnRow = { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 10 };

  if (loadErr) return <div className="info-note">Fragen-Sets konnten nicht geladen werden: {loadErr}</div>;
  if (!manifest || !v.fragen.length) return <div style={{ ...soft, padding: "8px 0" }}>Lade Fragen-Sets …</div>;

  const header = (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
      <span style={{ ...soft, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em" }}>Verfahren</span>
      <select value={v.setKey} onChange={e => chooseSet(e.target.value)} disabled={v.phase !== "start"}
        style={{ fontSize: 12, padding: "3px 6px", borderRadius: 3, border: "1px solid var(--st-gray-border)", background: "var(--st-bg)", color: "var(--st-text)" }}
        data-testid="interview-set">
        {manifest.sets.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
      </select>
      <label style={{ ...soft, display: "flex", alignItems: "center", gap: 4, marginLeft: "auto", cursor: "pointer" }}>
        <input type="checkbox" checked={vorlesen} onChange={toggleVorlesen} /> Fragen vorlesen
      </label>
      {v.phase === "start" && (
        <button className="btn-xs" type="button" onClick={() => setEditFragen(e => !e)}>
          {editFragen ? "Fragen schließen" : "Fragen anpassen"}
        </button>
      )}
    </div>
  );

  if (v.phase === "start") {
    return (
      <div data-testid="interview-start">
        {header}
        {editFragen ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {v.fragen.map((f, i) => (
              <div key={f.key}>
                <div style={soft}>Frage {i + 1} · Zielabschnitt: {manifest.abschnitte[f.ziel_abschnitt] || f.ziel_abschnitt}{f.pflicht ? " · Pflicht" : ""}</div>
                <textarea rows={2} value={f.text} disabled={!!f.pflicht}
                  onChange={e => patch({ fragen: v.fragen.map((x, k) => (k === i ? { ...x, text: e.target.value } : x)) })}
                  style={{ marginTop: 2 }} />
              </div>
            ))}
            <div style={btnRow}>
              <button className="btn-xs" type="button" onClick={resetFragen}>Auf Standard zurücksetzen</button>
              <span style={soft}>Die Pflichtfrage zur Selbstgefährdung ist nicht änderbar.</span>
            </div>
          </div>
        ) : (
          <ol style={{ margin: "0 0 0 18px", padding: 0, fontSize: 13, color: "var(--st-text)" }}>
            {v.fragen.map(f => <li key={f.key} style={{ marginBottom: 4 }}>{f.text}</li>)}
          </ol>
        )}
        <div style={btnRow}>
          <button className="btn-primary" type="button" onClick={startInterview}>Interview starten</button>
          <span style={soft}>{v.fragen.length} Fragen · Antworten per Mikrofon oder Tastatur</span>
        </div>
      </div>
    );
  }

  if (v.phase === "fertig") {
    return (
      <div data-testid="interview-fertig">
        {header}
        <div className="info-note" style={{ marginBottom: 8 }}>Interview abgeschlossen – die Antworten sind die Quelle für die Dokumentation. Zum Ändern eine Frage anklicken.</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {v.eintraege.map((e, i) => (
            <div key={e.key} onClick={() => bearbeiten(i)} style={{ cursor: "pointer", padding: "6px 8px", border: "1px solid var(--st-gray-border)", borderRadius: 4, background: "var(--st-bg)" }}>
              <div style={{ ...soft, fontWeight: 600 }}>{i + 1}. {e.frage}</div>
              <div style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>{(e.antwort || "").trim() || <em style={soft}>nicht erhoben</em>}</div>
              {e.rueckfrage && (
                <div style={{ marginTop: 4 }}>
                  <div style={{ ...soft, fontStyle: "italic" }}>Rückfrage: {e.rueckfrage}</div>
                  <div style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>{(e.rueckfrage_antwort || "").trim() || <em style={soft}>nicht erhoben</em>}</div>
                </div>
              )}
            </div>
          ))}
        </div>
        <div style={btnRow}>
          <button className="btn-secondary" type="button" onClick={neuStarten}>Neu beginnen</button>
        </div>
      </div>
    );
  }

  // Phase antwort | rueckfrage
  const istRueckfrage = v.phase === "rueckfrage";
  const recording = dict.state === "recording";
  const transcribing = dict.state === "transcribing";
  return (
    <div data-testid="interview-dialog">
      {header}
      <div style={{ ...soft, marginBottom: 4 }}>Frage {v.idx + 1} von {total}{frage?.pflicht ? " · Pflichtfrage" : ""}</div>
      <div style={{ fontSize: 15, lineHeight: 1.4, fontWeight: 600, color: "var(--st-text)" }} data-testid="interview-frage">
        {istRueckfrage ? current.rueckfrage : current.frage}
        <button type="button" className="btn-xs" title="Nochmal vorlesen" onClick={() => speak(istRueckfrage ? current.rueckfrage : current.frage)} style={{ marginLeft: 8 }}>🔊</button>
      </div>
      {istRueckfrage && <div style={{ ...soft, marginTop: 2 }}>Rückfrage zu: {current.frage}</div>}
      {!istRueckfrage && frage?.hinweis && <div style={{ ...soft, marginTop: 2 }}>{frage.hinweis}</div>}

      <textarea rows={5} value={draftText} onChange={e => setDraftText(e.target.value)}
        placeholder={recording ? "Aufnahme läuft …" : "Antwort einsprechen (Mikrofon) oder hier tippen …"}
        disabled={transcribing} style={{ marginTop: 8 }} data-testid="interview-antwort" />

      <div style={btnRow}>
        {!recording
          ? <button type="button" className="rec-btn rec-btn-start" onClick={dict.start} disabled={transcribing || busy !== null}>🎙 Aufnehmen</button>
          : <button type="button" className="rec-btn rec-btn-stop" onClick={dict.stop}>■ Stopp ({dict.seconds}s)</button>}
        {transcribing && <span style={soft}>Transkribiere …</span>}
        {busy === "turn" && <span style={soft}>Prüfe Antwort …</span>}
        <span style={{ marginLeft: "auto" }} />
        <button type="button" className="btn-secondary" onClick={zurueck} disabled={(v.idx === 0 && !istRueckfrage) || busy !== null || recording}>Zurück</button>
        {!frage?.pflicht && <button type="button" className="btn-secondary" onClick={ueberspringen} disabled={busy !== null || recording || transcribing}>Überspringen</button>}
        <button type="button" className="btn-primary" onClick={weiter} disabled={busy !== null || transcribing} data-testid="interview-weiter">
          {recording ? "Stopp" : (v.idx + 1 >= total ? "Abschließen" : "Weiter")}
        </button>
      </div>
    </div>
  );
}

export { InterviewDialog, INTERVIEW_DEFAULT, emptyInterview, buildInterviewProtokoll, interviewHasContent, eintraegeFromFragen, speak, pickGermanVoice };
