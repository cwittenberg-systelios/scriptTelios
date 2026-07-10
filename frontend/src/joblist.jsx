// ────────────────────────────────────────────────────────────────────────────
// src/joblist.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { downloadTranscript } from "./api.jsx";
import { RepairBundle, ResultVersionsTabs } from "./qa.jsx";
import { Output, copyFormatted } from "./ui.jsx";


// ══════════════════════════════════════════════════════════════════════════
// Sprint B (Multi-Job-Liste P1): Job-Liste und Detail-Pane
// ══════════════════════════════════════════════════════════════════════════

// Status-Farben fuer Listen-Indikatoren. Gleicher Look wie Recording-Stati,
// aber mit Job-spezifischen Stati (pending/running statt uploading/transcribing).
const JOB_STATUS_STYLE = {
  pending:   { dot: "#999",     label: "Wartet",   sub: "var(--st-text-soft)" },
  running:   { dot: "#185FA5",  label: "Läuft",    sub: "#185FA5"             },
  done:      { dot: "#2d7a3a",  label: "Fertig",   sub: "var(--st-text-soft)" },
  cancelled: { dot: "#999",     label: "Abgebrochen", sub: "var(--st-text-soft)" },
  error:     { dot: "#c02020",  label: "Fehler",   sub: "#c02020"             },
};

// Sectioning analog zur Aufzeichnungs-Pattern: Aktiv / Heute / Gestern / Älter.
function _sectionForJob(j, todayStart, yesterdayStart) {
  if (j.status === "pending" || j.status === "running") return "Aktiv";
  const t = j.created_at ? new Date(j.created_at).getTime() : 0;
  if (t >= todayStart) return "Heute";
  if (t >= yesterdayStart) return "Gestern";
  return "Älter";
}

function _fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const today = new Date(); today.setHours(0,0,0,0);
  // Heute: HH:MM, sonst TT.MM.
  if (d.getTime() >= today.getTime()) {
    return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
}

function JobListPane({
  jobs, drafts, selected, onSelect, onDelete, onCancel, onDeleteDraft,
  loading, error,
}) {
  const [olderExpanded, setOlderExpanded] = useState(false);

  // Heute = lokale Mitternacht; Gestern = 24h davor
  const todayStart = useMemo(() => {
    const d = new Date(); d.setHours(0,0,0,0); return d.getTime();
  }, [jobs]);
  const yesterdayStart = todayStart - 24 * 60 * 60 * 1000;

  const grouped = useMemo(() => {
    const g = { Aktiv: [], Heute: [], Gestern: [], Älter: [] };
    for (const j of jobs) {
      const s = _sectionForJob(j, todayStart, yesterdayStart);
      g[s].push(j);
    }
    return g;
  }, [jobs, todayStart, yesterdayStart]);

  function isSelectedJob(j) {
    return selected && selected.type === "job" && selected.id === j.job_id;
  }
  function isSelectedDraft(d) {
    return selected && selected.type === "draft" && selected.id === d.id;
  }

  function renderItem(j) {
    const sel = isSelectedJob(j);
    const ss = JOB_STATUS_STYLE[j.status] || JOB_STATUS_STYLE.pending;
    const isTerminal = j.status === "done" || j.status === "error" || j.status === "cancelled";
    const isLive     = j.status === "pending" || j.status === "running";
    const subStatus  = isLive
      ? (j.progress_phase || ss.label) + (j.progress > 0 ? ` · ${j.progress}%` : "")
      : ss.label;
    const label      = j.patient_kuerzel || `Gespräch ${_fmtTime(j.created_at)}`;
    return (
      <div key={j.job_id}
           onClick={() => onSelect({ type: "job", id: j.job_id })}
           style={{
             display:"flex", flexDirection:"column", gap:2,
             padding:"8px 10px", borderRadius:4, cursor:"pointer",
             background: sel ? "var(--st-red-pale)" : "transparent",
             borderLeft: sel ? "3px solid var(--st-red)" : "3px solid transparent",
             marginBottom: 2,
           }}>
        <div style={{display:"flex", alignItems:"center", gap:6}}>
          <span style={{width:6, height:6, borderRadius:"50%", background: ss.dot, flexShrink:0}} />
          <span style={{fontSize:13, fontWeight: sel ? 600 : 500,
                        color:"var(--st-text)", overflow:"hidden",
                        textOverflow:"ellipsis", whiteSpace:"nowrap"}}>
            {label}
          </span>
          <span style={{fontSize:11, color:"var(--st-text-pale)", marginLeft:"auto", flexShrink:0}}>
            {_fmtTime(j.created_at)}
          </span>
        </div>
        <div style={{display:"flex", alignItems:"center", gap:6, paddingLeft:12}}>
          <span style={{fontSize:11, color: ss.sub, flex:1,
                        overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap"}}>
            {subStatus}
          </span>
          {isTerminal && (
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(j.job_id, label); }}
              title="Gespräch endgültig löschen"
              style={{
                padding:"0 4px", fontSize:11, border:"none", background:"transparent",
                color:"var(--st-text-pale)", cursor:"pointer", flexShrink:0,
              }}>✕</button>
          )}
          {isLive && (
            <button
              onClick={(e) => { e.stopPropagation(); onCancel(j.job_id); }}
              title="Job abbrechen"
              style={{
                padding:"0 4px", fontSize:11, border:"none", background:"transparent",
                color:"var(--st-red)", cursor:"pointer", flexShrink:0,
              }}>✕</button>
          )}
        </div>
      </div>
    );
  }

  // Entwurfs-Item — zwei Darstellungen:
  //  - leer:    grosser primaerer Button (rot, weiss, "+ Neues Gespräch")
  //             Ersetzt den frueheren Top-Button: ein Entwurf IST der Button.
  //  - gefuellt: normaler Listen-Eintrag mit Kuerzel-Label und ×-Verwerfen
  function renderDraft(d) {
    const sel = isSelectedDraft(d);
    const k = (d.kuerzel || "").trim();
    const hasContent = !!(d.audio || d.txtFile || d.text || d.bullets || k);

    if (!hasContent) {
      // Button-Modus: leerer Entwurf = primaerer Action-Button
      return (
        <button key={d.id}
                onClick={() => onSelect({ type: "draft", id: d.id })}
                style={{
                  display:"flex", alignItems:"center", justifyContent:"center", gap:6,
                  padding:"10px 12px", fontSize:13, fontWeight:600,
                  background: sel ? "var(--st-red)" : "var(--st-red)",
                  color:"white", border:"none", borderRadius:4,
                  cursor:"pointer", marginBottom:6,
                  outline: sel ? "2px solid rgba(255,255,255,0.4)" : "none",
                  outlineOffset: sel ? "-4px" : 0,
                }}
                title={sel ? "Aktuell ausgewählt" : "Neues Gespräch starten"}>
          + Neues Gespräch
        </button>
      );
    }

    // Gefuellter Entwurf: Label aus Kuerzel + Geschlecht
    let label = "Neuer Entwurf";
    if (k) {
      const kn = k.replace(/\.?$/, ".");
      if (d.geschlecht === "w")      label = `Frau ${kn}`;
      else if (d.geschlecht === "m") label = `Herr ${kn}`;
      else                            label = kn;
    }
    return (
      <div key={d.id}
           onClick={() => onSelect({ type: "draft", id: d.id })}
           style={{
             display:"flex", flexDirection:"column", gap:2,
             padding:"8px 10px", borderRadius:4, cursor:"pointer",
             background: sel ? "var(--st-red-pale)" : "transparent",
             borderLeft: sel ? "3px solid var(--st-red)" : "3px solid transparent",
             marginBottom: 2,
           }}>
        <div style={{display:"flex", alignItems:"center", gap:6}}>
          <span style={{width:14, fontSize:11, color:"var(--st-text-pale)", flexShrink:0, lineHeight:1}}>✎</span>
          <span style={{fontSize:13, fontWeight: sel ? 600 : 500,
                        color:"var(--st-text)", overflow:"hidden",
                        textOverflow:"ellipsis", whiteSpace:"nowrap", flex:1}}>
            {label}
          </span>
          <button
            onClick={(e) => { e.stopPropagation(); onDeleteDraft(d.id); }}
            title="Entwurf verwerfen (Felder zurücksetzen)"
            style={{padding:"0 4px", fontSize:11, border:"none", background:"transparent",
                    color:"var(--st-text-pale)", cursor:"pointer", flexShrink:0}}>✕</button>
        </div>
      </div>
    );
  }

  function renderSection(name, items, expandable = false, itemRenderer = renderItem) {
    if (items.length === 0) return null;
    if (expandable && !olderExpanded) {
      return (
        <div style={{margin:"8px 0 2px"}}>
          <button onClick={() => setOlderExpanded(true)}
                  style={{width:"100%", textAlign:"left", padding:"4px 10px", fontSize:10,
                          fontWeight:600, letterSpacing:"0.08em", textTransform:"uppercase",
                          color:"var(--st-text-soft)", background:"transparent",
                          border:"none", cursor:"pointer"}}>
            {name} ({items.length}) ▸
          </button>
        </div>
      );
    }
    return (
      <div>
        <div style={{padding:"8px 4px 2px", fontSize:10, fontWeight:600,
                     letterSpacing:"0.08em", textTransform:"uppercase",
                     color:"var(--st-text-soft)"}}>
          {name}
        </div>
        {items.map(itemRenderer)}
      </div>
    );
  }

  return (
    <div style={{
      background:"var(--st-white)", border:"1px solid var(--st-gray-mid)",
      borderRadius:5, padding:10, display:"flex", flexDirection:"column",
      gap:2, minHeight:300,
    }}>
      {/* Sprint B v2: kein Top-"+ Neues Gespräch"-Button mehr.
          Stattdessen ist der leere Entwurf in der Entwürfe-Sektion selbst
          als Button gestylt - siehe renderDraft(). Vorteil: kein redundanter
          UI-Pfad ("Button anlegen UND leeren Entwurf in der Liste sehen"). */}

      {loading && jobs.length === 0 && drafts.length === 0 && (
        <div style={{padding:"12px 8px", fontSize:12, color:"var(--st-text-pale)", textAlign:"center"}}>
          Lade…
        </div>
      )}
      {error && (
        <div style={{padding:"6px 8px", fontSize:12, color:"#c02020"}}>
          {error}
        </div>
      )}

      {renderSection("Entwürfe", drafts, false, renderDraft)}
      {renderSection("Aktiv",    grouped.Aktiv)}
      {renderSection("Heute",    grouped.Heute)}
      {renderSection("Gestern",  grouped.Gestern)}
      {renderSection("Älter",    grouped.Älter, true)}
    </div>
  );
}


// Detail-Pane fuer einen ausgewaehlten (laufenden oder fertigen) Job.
// Wenn der Job laeuft, wird der Inline-Progress vom Output-Component
// uebernommen (loading=true + jobId fuer SSE). Bei terminaler Job-Anzeige
// wird der finale Text statisch angezeigt; RepairBundle bleibt funktional.
function JobDetailPane({ job, jobOps, jobState, toast, onCancel, onDelete, onBack, onTerminal }) {
  // Output erwartet `text`, `loading`, `jobId`. Bei laufendem Job: loading=true
  // damit der SSE-Progress greift; bei terminalem Job: loading=false.
  const isLive    = job && (job.status === "pending" || job.status === "running");
  const isError   = job && job.status === "error";
  const cancelled = job && job.status === "cancelled";
  const showText  = jobState.hasRepair ? jobState.text : (job?.result_text || "");
  const label     = job?.patient_kuerzel || `Gespräch ${_fmtTime(job?.created_at)}`;

  return (
    <div className="workflow">
      <div style={{
        display:"flex", alignItems:"center", gap:10, padding:"10px 14px",
        background:"var(--st-cream)", border:"1px solid var(--st-gray-mid)",
        borderRadius:5,
      }}>
        <button onClick={onBack}
                style={{padding:"4px 10px", fontSize:12, border:"1px solid var(--st-gray-border)",
                        borderRadius:3, background:"transparent", cursor:"pointer",
                        color:"var(--st-text-soft)"}}>
          ← Zurück
        </button>
        <div style={{fontSize:13, fontWeight:600}}>{label}</div>
        <span style={{
          marginLeft:6, fontSize:11, padding:"2px 6px", borderRadius:3,
          background: (JOB_STATUS_STYLE[job?.status] || JOB_STATUS_STYLE.pending).dot + "22",
          color:      (JOB_STATUS_STYLE[job?.status] || JOB_STATUS_STYLE.pending).dot,
          fontWeight:600,
        }}>
          {(JOB_STATUS_STYLE[job?.status] || {}).label || job?.status}
        </span>
        <div style={{marginLeft:"auto", display:"flex", gap:6}}>
          {isLive && (
            <button onClick={onCancel}
                    style={{padding:"4px 10px", fontSize:12, border:"1px solid var(--st-red)",
                            borderRadius:3, background:"transparent", color:"var(--st-red)",
                            cursor:"pointer", fontWeight:600}}>
              ✕ Abbrechen
            </button>
          )}
          {!isLive && (
            <button onClick={onDelete}
                    style={{padding:"4px 10px", fontSize:12, border:"1px solid var(--st-gray-border)",
                            borderRadius:3, background:"transparent", color:"var(--st-text-soft)",
                            cursor:"pointer"}}>
              ✕ Löschen
            </button>
          )}
        </div>
      </div>

      {isError && job.error_msg && (
        <div style={{padding:"10px 14px", background:"#fdecec", border:"1px solid #f0b0b0",
                     borderRadius:5, fontSize:13, color:"#c02020"}}>
          <strong>Fehler:</strong> {job.error_msg}
        </div>
      )}
      {cancelled && (
        <div style={{padding:"10px 14px", background:"var(--st-gray-light)",
                     border:"1px solid var(--st-gray-border)", borderRadius:5,
                     fontSize:13, color:"var(--st-text-soft)"}}>
          Abgebrochen{showText ? " – generierter Text bis zum Abbruch:" : "."}
        </div>
      )}

      <ResultVersionsTabs
        hasRepair={jobState.hasRepair}
        active={jobState.activeVersion}
        onChange={jobOps.setActiveVersion}
        disabled={jobState.repairBusy}
      />
      <Output text={showText} loading={isLive} jobId={job?.job_id}
        onTerminal={onTerminal}
        onCopy={() => { copyFormatted(showText); toast("In Zwischenablage kopiert"); }}
        extraButtons={job?.has_transcript ? [
          { label: "Transkript ↓", onClick: () => downloadTranscript(job.job_id) }
        ] : []} />

      <RepairBundle job={jobState} ops={jobOps} toast={toast} />
    </div>
  );
}

export { JOB_STATUS_STYLE, _sectionForJob, _fmtTime, JobListPane, JobDetailPane };
