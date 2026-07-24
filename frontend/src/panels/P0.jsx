// ────────────────────────────────────────────────────────────────────────────
// src/panels/P0.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { apiFetch, downloadViaApi, getApiBase, getConfluenceUser } from "../api.js";
import { AudioRecorder } from "../audio.jsx";
import { _pendingLabels, _recordingsCache, offlineQueueAdd, offlineQueueList, offlineQueueRemove } from "../shared.js";
import { Card, Dropzone } from "../ui.jsx";


function LabelEdit({ value, placeholder, onSave }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft]     = useState(value);
  useEffect(() => { setDraft(value); }, [value]);
  function commit() {
    setEditing(false);
    if (draft.trim() !== value) onSave(draft.trim());
  }
  if (editing) return (
    <input autoFocus value={draft}
      onChange={e => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={e => { if (e.key === "Enter") commit(); if (e.key === "Escape") { setDraft(value); setEditing(false); } }}
      maxLength={120}
      style={{width:"100%",fontWeight:600,fontSize:13,border:"1px solid var(--border)",borderRadius:3,padding:"2px 6px",boxSizing:"border-box"}}
    />
  );
  return (
    <div onClick={() => setEditing(true)} title="Klicken zum Bearbeiten"
      style={{fontWeight:600,cursor:"text",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",minHeight:18}}>
      {value || <em style={{color:"var(--fg-muted)",fontWeight:400}}>{placeholder}</em>}
    </div>
  );
}

function P0({ toast }) {
  const [recordings, setRecordingsRaw] = useState(_recordingsCache.data);
  const [loading, setLoading]          = useState(_recordingsCache.data.length === 0);
  const [error, setError]              = useState(null);
  const [offlineQueue, setOfflineQueue] = useState([]);
  const [uploading, setUploading]       = useState(false);
  const pollRef      = useRef(null);
  const uploadingRef = useRef(false);

  // Wrapper: State + Modul-Cache synchron halten
  // Pending Labels immer einmergen damit sie nicht durch Server-Daten überschrieben werden
  function setRecordings(updater) {
    setRecordingsRaw(prev => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      const merged = next.map(r =>
        _pendingLabels[r.id] !== undefined ? { ...r, label: _pendingLabels[r.id] } : r
      );
      _recordingsCache.data = merged;
      return merged;
    });
  }

  const loadOfflineQueue = useCallback(async () => {
    const items = await offlineQueueList();
    setOfflineQueue(items);
  }, []);

  useEffect(() => { loadOfflineQueue(); }, [loadOfflineQueue]);

  const flushOfflineQueue = useCallback(async () => {
    if (uploadingRef.current) return;
    const items = await offlineQueueList();
    if (items.length === 0) return;
    uploadingRef.current = true;
    setUploading(true);
    for (const item of items) {
      try {
        const form = new FormData();
        const file = new File([item.blob], item.name, { type: item.type });
        form.append("audio", file);
        if (item.label) form.append("label", item.label);
        const tid = getConfluenceUser();
        if (tid) form.append("therapeut_id", tid);
        const res = await apiFetch(`${getApiBase()}/recordings`, { method: "POST", body: form });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const newRec = await res.json();
        await offlineQueueRemove(item.id);
        setOfflineQueue(prev => prev.filter(q => q.id !== item.id));
        setRecordings(prev => [newRec, ...prev]);
        toast(`Offline-Aufnahme „${item.label || item.name}" hochgeladen`);
      } catch (e) {
        break;
      }
    }
    uploadingRef.current = false;
    setUploading(false);
  }, [toast]);

  const loadRecordings = useCallback(async () => {
    try {
      const tid = getConfluenceUser();
      const url = tid
        ? `${getApiBase()}/recordings?therapeut_id=${encodeURIComponent(tid)}`
        : `${getApiBase()}/recordings`;
      const res = await apiFetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      // Pending Labels flushen: Items die jetzt ready sind und ein lokales Label haben
      const flushPromises = data
        .filter(r => r.status === "ready" && _pendingLabels[r.id] !== undefined)
        .map(async r => {
          const label = _pendingLabels[r.id];
          delete _pendingLabels[r.id];
          try {
            await apiFetch(`${getApiBase()}/recordings/${r.id}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ label }),
            });
            r.label = label; // in-place für setRecordings unten
          } catch (e) { /* Label-Flush fehlgeschlagen — ignorieren */ }
        });
      await Promise.all(flushPromises);

      setRecordings(data);
      setError(null);
      flushOfflineQueue();
    } catch (e) {
      setError(null);
    } finally {
      setLoading(false);
    }
  }, [flushOfflineQueue]);

  useEffect(() => { loadRecordings(); }, [loadRecordings]);

  // Polling (30s) solange transcribing-Items vorhanden
  useEffect(() => {
    const hasPending = recordings.some(r => r.status === "uploading" || r.status === "transcribing");
    if (hasPending && !pollRef.current) {
      pollRef.current = setInterval(loadRecordings, 30000);
    } else if (!hasPending && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [recordings, loadRecordings]);

  // st-health-ok: Server wieder erreichbar → neu laden + offline-Queue leeren
  useEffect(() => {
    const handler = () => loadRecordings();
    window.addEventListener("st-health-ok", handler);
    return () => window.removeEventListener("st-health-ok", handler);
  }, [loadRecordings]);

  // Sofort-Upload: kein "Speichern"-Button, Datei geht direkt hoch
  function onRecorded(file) {
    submitRecording(file, "");
  }

  async function submitRecording(file, label) {
    if (!file) return;
    const tempId = "tmp-" + Date.now();
    setRecordings(prev => [{
      id: tempId, label: label || null, status: "uploading",
      created_at: new Date().toISOString(), duration_s: null,
      transcript: null, has_audio: true, _uploading: true,
    }, ...prev]);
    try {
      const form = new FormData();
      form.append("audio", file);
      if (label && label.trim()) form.append("label", label.trim());
      const tid = getConfluenceUser();
      if (tid) form.append("therapeut_id", tid);
      const res = await apiFetch(`${getApiBase()}/recordings`, { method: "POST", body: form });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const newRec = await res.json();
      setRecordings(prev => prev.map(r => r.id === tempId ? newRec : r));
    } catch (e) {
      setRecordings(prev => prev.filter(r => r.id !== tempId));
      try {
        await offlineQueueAdd(file, label);
        await loadOfflineQueue();
        toast("Server nicht erreichbar – Aufnahme lokal gespeichert. Upload erfolgt automatisch wenn der Server wieder erreichbar ist.");
      } catch (e2) {
        toast("Speichern fehlgeschlagen: " + e.message);
      }
    }
  }

  async function updateLabel(id, label) {
    if (String(id).startsWith("tmp-")) return;
    // Label sofort im State aktualisieren (optimistisch)
    setRecordings(prev => prev.map(r => r.id === id ? { ...r, label } : r));
    const rec = recordings.find(r => r.id === id);
    if (rec && (rec.status === "uploading" || rec.status === "transcribing")) {
      // Noch nicht ready → lokal merken, wird beim nächsten loadRecordings geflusht
      _pendingLabels[id] = label;
      return;
    }
    // Ready → sofort PATCH
    try {
      await apiFetch(`${getApiBase()}/recordings/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label }),
      });
    } catch (e) {
      toast("Label konnte nicht gespeichert werden");
    }
  }

  async function deleteRecording(id) {
    if (!window.confirm("Aufnahme unwiderruflich löschen?")) return;
    try {
      await apiFetch(`${getApiBase()}/recordings/${id}`, { method: "DELETE" });
      setRecordings(prev => prev.filter(r => r.id !== id));
      toast("Aufnahme gelöscht");
    } catch (e) {
      toast("Löschen fehlgeschlagen: " + e.message);
    }
  }

  async function retryRecording(id) {
    // Status sofort optimistisch setzen damit der Button verschwindet
    setRecordings(prev => prev.map(r => r.id === id ? { ...r, status: "uploading", error_msg: null } : r));
    try {
      const res = await apiFetch(`${getApiBase()}/recordings/${id}/retry`, { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      toast("Transkription wird erneut gestartet");
    } catch (e) {
      // Bei Fehler den vorherigen Status zurueckholen
      toast("Erneuter Versuch fehlgeschlagen: " + e.message);
      setRecordings(prev => prev.map(r => r.id === id ? { ...r, status: "error" } : r));
    }
  }

  const STATUS = {
    uploading:    { text: "Wird hochgeladen…",    color: "#c07000" },
    transcribing: { text: "Transkription läuft…", color: "#0060c0" },
    ready:        { text: "Bereit",               color: "#1a7a1a" },
    error:        { text: "Fehler",               color: "#c02020" },
  };

  function fmtDur(s) {
    if (!s) return "–";
    return `${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,"0")}`;
  }
  function fmtDat(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleDateString("de-DE",{day:"2-digit",month:"2-digit",year:"2-digit"})
      + " " + d.toLocaleTimeString("de-DE",{hour:"2-digit",minute:"2-digit"});
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Aufnahmen</div>
        <h2>Gesprächsaufzeichnungen</h2>
        <p>Aufnehmen, beschriften, verwalten. In P1–P4 direkt als Quelle wählen.</p>
      </div>
      <div className="page-body">
        <div className="workflow">

          <Card num="A" title="Neue Aufnahme" open={true}>
            <AudioRecorder onRecorded={onRecorded} onError={(msg) => toast(msg)} />
            <div style={{marginTop:12}}>
              <div style={{fontSize:11,fontWeight:600,letterSpacing:"0.06em",textTransform:"uppercase",color:"var(--st-text-soft)",marginBottom:6,textAlign:"center"}}>
                – oder Audiodatei hochladen –
              </div>
              {/* v19.7 S3: DnD-fähige Dropzone statt plain input. file bleibt null,
                  da P0 sofort hochlädt statt die Datei zu halten. */}
              <Dropzone
                label="Audiodatei wählen oder hierher ziehen"
                hint=".mp3 · .m4a · .wav · .ogg · .webm · .flac · .aac"
                accept=".mp3,.m4a,.wav,.ogg,.webm,.flac,.aac,audio/*"
                icon="&#128266;"
                file={null}
                onFile={(f) => {
                  if (!f) return;
                  // v19.9.1: sofortiges Feedback direkt an der Dropzone. Da die
                  // Datei nicht gehalten wird (file={null}, Sofort-Upload),
                  // war der einzige Hinweis der Eintrag in der Liste unten -
                  // leicht zu uebersehen.
                  toast(`Upload gestartet: ${f.name}`);
                  onRecorded(f);
                }}
              />
            </div>
            <div style={{fontSize:11,color:"var(--st-text-pale)",marginTop:8,textAlign:"center"}}>
              Aufnahmen werden sofort hochgeladen und erscheinen in der Liste unten.
            </div>
          </Card>

          <Card num="B" title="Aufnahmen" open={true}>
            {/* Offline-Queue */}
            {offlineQueue.length > 0 && (
              <div style={{marginBottom:12,padding:"10px 12px",background:"#fff8e1",border:"1px solid #f0c040",borderRadius:6}}>
                <div style={{fontSize:12,fontWeight:600,color:"#7a5800",marginBottom:6}}>
                  📵 {offlineQueue.length} Aufnahme{offlineQueue.length > 1 ? "n" : ""} lokal gespeichert (warten auf Upload)
                  {uploading && <span style={{marginLeft:8,fontWeight:400}}>Wird hochgeladen…</span>}
                </div>
                {offlineQueue.map(item => (
                  <div key={item.id} style={{display:"flex",alignItems:"center",gap:8,marginBottom:4,fontSize:12}}>
                    <span style={{flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{item.label || item.name}</span>
                    <span style={{color:"#999",flexShrink:0}}>{new Date(item.savedAt).toLocaleTimeString("de-DE",{hour:"2-digit",minute:"2-digit"})}</span>
                    <button style={{fontSize:11,padding:"1px 7px",border:"1px solid #ccc",borderRadius:3,background:"transparent",cursor:"pointer"}}
                      onClick={async () => { if (!window.confirm("Offline-Aufnahme löschen?")) return; await offlineQueueRemove(item.id); loadOfflineQueue(); }}>✕</button>
                  </div>
                ))}
                {!uploading && (
                  <button className="btn-secondary" style={{fontSize:11,padding:"3px 10px",marginTop:4}} onClick={flushOfflineQueue}>
                    Jetzt hochladen
                  </button>
                )}
              </div>
            )}
            {loading && <div className="p0-hint">Lade…</div>}
            {error   && <div className="upload-warn">{error}</div>}
            {!loading && recordings.length === 0 && offlineQueue.length === 0 && (
              <div className="p0-hint">Noch keine Aufnahmen vorhanden.</div>
            )}
            {recordings.map(r => {
              const st = STATUS[r.status] || { text: r.status, color: "#666" };
              const isTemp = String(r.id).startsWith("tmp-");
              return (
                <div key={r.id} style={{display:"flex",alignItems:"center",gap:10,padding:"10px 12px",marginBottom:6,background:"var(--card-bg)",border:"1px solid var(--border)",borderRadius:6,fontSize:13,opacity:isTemp?0.7:1}}>
                  <div style={{flex:1,minWidth:0}}>
                    {isTemp
                      ? <div style={{fontWeight:600,color:"var(--fg-muted)"}}>⏳ Wird hochgeladen…</div>
                      : <LabelEdit value={r.label || ""} placeholder="Beschriftung hinzufügen…" onSave={(v) => updateLabel(r.id, v)} />
                    }
                    <div style={{color:"var(--fg-muted)",fontSize:11,display:"flex",gap:10,marginTop:2}}>
                      <span>{fmtDat(r.created_at)}</span>
                      <span>{fmtDur(r.duration_s)}</span>
                      {!isTemp && <span style={{color:st.color,fontWeight:600}}>{st.text}</span>}
                      {r.error_msg && <span title={r.error_msg} style={{cursor:"help"}}>ⓘ</span>}
                    </div>
                  </div>
                  {!isTemp && r.has_audio !== false && (
                    <button type="button"
                      onClick={() => downloadViaApi(
                        `${getApiBase()}/recordings/${r.id}/download`,
                        `aufnahme-${r.id}.webm`,
                      )}
                      style={{padding:"4px 10px",fontSize:12,border:"1px solid var(--border)",borderRadius:4,background:"transparent",cursor:"pointer",textDecoration:"none",color:"var(--fg)"}}
                      title="Audio herunterladen (24h verfügbar)">⬇ Audio</button>
                  )}
                  {!isTemp && r.transcript && (
                    <button type="button"
                      onClick={() => downloadViaApi(
                        `${getApiBase()}/recordings/${r.id}/transcript`,
                        `transkript-${r.id}.txt`,
                      )}
                      style={{padding:"4px 10px",fontSize:12,border:"1px solid var(--border)",borderRadius:4,background:"transparent",cursor:"pointer",textDecoration:"none",color:"var(--fg)"}}
                      title="Transkript als .txt herunterladen">⬇ Transkript</button>
                  )}
                  {!isTemp && r.status === "error" && r.has_audio !== false && (
                    <button type="button"
                      onClick={() => retryRecording(r.id)}
                      style={{padding:"4px 10px",fontSize:12,border:"1px solid var(--st-blue, #0060c0)",borderRadius:4,background:"transparent",cursor:"pointer",color:"var(--st-blue, #0060c0)",fontWeight:600}}
                      title="Transkription erneut versuchen">↻ Erneut versuchen</button>
                  )}
                  {!isTemp && (
                    <button onClick={() => deleteRecording(r.id)}
                      style={{padding:"4px 10px",fontSize:13,border:"1px solid var(--st-red)",borderRadius:4,background:"transparent",cursor:"pointer",color:"var(--st-red)",fontWeight:700,lineHeight:1}}
                      title="Aufnahme löschen">✕</button>
                  )}
                </div>
              );
            })}
          </Card>

        </div>
      </div>
    </div>
  );
}

export { LabelEdit, P0 };
