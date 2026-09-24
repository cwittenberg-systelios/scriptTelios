// ────────────────────────────────────────────────────────────────────────────
// src/interview.jsx — Interview-Modus der Gespraechsdokumentation.
//
// v19.23: Dialog nach der Sitzung: Frage -> Antwort (Diktat oder Text) ->
//         ggf. EINE Rueckfrage (Backend, D1=C) -> Weiter. Am Ende entsteht
//         ein Protokoll, das P1 als `interviewProtokoll` an /jobs/generate
//         schickt.
// v19.24: Gespraechsfuehrung (Quittung/Ueberleitung, speech.js),
//         Klient-Frage als erste Frage (fuellt Kuerzel/Geschlecht),
//         Suizidalitaets-Trigger-Kette (Nachfragen-Liste je Eintrag),
//         Abschluss-Check (bis zu drei Fragen, "So lassen"), Feedback.
//
// Zustand lebt komplett im Draft von P1 (D4=A) und ueberlebt Reload ueber
// den Draft-Cache: `value` ist ein reines JSON-Objekt, `onChange(patch)`
// merged. Kein serverseitiger Session-State; `sessionId` buendelt nur die
// Prompt-Log-Eintraege und die Feedback-Fallkopie.
// ────────────────────────────────────────────────────────────────────────────
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchInterviewSets, interviewAbschluss, interviewLease, interviewTranscribe, interviewTurn, warmupInterviewServer } from "./api.js";
import { useInterviewLease } from "./interview-lease.js";
import { friendlyError } from "./shared.js";
import { getSpeechProvider } from "./speech.js";
import { TtsSelect } from "./tts-select.jsx";
import { FeedbackButton } from "./ui.jsx";

const LS_SET_KEY = "st_interview_set";       // gemerktes Set je Nutzer (E3)
const LS_VORLESEN = "st_interview_vorlesen"; // Schalter "Vorlesen"
const KLIENT_KEY = "klient";

// ── Leerer Interview-Zustand (Default fuer den Draft-Cache) ────────────────
const INTERVIEW_DEFAULT = {
  sessionId: "",
  setKey: "",
  setLabel: "",
  fragen: [],      // editierbare Kopie der Server-Defaults
  eintraege: [],   // {key, frage, antwort, nachfragen:[{typ,frage,antwort}], ziel_abschnitt, trigger_stufe}
  idx: 0,          // aktuelle Frage
  phase: "start",  // start | antwort | nachfrage | abschluss | fertig
  abschluss: [],   // {typ, bezug, frage, antwort, belassen}
  abschlussIdx: 0,
  klient: null,    // {anrede, initial, gender}
  lastPhrase: "",
};

function newSessionId() {
  return `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

function emptyInterview() { return { ...INTERVIEW_DEFAULT, fragen: [], eintraege: [], abschluss: [] }; }

// Protokoll fuer /jobs/generate und /interview/abschluss.
function buildInterviewProtokoll(v, { requireFertig = true } = {}) {
  if (!v || !v.eintraege?.length) return null;
  if (requireFertig && v.phase !== "fertig") return null;
  return {
    set: v.setKey,
    set_label: v.setLabel || v.setKey,
    session_id: v.sessionId || "",
    eintraege: v.eintraege.map(e => ({
      key: e.key, frage: e.frage, antwort: e.antwort || "",
      nachfragen: (e.nachfragen || []).map(n => ({ typ: n.typ || "aspekt", frage: n.frage, antwort: n.antwort || "" })),
      ziel_abschnitt: e.ziel_abschnitt || undefined,
    })),
    abschluss: (v.abschluss || []).map(a => ({
      typ: a.typ, bezug: a.bezug || [], frage: a.frage, antwort: a.antwort || "", belassen: !!a.belassen,
    })),
  };
}

function interviewHasContent(v) {
  return !!(v && v.phase && v.phase !== "start" && v.eintraege?.some(e =>
    (e.antwort || "").trim() || (e.nachfragen || []).some(n => (n.antwort || "").trim())));
}

function eintraegeFromFragen(fragen) {
  return fragen.map(f => ({
    key: f.key, frage: f.text, antwort: "", nachfragen: [], ziel_abschnitt: f.ziel_abschnitt, trigger_stufe: 0,
  }));
}

function anredeOf(klient) {
  return klient && klient.anrede && klient.initial ? `${klient.anrede} ${klient.initial}` : null;
}

// ── Push-to-talk (ein Clip je Antwort) ─────────────────────────────────────
function useDictation({ onText, onError, onStart, sessionId }) {
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
      onStart?.();
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
          const d = await interviewTranscribe(file, sessionId);
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
// onKlient({anrede, initial, gender}) - P1 fuellt Kuerzel/Geschlecht (B2).
function InterviewDialog({ value, onChange, toast, model, onKlient }) {
  const v = value && value.fragen ? value : emptyInterview();
  const [manifest, setManifest] = useState(null);
  const [loadErr, setLoadErr] = useState(null);
  const [busy, setBusy] = useState(null);        // null | "turn" | "abschluss"
  const [editFragen, setEditFragen] = useState(false);
  const [vorlesen, setVorlesen] = useState(() => {
    try { return localStorage.getItem(LS_VORLESEN) !== "0"; } catch { return true; }
  });
  const [draftText, setDraftText] = useState("");
  // v19.24.1: Server-Status fuer die Statuszeile: null (unbekannt) | "ok" |
  // "starting" | "no_proxy" | "no_server" | "blocked_night" | "error"
  const [serverState, setServerState] = useState(null);
  const speech = getSpeechProvider();

  const patch = useCallback((p) => onChange({ ...v, ...p }), [onChange, v]);

  // v19.24.1: Manifest laden (Bundle-Fallback, wenn der Server aus ist) und
  // parallel den Server still anstossen. Sobald er laeuft (st-health-ok),
  // wird das Manifest vom Server nachgeladen - editierte Fragen im Draft
  // bleiben davon unberuehrt (v.fragen ist eine Kopie).
  useEffect(() => {
    let alive = true;
    const load = () => fetchInterviewSets()
      .then(m => { if (alive) setManifest(m); })
      .catch(e => { if (alive) setLoadErr(friendlyError(e)); });
    load();
    warmupInterviewServer().then(st => { if (alive) setServerState(st?.status || "error"); })
      .catch(() => { if (alive) setServerState("error"); });
    const onOk = () => { if (!alive) return; setServerState("ok"); load(); };
    window.addEventListener("st-health-ok", onOk);
    return () => { alive = false; window.removeEventListener("st-health-ok", onOk); };
  }, []);

  // Set initialisieren: gemerktes Set oder Server-Default
  useEffect(() => {
    if (!manifest || v.fragen.length) return;
    let key = "";
    try { key = localStorage.getItem(LS_SET_KEY) || ""; } catch { /* ignoriert */ }
    const set = manifest.sets.find(s => s.key === key) || manifest.sets.find(s => s.key === manifest.default_set) || manifest.sets[0];
    if (set) patch({ setKey: set.key, setLabel: set.label, fragen: set.fragen.map(f => ({ ...f })), eintraege: [], idx: 0, phase: "start", abschluss: [], klient: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manifest]);

  const current = v.eintraege[v.idx] || null;
  const frage = v.fragen[v.idx] || null;
  const total = v.eintraege.length;
  const lastNachfrage = current && current.nachfragen?.length ? current.nachfragen[current.nachfragen.length - 1] : null;
  const abschlussPunkt = v.phase === "abschluss" ? (v.abschluss[v.abschlussIdx] || null) : null;

  // Textfeld an Phase/Frage koppeln
  useEffect(() => {
    if (v.phase === "nachfrage") setDraftText(lastNachfrage?.antwort || "");
    else if (v.phase === "abschluss") setDraftText(abschlussPunkt?.antwort || "");
    else setDraftText(current?.antwort || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [v.idx, v.phase, v.abschlussIdx, current?.nachfragen?.length]);

  useEffect(() => () => speech.cancel(), [speech]);

  // Sprechen einer Sequenz (Quittung -> Rueckfrage, Ueberleitung -> Frage)
  const say = useCallback((parts) => {
    if (!vorlesen) return;
    speech.say(parts);
  }, [vorlesen, speech]);

  const dict = useDictation({
    onText: (t) => { if (t) setDraftText(prev => (prev.trim() ? prev.trim() + " " + t : t)); },
    onError: (m) => toast && toast(m),
    onStart: () => { speech.cancel(); interviewLease(v.sessionId, "touch"); },
    sessionId: v.sessionId,
  });
  // v19.34: Reservierung freigeben, sobald das Interview nicht mehr laeuft
  useInterviewLease(v.sessionId, ["antwort", "nachfrage", "abschluss"].includes(v.phase));

  function toggleVorlesen() {
    const next = !vorlesen;
    setVorlesen(next);
    try { localStorage.setItem(LS_VORLESEN, next ? "1" : "0"); } catch { /* ignoriert */ }
    if (!next) speech.cancel();
  }

  function chooseSet(key) {
    const set = manifest?.sets.find(s => s.key === key);
    if (!set) return;
    try { localStorage.setItem(LS_SET_KEY, key); } catch { /* ignoriert */ }
    patch({ setKey: set.key, setLabel: set.label, fragen: set.fragen.map(f => ({ ...f })), eintraege: [], idx: 0, phase: "start", abschluss: [], klient: null });
  }

  function resetFragen() {
    const set = manifest?.sets.find(s => s.key === v.setKey);
    if (set) patch({ fragen: set.fragen.map(f => ({ ...f })) });
  }

  function startInterview() {
    speech.cancel();
    const eintraege = eintraegeFromFragen(v.fragen);
    patch({ sessionId: newSessionId(), eintraege, idx: 0, phase: "antwort", abschluss: [], abschlussIdx: 0, klient: null, lastPhrase: "" });
    say([eintraege[0]?.frage]);
  }

  function withEintrag(eintraege, i, p) {
    return eintraege.map((e, k) => (k === i ? { ...e, ...p } : e));
  }

  // Weiter zur naechsten Frage bzw. in den Abschluss-Check.
  async function advance(eintraege, extra = {}, ueberleitung = null) {
    speech.cancel();
    const next = v.idx + 1;
    if (next < eintraege.length) {
      patch({ eintraege, idx: next, phase: "antwort", lastPhrase: ueberleitung || v.lastPhrase, ...extra });
      say([ueberleitung, eintraege[next].frage]);
      return;
    }
    // Abschluss-Check (C3: automatisch, ueberspringbar)
    patch({ eintraege, ...extra });
    setBusy("abschluss");
    let punkte = [];
    try {
      const protokoll = buildInterviewProtokoll({ ...v, eintraege, ...extra, phase: "fertig" });
      const d = await interviewAbschluss({ protokoll, model: model || null, session_id: v.sessionId || null });
      punkte = Array.isArray(d.punkte) ? d.punkte : [];
    } catch (e) {
      toast && toast("Abschluss-Prüfung nicht möglich (" + friendlyError(e) + ") – Interview abgeschlossen.");
    } finally {
      setBusy(null);
    }
    if (!punkte.length) {
      patch({ eintraege, ...extra, phase: "fertig", abschluss: [] });
      say(["Danke, das war die letzte Frage."]);
      return;
    }
    const abschluss = punkte.map(p => ({ ...p, antwort: "", belassen: false }));
    patch({ eintraege, ...extra, phase: "abschluss", abschluss, abschlussIdx: 0 });
    say([ueberleitung, "Ich habe noch " + (abschluss.length === 1 ? "eine Frage" : abschluss.length + " Fragen") + " zum Ganzen.", abschluss[0].frage]);
  }

  function pushNachfrage(eintraege, i, res, antwort) {
    const nf = { typ: res.rueckfrage_typ || "aspekt", frage: res.rueckfrage, antwort: "" };
    const e = eintraege[i];
    return withEintrag(eintraege, i, {
      antwort: antwort !== undefined ? antwort : e.antwort,
      nachfragen: [...(e.nachfragen || []), nf],
      trigger_stufe: res.trigger_stufe || 0,
    });
  }

  async function callTurn(req) {
    setBusy("turn");
    try {
      return await interviewTurn({
        set: v.setKey, model: model || null, session_id: v.sessionId || null,
        anrede: anredeOf(v.klient), frage_index: v.idx, vorherige_phrase: v.lastPhrase || null,
        bisherige: v.eintraege.slice(0, v.idx).filter(e => e.key !== KLIENT_KEY).map(e => ({ frage: e.frage, antwort: e.antwort })),
        ...req,
      });
    } catch (e) {
      toast && toast("Rückfrage-Prüfung nicht möglich (" + friendlyError(e) + ") – weiter ohne Rückfrage.");
      return null;
    } finally {
      setBusy(null);
    }
  }

  function applyKlient(res) {
    if (res?.klient) {
      onKlient && onKlient(res.klient);
      return { klient: res.klient };
    }
    return {};
  }

  async function weiter() {
    if (!current || !frage) return;
    if (dict.state === "recording") { dict.stop(); return; }
    const text = draftText.trim();

    // ── Phase "nachfrage": Antwort auf Rueckfrage/Nachfrage ─────────────
    if (v.phase === "nachfrage" && lastNachfrage) {
      const ni = current.nachfragen.length - 1;
      let eintraege = withEintrag(v.eintraege, v.idx, {
        nachfragen: current.nachfragen.map((n, k) => (k === ni ? { ...n, antwort: text } : n)),
      });
      const typ = lastNachfrage.typ || "aspekt";
      if (typ.startsWith("trigger")) {
        const res = await callTurn({ frage_key: frage.key, frage_text: current.frage, antwort: text,
          pflicht: !!frage.pflicht, pflichtaspekte: [], trigger_stufe: current.trigger_stufe || 1, rueckfrage_bereits: true });
        if (res?.rueckfrage) {
          eintraege = pushNachfrage(eintraege, v.idx, res);
          patch({ eintraege, phase: "nachfrage", lastPhrase: res.quittung || v.lastPhrase });
          say([res.quittung, res.rueckfrage]);
          return;
        }
        await advance(eintraege, {}, res?.ueberleitung || null);
        return;
      }
      if (typ === "klient") {
        const res = await callTurn({ frage_key: KLIENT_KEY, frage_text: current.frage, antwort: text,
          pflicht: true, pflichtaspekte: [], rueckfrage_bereits: true });
        await advance(eintraege, applyKlient(res), res?.ueberleitung || null);   // D4=B: sonst bleibt das Feld Pflicht
        return;
      }
      await advance(eintraege);   // aspekt/pflicht: kein zweiter Check
      return;
    }

    // ── Phase "antwort" ─────────────────────────────────────────────────
    const hatSchonAspekt = (current.nachfragen || []).some(n => n.typ === "aspekt" || n.typ === "pflicht");
    if (!text && frage.pflicht && frage.key !== KLIENT_KEY) {
      toast && toast("Diese Frage ist eine Pflichtfrage – bitte kurz beantworten.");
      return;
    }
    const res = await callTurn({ frage_key: frage.key, frage_text: current.frage, antwort: text,
      pflicht: !!frage.pflicht, pflichtaspekte: frage.pflichtaspekte || [],
      trigger_stufe: 0, rueckfrage_bereits: hatSchonAspekt });
    let eintraege = withEintrag(v.eintraege, v.idx, { antwort: text });
    if (res?.rueckfrage) {
      eintraege = pushNachfrage(eintraege, v.idx, res, text);
      patch({ eintraege, phase: "nachfrage", lastPhrase: res.quittung || v.lastPhrase, ...applyKlient(res) });
      say([res.quittung, res.rueckfrage]);
      return;
    }
    await advance(eintraege, applyKlient(res), res?.ueberleitung || null);
  }

  function ueberspringen() {
    if (!frage || frage.pflicht) return;
    const eintraege = withEintrag(v.eintraege, v.idx, v.phase === "nachfrage" && lastNachfrage
      ? { nachfragen: current.nachfragen.map((n, k) => (k === current.nachfragen.length - 1 ? { ...n, antwort: "" } : n)) }
      : { antwort: "" });
    advance(eintraege);
  }

  function zurueck() {
    if (v.idx === 0 && v.phase === "antwort") return;
    speech.cancel();
    const text = draftText.trim();
    if (v.phase === "nachfrage" && lastNachfrage) {
      const ni = current.nachfragen.length - 1;
      const eintraege = withEintrag(v.eintraege, v.idx, {
        nachfragen: current.nachfragen.map((n, k) => (k === ni ? { ...n, antwort: text } : n)),
      });
      patch({ eintraege, phase: "antwort" });
      return;
    }
    patch({ eintraege: withEintrag(v.eintraege, v.idx, { antwort: text }), idx: v.idx - 1, phase: "antwort" });
  }

  // ── Abschluss-Check ─────────────────────────────────────────────────────
  function abschlussNext(abschluss, i) {
    speech.cancel();
    if (i + 1 < abschluss.length) {
      patch({ abschluss, abschlussIdx: i + 1 });
      say([abschluss[i + 1].frage]);
    } else {
      patch({ abschluss, phase: "fertig" });
      say(["Danke, das war alles."]);
    }
  }
  function abschlussAntworten() {
    if (dict.state === "recording") { dict.stop(); return; }
    const i = v.abschlussIdx;
    const abschluss = v.abschluss.map((a, k) => (k === i ? { ...a, antwort: draftText.trim(), belassen: !draftText.trim() } : a));
    abschlussNext(abschluss, i);
  }
  function abschlussBelassen() {
    const i = v.abschlussIdx;
    abschlussNext(v.abschluss.map((a, k) => (k === i ? { ...a, antwort: "", belassen: true } : a)), i);
  }
  function abschlussUeberspringen() {
    speech.cancel();
    patch({ abschluss: v.abschluss.map((a, k) => (k >= v.abschlussIdx && !(a.antwort || "").trim() ? { ...a, belassen: true } : a)), phase: "fertig" });
  }

  function bearbeiten(i) {
    speech.cancel();
    patch({ idx: i, phase: "antwort" });
  }

  function neuStarten() {
    if (interviewHasContent(v) && !confirm("Interview verwerfen und neu beginnen?")) return;
    speech.cancel();
    patch({ eintraege: [], idx: 0, phase: "start", abschluss: [], abschlussIdx: 0, klient: null, sessionId: "", lastPhrase: "" });
  }

  // ── Render ───────────────────────────────────────────────────────────────
  const soft = { fontSize: 11, color: "var(--st-text-soft)" };
  const btnRow = { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 10 };

  if (loadErr) return <div className="info-note">Fragen-Sets konnten nicht geladen werden: {loadErr}</div>;
  if (!manifest || !v.fragen.length) return <div style={{ ...soft, padding: "8px 0" }}>Lade Fragen-Sets …</div>;

  // v19.24.1: Statuszeile, solange der Server nicht laeuft. Antworten koennen
  // trotzdem getippt werden; Diktat und Weiter warten dann auf den Server.
  const serverStatusNote = (() => {
    if (serverState === "starting") return "Server startet – Antworten werden verarbeitet, sobald er läuft (3–6 min).";
    if (serverState === "no_server") return "Kein Server verfügbar – bitte später erneut versuchen.";
    if (serverState === "blocked_night") return "Zwischen 23 und 5 Uhr startet kein Server automatisch.";
    if (serverState === "error" && manifest?.source === "bundle") return "Server nicht erreichbar – Fragen aus dem lokalen Stand.";
    return null;
  })();

  const header = (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
      <span style={{ ...soft, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em" }}>Verfahren</span>
      <select value={v.setKey} onChange={e => chooseSet(e.target.value)} disabled={v.phase !== "start"}
        style={{ fontSize: 12, padding: "3px 6px", borderRadius: 3, border: "1px solid var(--st-gray-border)", background: "var(--st-bg)", color: "var(--st-text)" }}
        data-testid="interview-set">
        {manifest.sets.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
      </select>
      {anredeOf(v.klient) && <span style={{ ...soft, fontWeight: 600 }} data-testid="interview-klient">{anredeOf(v.klient)}</span>}
      {serverStatusNote && <span style={{ ...soft, color: "var(--st-red)" }} data-testid="interview-server">{serverStatusNote}</span>}
      <label style={{ ...soft, display: "flex", alignItems: "center", gap: 4, marginLeft: "auto", cursor: "pointer" }}>
        <input type="checkbox" checked={vorlesen} onChange={toggleVorlesen} /> Vorlesen
      </label>
      {vorlesen && <TtsSelect onChange={() => { speech.cancel(); }} />}
      {v.phase === "start" && (
        <button className="btn-xs" type="button" onClick={() => setEditFragen(e => !e)}>
          {editFragen ? "Fragen schließen" : "Fragen anpassen"}
        </button>
      )}
    </div>
  );

  const recording = dict.state === "recording";
  const transcribing = dict.state === "transcribing";

  const antwortFeld = (placeholder) => (
    <textarea rows={5} value={draftText} onChange={e => setDraftText(e.target.value)}
      placeholder={recording ? "Aufnahme läuft …" : placeholder}
      disabled={transcribing} style={{ marginTop: 8 }} data-testid="interview-antwort" />
  );
  const micButton = (
    !recording
      ? <button type="button" className="rec-btn rec-btn-start" onClick={dict.start} disabled={transcribing || busy !== null}>🎙 Aufnehmen</button>
      : <button type="button" className="rec-btn rec-btn-stop" onClick={dict.stop}>■ Stopp ({dict.seconds}s)</button>
  );

  if (v.phase === "start") {
    return (
      <div data-testid="interview-start">
        {header}
        {editFragen ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {v.fragen.map((f, i) => (
              <div key={f.key}>
                <div style={soft}>Frage {i + 1} · {manifest.abschnitte[f.ziel_abschnitt] || f.ziel_abschnitt}{f.pflicht ? " · Pflicht" : ""}</div>
                <textarea rows={2} value={f.text} disabled={!!f.pflicht}
                  onChange={e => patch({ fragen: v.fragen.map((x, k) => (k === i ? { ...x, text: e.target.value } : x)) })}
                  style={{ marginTop: 2 }} />
              </div>
            ))}
            <div style={btnRow}>
              <button className="btn-xs" type="button" onClick={resetFragen}>Auf Standard zurücksetzen</button>
              <span style={soft}>Pflichtfragen (Klient, Selbstgefährdung) sind nicht änderbar.</span>
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

  if (v.phase === "abschluss" && abschlussPunkt) {
    const typLabel = { widerspruch: "Widerspruch", luecke: "Lücke", plausibilitaet: "Plausibilität" }[abschlussPunkt.typ] || abschlussPunkt.typ;
    return (
      <div data-testid="interview-abschluss">
        {header}
        <div style={{ ...soft, marginBottom: 4 }}>Kurz durchgesehen · Punkt {v.abschlussIdx + 1} von {v.abschluss.length} · {typLabel}{abschlussPunkt.bezug?.length ? ` · Fragen ${abschlussPunkt.bezug.join(", ")}` : ""}</div>
        <div style={{ fontSize: 15, lineHeight: 1.4, fontWeight: 600, color: "var(--st-text)" }} data-testid="interview-frage">
          {abschlussPunkt.frage}
          <button type="button" className="btn-xs" title="Nochmal vorlesen" onClick={() => say([abschlussPunkt.frage])} style={{ marginLeft: 8 }}>🔊</button>
        </div>
        {antwortFeld("Antwort einsprechen oder tippen – oder „So lassen“, wenn es so bleiben soll …")}
        <div style={btnRow}>
          {micButton}
          {transcribing && <span style={soft}>Transkribiere …</span>}
          <span style={{ marginLeft: "auto" }} />
          <button type="button" className="btn-secondary" onClick={abschlussUeberspringen} disabled={recording || transcribing}>Alles überspringen</button>
          <button type="button" className="btn-secondary" onClick={abschlussBelassen} disabled={recording || transcribing} data-testid="interview-belassen">So lassen</button>
          <button type="button" className="btn-primary" onClick={abschlussAntworten} disabled={transcribing} data-testid="interview-weiter">
            {recording ? "Stopp" : "Antworten"}
          </button>
        </div>
      </div>
    );
  }

  if (v.phase === "fertig") {
    const offen = (v.abschluss || []).filter(a => a.belassen).length;
    return (
      <div data-testid="interview-fertig">
        {header}
        <div className="info-note" style={{ marginBottom: 8 }}>
          Interview abgeschlossen – die Antworten sind die Quelle für die Dokumentation. Zum Ändern eine Frage anklicken.
          {offen > 0 && ` ${offen} ${offen === 1 ? "Punkt" : "Punkte"} bewusst offen gelassen.`}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {v.eintraege.map((e, i) => (
            <div key={e.key} onClick={() => bearbeiten(i)} style={{ cursor: "pointer", padding: "6px 8px", border: "1px solid var(--st-gray-border)", borderRadius: 4, background: "var(--st-bg)" }}>
              <div style={{ ...soft, fontWeight: 600 }}>{i + 1}. {e.frage}</div>
              <div style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>{(e.antwort || "").trim() || <em style={soft}>nicht erhoben</em>}</div>
              {(e.nachfragen || []).map((n, k) => (
                <div key={k} style={{ marginTop: 4 }}>
                  <div style={{ ...soft, fontStyle: "italic" }}>
                    {(n.typ || "").startsWith("trigger") && <span style={{ color: "var(--st-red)", fontWeight: 600, marginRight: 4 }}>Suizidalität ·</span>}
                    {n.frage}
                  </div>
                  <div style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>{(n.antwort || "").trim() || <em style={soft}>nicht erhoben</em>}</div>
                </div>
              ))}
            </div>
          ))}
          {(v.abschluss || []).length > 0 && (
            <div style={{ padding: "6px 8px", border: "1px dashed var(--st-gray-border)", borderRadius: 4 }}>
              <div style={{ ...soft, fontWeight: 600 }}>Abschluss-Check</div>
              {v.abschluss.map((a, k) => (
                <div key={k} style={{ marginTop: 4 }}>
                  <div style={{ ...soft, fontStyle: "italic" }}>{a.frage}</div>
                  <div style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>{a.belassen || !(a.antwort || "").trim() ? <em style={soft}>so gelassen</em> : a.antwort}</div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div style={btnRow}>
          <button type="button" className="btn-secondary" onClick={neuStarten}>Neu beginnen</button>
        </div>
        <FeedbackButton jobId={v.sessionId ? `interview-${v.sessionId}` : null} workflow="dokumentation" context="interview_dialog" toast={toast} />
      </div>
    );
  }

  // Phase antwort | nachfrage
  const istNachfrage = v.phase === "nachfrage" && !!lastNachfrage;
  const istTrigger = istNachfrage && (lastNachfrage.typ || "").startsWith("trigger");
  return (
    <div data-testid="interview-dialog">
      {header}
      <div style={{ ...soft, marginBottom: 4 }}>
        Frage {v.idx + 1} von {total}{frage?.pflicht ? " · Pflichtfrage" : ""}
        {istTrigger && <span style={{ color: "var(--st-red)", fontWeight: 600 }}> · Nachfrage Suizidalität</span>}
        {istNachfrage && !istTrigger && " · Rückfrage"}
      </div>
      <div style={{ fontSize: 15, lineHeight: 1.4, fontWeight: 600, color: "var(--st-text)" }} data-testid="interview-frage">
        {istNachfrage ? lastNachfrage.frage : current.frage}
        <button type="button" className="btn-xs" title="Nochmal vorlesen" onClick={() => say([istNachfrage ? lastNachfrage.frage : current.frage])} style={{ marginLeft: 8 }}>🔊</button>
      </div>
      {istNachfrage && <div style={{ ...soft, marginTop: 2 }}>Zu: {current.frage}</div>}
      {!istNachfrage && frage?.hinweis && <div style={{ ...soft, marginTop: 2 }}>{frage.hinweis}</div>}

      {antwortFeld("Antwort einsprechen (Mikrofon) oder hier tippen …")}

      <div style={btnRow}>
        {micButton}
        {transcribing && <span style={soft}>Transkribiere …</span>}
        {busy === "turn" && <span style={soft}>Prüfe Antwort …</span>}
        {busy === "abschluss" && <span style={soft}>Schaue kurz über alles …</span>}
        <span style={{ marginLeft: "auto" }} />
        <button type="button" className="btn-secondary" onClick={zurueck} disabled={(v.idx === 0 && !istNachfrage) || busy !== null || recording}>Zurück</button>
        {!frage?.pflicht && <button type="button" className="btn-secondary" onClick={ueberspringen} disabled={busy !== null || recording || transcribing}>Überspringen</button>}
        <button type="button" className="btn-primary" onClick={weiter} disabled={busy !== null || transcribing} data-testid="interview-weiter">
          {recording ? "Stopp" : (v.idx + 1 >= total ? "Abschließen" : "Weiter")}
        </button>
      </div>
    </div>
  );
}

export { InterviewDialog, INTERVIEW_DEFAULT, emptyInterview, buildInterviewProtokoll, interviewHasContent, eintraegeFromFragen, anredeOf };
