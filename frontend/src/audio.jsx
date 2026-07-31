// ────────────────────────────────────────────────────────────────────────────
// src/audio.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { apiFetch, getApiBase } from "./api.js";
import { MAX_UPLOAD_MB, _pendingLabels, fmtMB, fmtSec } from "./shared.js";

function AudioRecorder({ onRecorded, onError }) {
  const [state, setState] = useState("idle"); // idle | recording | paused | finalizing
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0);      // 0-100 Pegel
  const [gain, setGain] = useState(100);       // 50-200 Gain in %
  const mediaRecRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);
  const timerRef = useRef(null);
  const startTsRef = useRef(0);
  const pausedAccumRef = useRef(0);
  const audioCtxRef = useRef(null);
  const analyserRef = useRef(null);
  const gainNodeRef = useRef(null);
  const meterRafRef = useRef(null);
  const cancelledRef = useRef(false);

  // Timer aktualisieren
  useEffect(() => {
    if (state === "recording") {
      timerRef.current = setInterval(() => {
        const elapsed = (Date.now() - startTsRef.current) / 1000 + pausedAccumRef.current;
        setSeconds(elapsed);
      }, 500);
    } else {
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [state]);

  // Warnung vor dem Tab-Schließen
  useEffect(() => {
    if (state !== "recording" && state !== "paused") return;
    const handler = (e) => {
      e.preventDefault();
      e.returnValue = "Aufnahme läuft. Tab schließen verwirft die Aufnahme.";
      return e.returnValue;
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [state]);

  // v19.10: Heartbeat waehrend der Aufnahme.
  // Der Pod sieht waehrend einer laufenden Aufnahme sonst KEINEN Request:
  // der Upload erfolgt erst am Ende, Recording hat kein updated_at, und das
  // 30-s-Polling startet nur bei Items mit Status uploading/transcribing.
  // Ohne dieses Lebenszeichen wuerde der Idle-Auto-Stopp im Cloudflare-Worker
  // den Pod mitten in der Sitzung herunterfahren — die Aufnahme liegt zu dem
  // Zeitpunkt nur im Browser, der Upload danach liefe ins Leere.
  // Bewusst nur an die Aufnahme gekoppelt, nicht an "Makro offen": ein
  // vergessener Tab soll den Pod gerade NICHT am Leben halten.
  useEffect(() => {
    if (state !== "recording" && state !== "paused") return;
    let stopped = false;
    const ping = () => {
      if (stopped) return;
      // Fehler bewusst schlucken — ein Heartbeat darf die Aufnahme nie stoeren.
      try {
        Promise.resolve(
          apiFetch(`${getApiBase()}/activity/heartbeat`, { method: "POST" })
        ).catch(() => {});
      } catch { /* ignoriert */ }
    };
    ping();
    const id = setInterval(ping, 60000);
    return () => { stopped = true; clearInterval(id); };
  }, [state]);

  async function start() {
    // getUserMedia erfordert HTTPS oder localhost
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      const isHttp = location.protocol === "http:" && location.hostname !== "localhost" && location.hostname !== "127.0.0.1";
      onError && onError(
        isHttp
          ? "Mikrofon-Aufnahme ist nur über HTTPS verfügbar. Diese Seite wird über HTTP geladen — bitte den Administrator bitten, HTTPS zu aktivieren. Alternativ kann eine Aufnahme-Datei hochgeladen werden."
          : "Mikrofon-Aufnahme wird von diesem Browser nicht unterstützt. Bitte einen aktuellen Browser (Chrome, Edge, Firefox) verwenden."
      );
      return;
    }
    try {
      cancelledRef.current = false;
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;

      // Audio-Graph: Mic → Gain → Analyser → Destination (für Metering + Gain-Regelung)
      const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
      audioCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const gn = ctx.createGain();
      gn.gain.value = gain / 100;
      gainNodeRef.current = gn;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.5;
      analyserRef.current = analyser;
      source.connect(gn);
      gn.connect(analyser);
      // Destination für Recording: GainNode-Output als neuer Stream
      const dest = ctx.createMediaStreamDestination();
      gn.connect(dest);
      const recordStream = dest.stream;

      // Pegel-Meter-Loop
      const dataArr = new Uint8Array(analyser.frequencyBinCount);
      function meterLoop() {
        analyser.getByteFrequencyData(dataArr);
        let sum = 0;
        for (let i = 0; i < dataArr.length; i++) sum += dataArr[i];
        const avg = sum / dataArr.length;
        setLevel(Math.min(100, Math.round(avg * 100 / 128)));
        meterRafRef.current = requestAnimationFrame(meterLoop);
      }
      meterLoop();

      // Bester Codec für Sprache bei kleinster Dateigröße
      const mimeCandidates = [
        "audio/webm;codecs=opus",
        "audio/ogg;codecs=opus",
        "audio/webm",
      ];
      const mimeType = mimeCandidates.find(m => MediaRecorder.isTypeSupported(m)) || "";

      const rec = new MediaRecorder(recordStream, {
        mimeType: mimeType || undefined,
        audioBitsPerSecond: 24000, // Sprache komprimiert, ~180 KB/min
      });
      chunksRef.current = [];
      rec.ondataavailable = (e) => { if (e.data && e.data.size > 0) chunksRef.current.push(e.data); };
      rec.onstop = () => {
        if (meterRafRef.current) { cancelAnimationFrame(meterRafRef.current); meterRafRef.current = null; }
        if (audioCtxRef.current) { audioCtxRef.current.close().catch(() => {}); audioCtxRef.current = null; }
        setLevel(0);
        setState("idle");
        setSeconds(0);
        pausedAccumRef.current = 0;
        if (streamRef.current) {
          streamRef.current.getTracks().forEach(t => t.stop());
          streamRef.current = null;
        }
        if (cancelledRef.current) {
          chunksRef.current = [];
          return;
        }
        const blob = new Blob(chunksRef.current, { type: mimeType || "audio/webm" });
        const ext = mimeType.includes("ogg") ? "ogg" : "webm";
        const stamp = new Date().toISOString().replace(/[:T]/g, "-").slice(0, 19);
        const file = new File([blob], `aufnahme-${stamp}.${ext}`, { type: blob.type });
        chunksRef.current = [];
        onRecorded(file);
      };

      rec.start(1000); // 1s Chunks, damit ondataavailable regelmäßig feuert
      mediaRecRef.current = rec;
      startTsRef.current = Date.now();
      pausedAccumRef.current = 0;
      setSeconds(0);
      setState("recording");
    } catch (err) {
      const msg = err?.name === "NotAllowedError"
        ? "Mikrofon-Zugriff verweigert. Bitte in den Browser-Einstellungen erlauben."
        : `Aufnahme fehlgeschlagen: ${err?.message || err}`;
      onError && onError(msg);
    }
  }

  function pause() {
    const rec = mediaRecRef.current;
    if (!rec) return;
    if (rec.state === "recording") {
      rec.pause();
      pausedAccumRef.current += (Date.now() - startTsRef.current) / 1000;
      setState("paused");
    }
  }

  function resume() {
    const rec = mediaRecRef.current;
    if (!rec) return;
    if (rec.state === "paused") {
      rec.resume();
      startTsRef.current = Date.now();
      setState("recording");
    }
  }

  function stop() {
    const rec = mediaRecRef.current;
    if (!rec) return;
    setState("finalizing");
    try { rec.stop(); } catch (_) {}
  }

  function cancel() {
    cancelledRef.current = true;
    if (meterRafRef.current) { cancelAnimationFrame(meterRafRef.current); meterRafRef.current = null; }
    if (audioCtxRef.current) { audioCtxRef.current.close().catch(() => {}); audioCtxRef.current = null; }
    setLevel(0);
    const rec = mediaRecRef.current;
    if (rec) {
      try { rec.stop(); } catch (_) {}
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
    chunksRef.current = [];
    setState("idle");
    setSeconds(0);
    pausedAccumRef.current = 0;
  }

  // Gain live anpassen
  function onGainChange(e) {
    const v = Number(e.target.value);
    setGain(v);
    if (gainNodeRef.current) gainNodeRef.current.gain.value = v / 100;
  }

  const isActive = state === "recording" || state === "paused";

  return (
    <div className={"recorder-box" + (isActive ? " recording" : "")}>
      {state === "idle" && (
        <>
          <div className="rec-status">&#127897; Mikrofon-Aufnahme</div>
          <div className="rec-info">Sprache wird komprimiert – geeignet auch für lange Sitzungen</div>
          <div className="rec-buttons">
            <button className="rec-btn rec-btn-start" onClick={start}>Aufnahme starten</button>
          </div>
        </>
      )}
      {state === "recording" && (
        <>
          <div className="rec-status active"><span className="rec-dot" />Aufnahme läuft</div>
          <div className="rec-timer">{fmtSec(seconds)}</div>
          <div className="rec-meter-wrap">
            <div className="rec-meter-bar">
              <div className="rec-meter-fill" style={{ width: `${level}%` }} />
            </div>
          </div>
          <div className="rec-gain-wrap">
            <span className="rec-gain-label">&#128264;</span>
            <input type="range" className="rec-gain-slider" min="50" max="200" value={gain} onChange={onGainChange} />
            <span className="rec-gain-label">{gain}%</span>
          </div>
          <div className="rec-buttons">
            <button className="rec-btn rec-btn-pause" onClick={pause}>Pause</button>
            <button className="rec-btn rec-btn-stop" onClick={stop}>Stoppen & Übernehmen</button>
            <button className="rec-btn rec-btn-pause" onClick={cancel}>Verwerfen</button>
          </div>
          <div className="rec-info">Tab bitte nicht schließen – Aufnahme ginge verloren</div>
        </>
      )}
      {state === "paused" && (
        <>
          <div className="rec-status">Pausiert</div>
          <div className="rec-timer">{fmtSec(seconds)}</div>
          <div className="rec-meter-wrap">
            <div className="rec-meter-bar">
              <div className="rec-meter-fill" style={{ width: `${level}%` }} />
            </div>
          </div>
          <div className="rec-gain-wrap">
            <span className="rec-gain-label">&#128264;</span>
            <input type="range" className="rec-gain-slider" min="50" max="200" value={gain} onChange={onGainChange} />
            <span className="rec-gain-label">{gain}%</span>
          </div>
          <div className="rec-buttons">
            <button className="rec-btn rec-btn-start" onClick={resume}>Fortsetzen</button>
            <button className="rec-btn rec-btn-stop" onClick={stop}>Stoppen & Übernehmen</button>
            <button className="rec-btn rec-btn-pause" onClick={cancel}>Verwerfen</button>
          </div>
        </>
      )}
      {state === "finalizing" && (
        <div className="rec-status">Aufnahme wird vorbereitet ...</div>
      )}
    </div>
  );
}

/**
 * AudioInput - kombiniert Browser-Aufnahme und Upload bestehender Dateien.
 * Zeigt Warnung wenn Upload-Datei > MAX_UPLOAD_MB.
 */
function AudioInput({ file, onFile }) {
  const [mode, setMode] = useState("p0");
  const [recError, setRecError] = useState(null);
  const [sizeWarn, setSizeWarn] = useState(null);
  const [p0List, setP0List]       = useState([]);
  const [p0Loading, setP0Loading] = useState(false);
  const [p0Error, setP0Error]     = useState(null);
  const [p0Selected, setP0Selected] = useState(null);
  const p0PollRef = useRef(null);

  useEffect(() => { loadP0(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Polling (30s) bei pending Items + st-health-ok Event
  useEffect(() => {
    const hasPending = p0List.some(r => r.status === "uploading" || r.status === "transcribing");
    if (hasPending && !p0PollRef.current) {
      p0PollRef.current = setInterval(loadP0, 30000);
    } else if (!hasPending && p0PollRef.current) {
      clearInterval(p0PollRef.current);
      p0PollRef.current = null;
    }
    return () => { if (p0PollRef.current) { clearInterval(p0PollRef.current); p0PollRef.current = null; } };
  }, [p0List]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const handler = () => loadP0();
    window.addEventListener("st-health-ok", handler);
    return () => window.removeEventListener("st-health-ok", handler);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // v19.15 (Sprint E): Keep-Mounted-Fix. AudioInput mountet nur einmal beim
  // App-Start (Panels werden per display:none versteckt, nie unmounted).
  // Neue Aufnahmen aus P0 wurden deshalb erst nach Seiten-Reload sichtbar.
  // 1) st-recordings-changed: P0 feuert nach Upload/Record/Delete/Retry/Label.
  // 2) st-page-changed: Schutznetz – beim Aktivieren eines Panels mit
  //    AudioInput (p1: Doku, p2: Anamnese) Liste frisch laden, damit auch
  //    Statuswechsel (transcribing→ready) ohne eigenes pending-Polling ankommen.
  useEffect(() => {
    const onChanged = () => loadP0();
    const onPage = (e) => {
      if (e.detail === "p1" || e.detail === "p2") loadP0();
    };
    window.addEventListener("st-recordings-changed", onChanged);
    window.addEventListener("st-page-changed", onPage);
    return () => {
      window.removeEventListener("st-recordings-changed", onChanged);
      window.removeEventListener("st-page-changed", onPage);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function handleFile(f) {
    setRecError(null);
    if (!f) { setSizeWarn(null); onFile(null); return; }
    const sizeMB = f.size / (1024 * 1024);
    if (sizeMB > MAX_UPLOAD_MB) {
      setSizeWarn(`Datei ist ${fmtMB(f.size)} MB groß. Upload-Limit liegt bei ${MAX_UPLOAD_MB} MB.`);
      return;
    }
    setSizeWarn(null);
    onFile(f);
  }

  async function loadP0() {
    setP0Loading(true);
    setP0Error(null);
    try {
      const url = `${getApiBase()}/recordings`;
      const res = await apiFetch(url);
      const all = await res.json();

      // Pending Labels flushen sobald Item ready
      all.filter(r => r.status === "ready" && _pendingLabels[r.id] !== undefined)
        .forEach(async r => {
          const label = _pendingLabels[r.id];
          delete _pendingLabels[r.id];
          r.label = label;
          try {
            await apiFetch(`${getApiBase()}/recordings/${r.id}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ label }),
            });
          } catch (e) { /* ignorieren */ }
        });

      const withLabels = all.map(r =>
        _pendingLabels[r.id] !== undefined ? { ...r, label: _pendingLabels[r.id] } : r
      );
      setP0List(withLabels.filter(r => r.status !== "deleted"));
    } catch (e) {
      setP0Error("Aufnahmen konnten nicht geladen werden.");
    } finally {
      setP0Loading(false);
    }
  }

  function switchMode(m) { setMode(m); if (m === "p0") loadP0(); }

  function pickP0(rec) {
    setP0Selected(rec);
    onFile({ __p0recording: true, transcript: rec.transcript, name: rec.label || `Aufnahme #${rec.id}`, id: rec.id });
  }

  function clearP0() { setP0Selected(null); onFile(null); }

  if (file) {
    if (file.__p0recording) {
      const isPending = !file.transcript;
      return (
        <div className="recorder-box" style={{ background: "var(--st-red-pale)", borderColor: "var(--st-red)", borderStyle: "solid" }}>
          <div className="rec-status">&#127897; {file.name}</div>
          <div className="rec-info">
            {isPending
              ? <span style={{color:"#0060c0"}}>⏳ Transkription läuft – wird beim Generieren priorisiert</span>
              : `Transkript aus der Aufnahmeliste · ${Math.round(file.transcript.split(" ").length)} Wörter`}
          </div>
          <div className="rec-buttons">
            <button className="rec-btn rec-btn-pause" onClick={clearP0}>Entfernen</button>
          </div>
        </div>
      );
    }
    return (
      <div className="recorder-box" style={{ background: "var(--st-red-pale)", borderColor: "var(--st-red)", borderStyle: "solid" }}>
        <div className="rec-status">&#127897; {file.name}</div>
        <div className="rec-info">{fmtMB(file.size)} MB</div>
        <div className="rec-buttons">
          <button className="rec-btn rec-btn-stop" onClick={() => {
            const url = URL.createObjectURL(file);
            const a = document.createElement("a");
            a.href = url; a.download = file.name; a.click();
            URL.revokeObjectURL(url);
          }}>↓ Aufnahme speichern</button>
          <button className="rec-btn rec-btn-pause" onClick={() => { setSizeWarn(null); onFile(null); }}>Entfernen</button>
        </div>
      </div>
    );
  }

  return (
    <div className="audio-input-wrap">
      <div className="p0-picker">
        {p0Loading && <div className="p0-hint">Lade Aufnahmen…</div>}
        {p0Error   && <div className="upload-warn">{p0Error}</div>}
        {!p0Loading && !p0Error && p0List.length === 0 && (
          <div className="p0-hint" style={{display:"flex",flexDirection:"column",gap:8,alignItems:"center"}}>
            <span>Noch keine Aufnahmen vorhanden.</span>
            <button className="btn-secondary" style={{fontSize:12,padding:"4px 12px"}}
              onClick={() => window.dispatchEvent(new CustomEvent("st-nav", { detail: "p0" }))}>
              ⏺ Zu Aufnahmen (P0)
            </button>
          </div>
        )}
        {p0List.length > 0 && (
          <div className="p0-picker-list">
            {p0List.map(r => {
              const isPending = r.status === "uploading" || r.status === "transcribing";
              const isError   = r.status === "error";
              const statusLabel = isPending
                ? (r.status === "transcribing" ? "⏳ Transkription läuft…" : "⏳ Wird hochgeladen…")
                : isError ? "⚠️ Fehler" : null;
              return (
                <div key={r.id} className="p0-picker-item"
                  onClick={() => !isError && pickP0(r)}
                  style={isError ? {opacity:0.5,cursor:"default"} : {}}
                  title={isPending ? "Transkription läuft – wird beim Generieren priorisiert" : isError ? (r.error_msg || "Fehler") : "Aufnahme auswählen"}>
                  <span className="p0-picker-label">{r.label || <em>Ohne Beschriftung</em>}</span>
                  <span className="p0-picker-meta">
                    {statusLabel
                      ? <span style={{color:isPending?"#0060c0":"#c02020",fontWeight:600}}>{statusLabel}</span>
                      : <>
                          {r.created_at ? new Date(r.created_at).toLocaleDateString("de-DE",{day:"2-digit",month:"2-digit",year:"2-digit"}) : ""}
                          {r.duration_s ? ` · ${Math.floor(r.duration_s/60)}:${String(Math.floor(r.duration_s%60)).padStart(2,"0")}` : ""}
                        </>
                    }
                  </span>
                </div>
              );
            })}
          </div>
        )}
        {p0List.length > 0 && (
          <div style={{marginTop:6,textAlign:"right"}}>
            <button className="btn-secondary" style={{fontSize:11,padding:"2px 8px"}}
              onClick={() => window.dispatchEvent(new CustomEvent("st-nav", { detail: "p0" }))}>
              + Neue Aufnahme
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export { AudioRecorder, AudioInput };
