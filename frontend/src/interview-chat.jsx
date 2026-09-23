// ────────────────────────────────────────────────────────────────────────────
// src/interview-chat.jsx — Dialog-Modus des Interviews (v19.31, S5).
//
// Das Modell fuehrt das Gespraech; die Fragenliste ist seine Checkliste.
// Jeder Turn: Behandler-Antwort (Diktat/Text) -> POST /interview/chat/stream
// -> gestreamter Interviewer-Satz (sofort vorgelesen, speech.sayStream)
// -> meta (Checkliste, fertig, Regie). Zustand liegt im P1-Draft (D2=B):
// `value` = {sessionId, setKey, setLabel, historie, checkliste, rueckfragen,
// triggerStufe, klient, fertig, phase: start|laeuft|fertig}.
//
// onKlient({anrede, initial, gender}) fuellt Kuerzel/Geschlecht in P1.
// ────────────────────────────────────────────────────────────────────────────
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchInterviewSets, interviewChatStream, interviewTranscribe, warmupInterviewServer } from "./api.js";
import { friendlyError } from "./shared.js";
import { getSpeechProvider } from "./speech.js";
import { FeedbackButton } from "./ui.jsx";

const LS_SET_KEY = "st_interview_set";
const LS_VORLESEN = "st_interview_vorlesen";

const CHAT_DEFAULT = {
  sessionId: "", setKey: "", setLabel: "", fragen: [],
  historie: [],          // {rolle: system|behandler, text, thema}
  checkliste: {},        // key -> offen|unklar|abgedeckt
  rueckfragen: {},       // thema -> n
  triggerStufe: 0,
  klient: null,
  fertig: false,
  phase: "start",        // start | laeuft | fertig
};

function emptyChat() { return { ...CHAT_DEFAULT, fragen: [], historie: [], checkliste: {}, rueckfragen: {} }; }
function newSessionId() { return `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`; }

// Gespraech fuer /jobs/generate (interview_gespraech). null solange nicht fertig.
function buildInterviewGespraech(v, { requireFertig = true } = {}) {
  if (!v || !v.historie?.length) return null;
  if (requireFertig && v.phase !== "fertig") return null;
  return {
    set: v.setKey, set_label: v.setLabel || v.setKey, session_id: v.sessionId || "",
    historie: v.historie.map(t => ({ rolle: t.rolle, text: t.text || "", thema: t.thema || "" })),
    klient: v.klient || null,
  };
}

function chatHasContent(v) {
  return !!(v && v.phase !== "start" && v.historie?.some(t => t.rolle === "behandler" && (t.text || "").trim()));
}

function anredeOf(k) { return k && k.anrede && k.initial ? `${k.anrede} ${k.initial}` : null; }

// ── Push-to-talk (identisch zu interview.jsx) ───────────────────────────────
function useDictation({ onText, onError, onStart }) {
  const [state, setState] = useState("idle");
  const recRef = useRef(null); const chunksRef = useRef([]); const streamRef = useRef(null);
  const [seconds, setSeconds] = useState(0); const timerRef = useRef(null);
  const stopTracks = () => {
    if (streamRef.current) { streamRef.current.getTracks().forEach(t => t.stop()); streamRef.current = null; }
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
  };
  async function start() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      onError?.("Mikrofon-Aufnahme wird von diesem Browser nicht unterstützt (HTTPS nötig). Du kannst auch tippen.");
      return;
    }
    try {
      onStart?.();
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      streamRef.current = stream;
      const mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm"].find(m => MediaRecorder.isTypeSupported(m)) || "";
      const rec = new MediaRecorder(stream, { mimeType: mime || undefined, audioBitsPerSecond: 24000 });
      chunksRef.current = [];
      rec.ondataavailable = (e) => { if (e.data && e.data.size > 0) chunksRef.current.push(e.data); };
      rec.onstop = async () => {
        stopTracks();
        const blob = new Blob(chunksRef.current, { type: mime || "audio/webm" });
        chunksRef.current = [];
        if (blob.size < 200) { setState("idle"); return; }
        const file = new File([blob], `antwort.${mime.includes("ogg") ? "ogg" : "webm"}`, { type: blob.type });
        setState("transcribing");
        try { const d = await interviewTranscribe(file); onText?.((d.transcript || "").trim()); }
        catch (e) { onError?.("Transkription fehlgeschlagen: " + friendlyError(e)); }
        finally { setState("idle"); }
      };
      rec.start(1000); recRef.current = rec; setSeconds(0);
      timerRef.current = setInterval(() => setSeconds(s => s + 1), 1000);
      setState("recording");
    } catch (err) {
      stopTracks();
      onError?.(err?.name === "NotAllowedError" ? "Mikrofon-Zugriff verweigert. Bitte in den Browser-Einstellungen erlauben." : `Aufnahme fehlgeschlagen: ${err?.message || err}`);
    }
  }
  function stop() { const rec = recRef.current; if (rec && rec.state !== "inactive") { try { rec.stop(); } catch { /* ignoriert */ } } recRef.current = null; }
  useEffect(() => () => { stopTracks(); }, []);
  return { state, seconds, start, stop };
}

// ── Komponente ──────────────────────────────────────────────────────────────
function InterviewChat({ value, onChange, toast, model, onKlient }) {
  const v = value && value.historie ? value : emptyChat();
  const [manifest, setManifest] = useState(null);
  const [loadErr, setLoadErr] = useState(null);
  const [serverState, setServerState] = useState(null);
  const [streaming, setStreaming] = useState(false);
  const [liveText, setLiveText] = useState("");        // gerade gestreamter Satz
  const [draftText, setDraftText] = useState("");
  const [vorlesen, setVorlesen] = useState(() => { try { return localStorage.getItem(LS_VORLESEN) !== "0"; } catch { return true; } });
  const speech = getSpeechProvider();
  const abortRef = useRef(null);
  const logRef = useRef(null);

  const patch = useCallback((p) => onChange({ ...v, ...p }), [onChange, v]);

  useEffect(() => {
    let alive = true;
    const load = () => fetchInterviewSets().then(m => { if (alive) setManifest(m); }).catch(e => { if (alive) setLoadErr(friendlyError(e)); });
    load();
    warmupInterviewServer().then(st => { if (alive) setServerState(st?.status || "error"); }).catch(() => { if (alive) setServerState("error"); });
    const onOk = () => { if (!alive) return; setServerState("ok"); load(); };
    window.addEventListener("st-health-ok", onOk);
    return () => { alive = false; window.removeEventListener("st-health-ok", onOk); };
  }, []);

  useEffect(() => {
    if (!manifest || v.fragen.length) return;
    let key = ""; try { key = localStorage.getItem(LS_SET_KEY) || ""; } catch { /* ignoriert */ }
    const set = manifest.sets.find(s => s.key === key) || manifest.sets.find(s => s.key === manifest.default_set) || manifest.sets[0];
    if (set) patch({ setKey: set.key, setLabel: set.label, fragen: set.fragen.map(f => ({ ...f })) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manifest]);

  useEffect(() => () => { speech.cancel(); abortRef.current?.abort(); }, [speech]);
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [v.historie.length, liveText]);

  const dict = useDictation({
    onText: (t) => { if (t) setDraftText(prev => (prev.trim() ? prev.trim() + " " + t : t)); },
    onError: (m) => toast && toast(m),
    onStart: () => speech.cancel(),
  });

  function toggleVorlesen() {
    const next = !vorlesen; setVorlesen(next);
    try { localStorage.setItem(LS_VORLESEN, next ? "1" : "0"); } catch { /* ignoriert */ }
    if (!next) speech.cancel();
  }
  function chooseSet(key) {
    const set = manifest?.sets.find(s => s.key === key); if (!set) return;
    try { localStorage.setItem(LS_SET_KEY, key); } catch { /* ignoriert */ }
    patch({ setKey: set.key, setLabel: set.label, fragen: set.fragen.map(f => ({ ...f })) });
  }

  // Ein Turn: Historie (+ neue Behandler-Antwort) -> Stream -> Historie + meta
  async function turn(historie, extra = {}) {
    // `extra` traegt beim Start die frischen Felder (Session, leere Listen),
    // die in `v` noch nicht angekommen sind - deshalb hier zusammenfuehren.
    const cur = { ...v, ...extra };
    setStreaming(true); setLiveText("");
    const tts = vorlesen ? speech.sayStream() : null;
    const ctrl = new AbortController(); abortRef.current = ctrl;
    let meta = null;
    try {
      meta = await interviewChatStream({
        set: cur.setKey, historie, checkliste: cur.checkliste, rueckfragen: cur.rueckfragen,
        trigger_stufe: cur.triggerStufe, klient: cur.klient, model: model || null, session_id: cur.sessionId || null,
      }, (ev) => {
        if (ev.type === "delta") { setLiveText(t => t + ev.text); tts && tts.push(ev.text); }
      }, { signal: ctrl.signal });
    } catch (e) {
      tts && tts.end();
      setStreaming(false); setLiveText("");
      if (e?.name === "AbortError") return;
      toast && toast("Antwort nicht möglich (" + friendlyError(e) + ") – bitte nochmal.");
      patch({ ...extra, historie });
      return;
    } finally { abortRef.current = null; }
    tts && tts.end();
    setStreaming(false); setLiveText("");
    if (!meta) { patch({ ...extra, historie }); return; }
    const neu = [...historie, { rolle: "system", text: meta.sage || "", thema: meta.thema || "" }];
    const p = {
      ...extra,
      historie: neu, checkliste: meta.checkliste || cur.checkliste, rueckfragen: meta.rueckfragen || cur.rueckfragen,
      triggerStufe: meta.trigger_stufe || 0, klient: meta.klient || cur.klient, fertig: !!meta.fertig,
      phase: meta.fertig ? "fertig" : "laeuft",
    };
    if (meta.klient && !cur.klient && onKlient) onKlient(meta.klient);
    patch(p);
    if (meta.fertig_verweigert) toast && toast("Noch nicht fertig – Pflichtpunkte fehlen (Klient, Selbstgefährdung).");
  }

  function start() {
    speech.cancel();
    const p = { sessionId: newSessionId(), historie: [], checkliste: {}, rueckfragen: {}, triggerStufe: 0, klient: null, fertig: false, phase: "laeuft" };
    onChange({ ...v, ...p });
    turn([], p);
  }
  function senden() {
    if (dict.state === "recording") { dict.stop(); return; }
    const t = draftText.trim(); if (!t || streaming) return;
    setDraftText("");
    turn([...v.historie, { rolle: "behandler", text: t }]);
  }
  function abbrechen() { abortRef.current?.abort(); speech.cancel(); setStreaming(false); setLiveText(""); }
  function beenden() {
    // Behandler beendet aktiv: ein Turn mit Bitte um Abschluss (Backend verweigert ohne Pflicht)
    turn([...v.historie, { rolle: "behandler", text: "Das war's von meiner Seite, bitte abschließen." }]);
  }
  function neu() {
    if (chatHasContent(v) && !confirm("Gespräch verwerfen und neu beginnen?")) return;
    speech.cancel(); abortRef.current?.abort();
    patch({ ...emptyChat(), setKey: v.setKey, setLabel: v.setLabel, fragen: v.fragen });
  }
  function weiterfuehren() { patch({ phase: "laeuft", fertig: false }); }

  // ── Render ───────────────────────────────────────────────────────────────
  const soft = { fontSize: 11, color: "var(--st-text-soft)" };
  const row = { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 10 };
  if (loadErr) return <div className="info-note">Fragen-Sets konnten nicht geladen werden: {loadErr}</div>;
  if (!manifest || !v.fragen.length) return <div style={{ ...soft, padding: "8px 0" }}>Lade Fragen-Sets …</div>;

  const serverNote = serverState === "starting" ? "Server startet – Antworten werden verarbeitet, sobald er läuft (3–6 min)."
    : serverState === "no_server" ? "Kein Server verfügbar – bitte später erneut versuchen."
    : serverState === "blocked_night" ? "Zwischen 23 und 5 Uhr startet kein Server automatisch." : null;
  // v19.31.2: optionale Punkte zaehlen nicht in den Fortschritt.
  const pflichtFragen = v.fragen.filter(f => !f.optional);
  const abgedeckt = pflichtFragen.filter(f => v.checkliste[f.key] === "abgedeckt").length;
  const recording = dict.state === "recording", transcribing = dict.state === "transcribing";

  const header = (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
      <span style={{ ...soft, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em" }}>Verfahren</span>
      <select value={v.setKey} onChange={e => chooseSet(e.target.value)} disabled={v.phase !== "start"} data-testid="chat-set"
        style={{ fontSize: 12, padding: "3px 6px", borderRadius: 3, border: "1px solid var(--st-gray-border)", background: "var(--st-bg)", color: "var(--st-text)" }}>
        {manifest.sets.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
      </select>
      {anredeOf(v.klient) && <span style={{ ...soft, fontWeight: 600 }} data-testid="chat-klient">{anredeOf(v.klient)}</span>}
      {serverNote && <span style={{ ...soft, color: "var(--st-red)" }}>{serverNote}</span>}
      <label style={{ ...soft, display: "flex", alignItems: "center", gap: 4, marginLeft: "auto", cursor: "pointer" }}>
        <input type="checkbox" checked={vorlesen} onChange={toggleVorlesen} /> Vorlesen
      </label>
    </div>
  );

  const checkliste = (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 180 }} data-testid="chat-checkliste">
      <div style={{ ...soft, fontWeight: 600 }}>Fragenliste · {abgedeckt}/{pflichtFragen.length}</div>
      {v.fragen.map((f, i) => {
        const st = v.checkliste[f.key] || "offen";
        const mark = st === "abgedeckt" ? "✓" : st === "unklar" ? "?" : "·";
        const col = st === "abgedeckt" ? "var(--st-text-soft)" : st === "unklar" ? "var(--st-red)" : "var(--st-text)";
        return <div key={f.key} title={f.text} style={{ fontSize: 12, color: col, display: "flex", gap: 6 }}>
          <span style={{ width: 12, textAlign: "center", fontWeight: 700 }}>{mark}</span>
          <span style={{ textDecoration: st === "abgedeckt" ? "line-through" : "none" }}>{i + 1}. {f.text.length > 44 ? f.text.slice(0, 42) + "…" : f.text}{f.pflicht ? " *" : ""}{f.optional ? " (optional)" : ""}</span>
        </div>;
      })}
    </div>
  );

  if (v.phase === "start") {
    return <div data-testid="chat-start">
      {header}
      <div className="info-note">Du erzählst, das System fragt nach – entlang der Fragenliste, aber im Gespräch. Am Ende wird aus deinen Antworten die Dokumentation erstellt.</div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: 16, marginTop: 10 }}>
        <ol style={{ margin: "0 0 0 18px", padding: 0, fontSize: 13, color: "var(--st-text)" }}>{v.fragen.map(f => <li key={f.key} style={{ marginBottom: 4 }}>{f.text}</li>)}</ol>
      </div>
      <div style={row}><button className="btn-primary" type="button" onClick={start} data-testid="chat-start-btn">Gespräch beginnen</button><span style={soft}>{v.fragen.length} Punkte · Antworten per Mikrofon oder Tastatur</span></div>
    </div>;
  }

  return <div data-testid="chat-dialog">
    {header}
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: 16 }}>
      <div>
        <div ref={logRef} style={{ maxHeight: 360, overflowY: "auto", display: "flex", flexDirection: "column", gap: 8, padding: "4px 2px" }} data-testid="chat-log">
          {v.historie.map((t, i) => (
            <div key={i} style={{ alignSelf: t.rolle === "system" ? "flex-start" : "flex-end", maxWidth: "85%",
              background: t.rolle === "system" ? "var(--st-gray-light)" : "var(--st-red-pale)", color: "var(--st-text)",
              borderRadius: 10, padding: "8px 12px", fontSize: 14, lineHeight: 1.45, whiteSpace: "pre-wrap" }}>
              {t.text}
            </div>
          ))}
          {streaming && <div style={{ alignSelf: "flex-start", maxWidth: "85%", background: "var(--st-gray-light)", borderRadius: 10, padding: "8px 12px", fontSize: 14, lineHeight: 1.45 }} data-testid="chat-live">{liveText || "…"}</div>}
        </div>
        {v.phase === "fertig" ? (
          <>
            <div className="info-note" style={{ marginTop: 8 }}>Gespräch abgeschlossen – deine Antworten sind die Quelle für die Dokumentation.</div>
            <div style={row}>
              <button type="button" className="btn-secondary" onClick={neu}>Neu beginnen</button>
              <button type="button" className="btn-secondary" onClick={weiterfuehren}>Noch etwas ergänzen</button>
              <span style={{ marginLeft: "auto" }} />
            </div>
            <FeedbackButton jobId={v.sessionId ? `interview-${v.sessionId}` : null} workflow="dokumentation" context="interview_chat" toast={toast} />
          </>
        ) : (
          <>
            <textarea rows={3} value={draftText} onChange={e => setDraftText(e.target.value)} disabled={transcribing || streaming}
              placeholder={recording ? "Aufnahme läuft …" : "Antwort einsprechen oder tippen … (Enter = senden)"} style={{ marginTop: 8 }} data-testid="chat-antwort"
              onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); senden(); } }} />
            <div style={row}>
              {!recording
                ? <button type="button" className="rec-btn rec-btn-start" onClick={dict.start} disabled={transcribing || streaming}>🎙 Aufnehmen</button>
                : <button type="button" className="rec-btn rec-btn-stop" onClick={dict.stop}>■ Stopp ({dict.seconds}s)</button>}
              {transcribing && <span style={soft}>Transkribiere …</span>}
              {streaming && <><span style={soft}>Antwortet …</span><button type="button" className="btn-xs" onClick={abbrechen}>Abbrechen</button></>}
              <span style={{ marginLeft: "auto" }} />
              <button type="button" className="btn-secondary" onClick={beenden} disabled={streaming || recording} title="Bittet das System um den Abschluss">Abschließen</button>
              <button type="button" className="btn-primary" onClick={senden} disabled={streaming || transcribing || (!recording && !draftText.trim())} data-testid="chat-senden">{recording ? "Stopp" : "Senden"}</button>
            </div>
          </>
        )}
      </div>
      {checkliste}
    </div>
  </div>;
}

export { InterviewChat, CHAT_DEFAULT, emptyChat, buildInterviewGespraech, chatHasContent };
