// ────────────────────────────────────────────────────────────────────────────
// src/ui.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { apiFetch, getApiBase } from "./api.jsx";


function JobProgressBar({ jobId, onTerminal }) {
  const [p, setP] = useState({ progress: 0, progress_phase: "Starte...", progress_detail: "" });
  // Sprint Draft-Persistence Bugfix: onTerminal in ref ablegen, damit der
  // useEffect nicht bei jedem neuen Callback-Identitaet neu laeuft (sonst
  // wuerde die SSE bei jeder Parent-Rerender neu aufgebaut).
  const onTerminalRef = useRef(onTerminal);
  useEffect(() => { onTerminalRef.current = onTerminal; }, [onTerminal]);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    // Versuch 1: Server-Sent Events (live, kein Polling)
    const sseUrl = `${getApiBase()}/jobs/${jobId}/stream`;
    let es;
    try {
      es = new EventSource(sseUrl);
      es.onmessage = (e) => {
        if (cancelled) return;
        try {
          const d = JSON.parse(e.data);
          if (d.type === "progress") {
            setP({ progress: d.progress || 0, progress_phase: d.phase || "", progress_detail: d.detail || "" });
          } else if (d.type === "done" || d.type === "error" || d.type === "cancelled") {
            setP(prev => ({ ...prev, progress: d.type === "done" ? 100 : prev.progress, progress_phase: d.type === "done" ? "Fertig" : d.type === "error" ? "Fehler" : "Abgebrochen" }));
            es.close();
            // Parent benachrichtigen (z.B. P1: Detail-Dict neu laden)
            if (onTerminalRef.current) onTerminalRef.current(d.type);
          }
        } catch (_) {}
      };
      es.onerror = () => {
        // SSE fehlgeschlagen → Fallback auf Polling
        es.close();
        if (!cancelled) startPolling();
      };
    } catch (_) {
      // EventSource nicht verfuegbar → Polling
      startPolling();
    }

    // Fallback: Polling (alle 3s)
    function startPolling() {
      const tick = async () => {
        try {
          const r = await apiFetch(`${getApiBase()}/jobs/${jobId}`);
          const j = await r.json();
          if (cancelled) return;
          setP({ progress: j.progress || 0, progress_phase: j.progress_phase || "", progress_detail: j.progress_detail || "" });
          if (j.status === "done" || j.status === "error" || j.status === "cancelled") {
            // Auch im Polling-Fallback Parent benachrichtigen
            if (onTerminalRef.current) onTerminalRef.current(j.status);
            return;
          }
          setTimeout(tick, 3000);
        } catch { if (!cancelled) setTimeout(tick, 5000); }
      };
      tick();
    }

    return () => {
      cancelled = true;
      if (es) try { es.close(); } catch (_) {}
    };
  }, [jobId]);
  return (
    <div style={{margin:"12px 0"}}>
      <div style={{height:8, background:"var(--st-gray-bg)", borderRadius:4, overflow:"hidden"}}>
        <div style={{height:"100%", width:`${p.progress}%`, background:"var(--st-red)", transition:"width 0.4s ease-out"}}/>
      </div>
      <div style={{fontSize:12, color:"var(--st-text-soft)", marginTop:4, textAlign:"center"}}>
        {p.progress_phase} {p.progress_detail && `— ${p.progress_detail}`} ({p.progress}%)
      </div>
    </div>
  );
}

/** Prüft eine Datei gegen ein accept-Attribut (".pdf,image/*,…"). Leer = alles. */
function matchesAccept(file, accept) {
  if (!accept || !accept.trim()) return true;
  const name = (file.name || "").toLowerCase();
  const mime = (file.type || "").toLowerCase();
  return accept.split(",").some((raw) => {
    const a = raw.trim().toLowerCase();
    if (!a) return false;
    if (a.startsWith(".")) return name.endsWith(a);
    if (a.endsWith("/*")) return mime.startsWith(a.slice(0, -1));
    return mime === a;
  });
}

function Dropzone({ label, hint, accept, file, onFile, icon }) {
  const [drag, setDrag] = useState(false);
  const [warn, setWarn] = useState(null);
  // dragenter/dragleave feuern für jedes Kind-Element — Counter statt
  // Boolean verhindert Flackern des Highlights (v19.7 S2).
  const dragDepth = useRef(0);
  const warnTimer = useRef(null);
  useEffect(() => () => { if (warnTimer.current) clearTimeout(warnTimer.current); }, []);

  function showWarn(msg) {
    setWarn(msg);
    if (warnTimer.current) clearTimeout(warnTimer.current);
    warnTimer.current = setTimeout(() => setWarn(null), 4000);
  }

  function takeFile(f) {
    if (!f) return;
    if (!matchesAccept(f, accept)) {
      showWarn(`Dateityp nicht unterstützt (erwartet: ${accept})`);
      return;
    }
    setWarn(null);
    onFile(f);
  }

  function onDrop(e) {
    e.preventDefault();
    e.stopPropagation(); // nie zu Confluence hochbubblen lassen
    dragDepth.current = 0; setDrag(false);
    // Nur die erste Datei übernehmen (Single-File-API)
    takeFile(e.dataTransfer.files && e.dataTransfer.files[0]);
  }

  let cls = "dropzone";
  if (drag) cls += " drag";
  if (file) cls += " filled";

  return (
    <div className={cls}
      onDragEnter={(e) => { e.preventDefault(); dragDepth.current += 1; setDrag(true); }}
      onDragOver={(e) => { e.preventDefault(); }}
      onDragLeave={() => { dragDepth.current = Math.max(0, dragDepth.current - 1); if (dragDepth.current === 0) setDrag(false); }}
      onDrop={onDrop}
    >
      {file ? (
        <div className="dz-file">
          <span>{icon}</span>
          <span style={{ maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file.name}</span>
          <button className="dz-remove" onClick={(e) => { e.stopPropagation(); onFile(null); }}>&#215;</button>
          {warn && <div className="dz-hint" style={{ color: "var(--st-red)", fontWeight: 600, flexBasis: "100%" }}>{warn}</div>}
        </div>
      ) : (
        <>
          <div className="dz-icon">{icon}</div>
          <div className="dz-label">{label}</div>
          {hint && <div className="dz-hint">{hint}</div>}
          {warn && <div className="dz-hint" style={{ color: "var(--st-red)", fontWeight: 600 }}>{warn}</div>}
          <input type="file" accept={accept}
            onChange={(e) => {
              takeFile(e.target.files && e.target.files[0]);
              e.target.value = ""; // gleiche Datei erneut wählbar
            }} />
        </>
      )}
    </div>
  );
}

/**
 * InputTabs – kompakte Tab-Navigation für alternative Eingabemodi.
 * Kinder bekommen den aktiven Tab-ID als Argument (render-prop).
 * Beispiel:
 *   <InputTabs tabs={[{id:"a",icon:"🎙",label:"Audio"}, ...]}>
 *     {(active) => active === "a" && <Dropzone ... />}
 *   </InputTabs>
 */
/**
 * ModelSelector – kompakter Inline-Selektor für das LLM-Modell.
 * Lädt verfügbare Modelle vom Backend und zeigt sie als Buttons an.
 * Props: model (aktiver Wert), onChange (Callback), apiBase
 */
function ModelSelector({ model, onChange, apiBase }) {
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(false);
  const [defaultModel, setDefaultModel] = useState("");

  useEffect(() => {
    setLoading(true);
    apiFetch(`${apiBase}/models`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.models?.length) {
          setModels(data.models);
          setDefaultModel(data.default || "");
          // Wenn noch kein Modell gewählt, Default setzen
          if (!model && data.default) onChange(data.default);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [apiBase]);

  if (loading || models.length === 0) return null;

  // Effektiv aktives Modell: explizite Wahl > localStorage-Default > Server-Default
  const activeModel = model || defaultModel;

  return (
    <div style={{display:"flex", alignItems:"center", gap:4, flexWrap:"wrap"}}>
      <span style={{fontSize:11, fontWeight:600, color:"var(--st-text-soft)",
        textTransform:"uppercase", letterSpacing:"0.06em", marginRight:2}}>Modell</span>
      {models.map(m => {
        const isActive = activeModel === m.name || (!activeModel && m.is_default);
        const shortName = m.name.replace(/:latest$/, "");
        return (
          <button key={m.name} onClick={() => onChange(m.name)} title={m.name}
            style={{
              padding:"3px 8px", borderRadius:3, cursor:"pointer",
              fontSize:11, fontWeight: isActive ? 700 : 400,
              background: isActive ? "var(--st-red)" : "var(--st-gray-light)",
              color: isActive ? "white" : "var(--st-text-soft)",
              border: isActive ? "1px solid var(--st-red)" : "1px solid var(--st-gray-border)",
              transition:"all 0.12s", whiteSpace:"nowrap",
            }}>
            {shortName}
            {m.size_gb ? <span style={{opacity:0.75, fontSize:10}}> {m.size_gb}G</span> : null}
          </button>
        );
      })}
    </div>
  );
}

function InputTabs({ tabs, children, defaultTab }) {
  const [active, setActive] = useState(defaultTab || tabs[0]?.id);
  return (
    <div className="input-tabs-wrap">
      <div className="input-tabs-bar">
        {tabs.map(t => (
          <button
            key={t.id}
            className={"input-tab" + (active === t.id ? " active" : "")}
            onClick={() => setActive(t.id)}
            type="button"
          >
            <span className="input-tab-icon">{t.icon}</span>
            <span className="input-tab-label">{t.label}</span>
          </button>
        ))}
      </div>
      <div className="input-tabs-body">
        {children(active)}
      </div>
    </div>
  );
}

function Card({ num, title, badge, open: defaultOpen = true, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="step-card">
      <div className={"step-head" + (open ? " open" : "")} onClick={() => setOpen(!open)}>
        <div className="step-num">{num}</div>
        <div className="step-label">{title}</div>
        {badge && (
          <span className={"step-pill " + (badge === "opt" ? "pill-opt" : "pill-req")}>
            {badge === "opt" ? "Optional" : "Erforderlich"}
          </span>
        )}
        <span className={"step-caret" + (open ? " open" : "")}>&#9660;</span>
      </div>
      {open && <div className="step-body">{children}</div>}
    </div>
  );
}

function PromptEditor({ value, onChange, def }) {
  return (
    <div className="prompt-box">
      <div className="prompt-bar">
        <span className="prompt-bar-label">Prompt-Vorlage</span>
        <button className="btn-xs" onClick={() => onChange(def)}>Zuruecksetzen</button>
      </div>
      <textarea rows={8} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function Output({ text, loading, jobId, tabs, activeTab, onTab, onCopy, onDownload, extraButtons = [], warn = null, onTerminal = null }) {
  const empty = !text && !loading;
  return (
    <div className="output-card">
      <div className="output-head">
        <span className="output-title">Ergebnis</span>
        <div className="output-btns">
          {text && <button className="btn-out" onClick={onCopy}>Kopieren</button>}
          {text && onDownload && <button className="btn-out" onClick={onDownload}>Download</button>}
          {extraButtons.map((btn, i) => (
            <button key={i} className="btn-out" onClick={btn.onClick}>{btn.label}</button>
          ))}
        </div>
      </div>
      {tabs && (
        <div className="output-tabs">
          {tabs.map((t) => (
            <div key={t} className={"otab" + (activeTab === t ? " on" : "")} onClick={() => onTab(t)}>{t}</div>
          ))}
        </div>
      )}
      <div className={"output-text" + (empty ? " empty" : "")}>
        {loading
          ? (jobId ? <JobProgressBar jobId={jobId} onTerminal={onTerminal} /> : "Wird generiert ...")
          : text
            ? text
            : warn
              ? <span style={{color:"var(--st-error,#b00)",fontStyle:"normal",fontWeight:500}}>{warn}</span>
              : "Der generierte Text erscheint hier."}
      </div>
    </div>
  );
}

function Tags({ list, onChange }) {
  const [val, setVal] = useState("");
  function add() {
    const v = val.trim();
    if (v && !list.includes(v)) { onChange([...list, v]); setVal(""); }
  }
  return (
    <div className="tag-wrap">
      {list.map((d) => (
        <span key={d} className="tag">
          {d}
          <button className="tag-x" onClick={() => onChange(list.filter((x) => x !== d))}>&#215;</button>
        </span>
      ))}
      <input className="tag-input" placeholder="ICD-Code + Enter ..."
        value={val} onChange={(e) => setVal(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } }}
        onBlur={add}
      />
    </div>
  );
}



// ── JobModelPicker ────────────────────────────────────────────────────────────
// Modellwahl direkt beim Job-Generieren (2026-07-03). Zeigt die verfuegbaren
// Modelle mit FREUNDLICHEN Familiennamen (Gemma/Mistral/Qwen - keine
// technischen Tags) als Pill-Auswahl und markiert die pro Workflow empfohlene
// Familie mit einem "Empfehlung"-Chip. Routing-Default (Modellvergleich
// Runde 1, 2026-07): Gemma fuer klinische Dokumente (hypnosystemische
// Sprache), Mistral fuer Kassenkommunikation (knapp, klar). Die Wahl in den
// Einstellungen (ModelSelector) bleibt der globale Fallback.
const JOB_MODEL_RECOMMENDATION = {
  dokumentation:      "gemma",
  anamnese:           "mistral",
  entlassbericht:     "gemma",
  akutantrag:         "mistral",
  verlaengerung:      "mistral",
  folgeverlaengerung: "mistral",
};

function modelFamily(name) {
  const t = (name || "").toLowerCase();
  if (t.startsWith("gemma"))   return "gemma";
  if (t.startsWith("mistral")) return "mistral";
  if (t.startsWith("qwen"))    return "qwen";
  return t.split(/[:\/]/)[0];
}

// Freundliches Label: Familienname; bei mehreren Modellen derselben Familie
// wird die Hauptversion angehaengt ("Gemma 3" / "Gemma 4") - weiterhin ohne
// technische Tags wie ":27b-q4".
function friendlyModelLabel(name, allNames) {
  const fam = modelFamily(name);
  const pretty = fam.charAt(0).toUpperCase() + fam.slice(1);
  const siblings = allNames.filter(n => modelFamily(n) === fam);
  if (siblings.length <= 1) return pretty;
  const ver = (name.match(/(\d+(?:\.\d+)?)/) || [])[1];
  return ver ? `${pretty} ${ver}` : pretty;
}

function JobModelPicker({ workflow, value, onChange }) {
  const [models, setModels] = useState([]);
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    let alive = true;
    apiFetch(`${getApiBase()}/models`)
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (!alive || !data?.models?.length) return;
        setModels(data.models);
        // Default: empfohlenes Modell des Workflows, falls verfuegbar.
        // Bei mehreren Kandidaten derselben Familie gewinnt die hoechste
        // Versionsnummer. Kein Kandidat -> Wahl bleibt leer (globaler
        // Fallback greift beim Submit: jobModel || model).
        if (!value) {
          const fam = JOB_MODEL_RECOMMENDATION[workflow];
          const candidates = data.models
            .map(m => m.name)
            .filter(n => modelFamily(n) === fam)
            .sort((a, b) => {
              const va = parseFloat((a.match(/(\d+(?:\.\d+)?)/) || [0, 0])[1]);
              const vb = parseFloat((b.match(/(\d+(?:\.\d+)?)/) || [0, 0])[1]);
              return vb - va;
            });
          if (candidates.length) onChange(candidates[0]);
        }
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [workflow]);

  // Klick ausserhalb schliesst das Dropdown
  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  if (models.length < 2) return null;   // eine Option = keine Wahl noetig

  const names = models.map(m => m.name);
  const recFam = JOB_MODEL_RECOMMENDATION[workflow];
  // Das konkret empfohlene Modell (hoechste Version der empfohlenen Familie)
  const recModel = names
    .filter(n => modelFamily(n) === recFam)
    .sort((a, b) => {
      const va = parseFloat((a.match(/(\d+(?:\.\d+)?)/) || [0, 0])[1]);
      const vb = parseFloat((b.match(/(\d+(?:\.\d+)?)/) || [0, 0])[1]);
      return vb - va;
    })[0];

  const selectedLabel = value ? friendlyModelLabel(value, names) : "Modell wählen";

  // Empfehlung-Chip (farbig, runde Ecken) - wiederverwendbar
  const Chip = ({ light }) => (
    <span style={{
      fontSize:9, fontWeight:700, textTransform:"uppercase",
      letterSpacing:"0.05em", padding:"1px 6px", borderRadius:10,
      background: light ? "rgba(255,255,255,0.25)" : "var(--st-teal, #2a7d7d)",
      color:"white", whiteSpace:"nowrap",
    }}>Empfehlung</span>
  );

  return (
    <div style={{display:"flex", alignItems:"center", gap:8, margin:"8px 0 6px"}}>
      <span style={{fontSize:11, fontWeight:600, color:"var(--st-text-soft)",
        textTransform:"uppercase", letterSpacing:"0.06em"}}>Modell</span>

      <div ref={boxRef} style={{position:"relative", minWidth:180}}>
        {/* Select-Feld (geschlossen) */}
        <button type="button" onClick={() => setOpen(o => !o)}
          style={{
            width:"100%", display:"flex", alignItems:"center", justifyContent:"space-between",
            gap:8, padding:"5px 10px", borderRadius:4, cursor:"pointer",
            fontSize:12, fontWeight:500, textAlign:"left",
            background:"white", color:"var(--st-text-soft)",
            border:"1px solid var(--st-gray-border)",
          }}>
          <span style={{display:"inline-flex", alignItems:"center", gap:6}}>
            {selectedLabel}
            {value && value === recModel && <Chip />}
          </span>
          {/* Chevron */}
          <span style={{fontSize:9, opacity:0.6, transform: open ? "rotate(180deg)" : "none",
            transition:"transform 0.15s"}}>▼</span>
        </button>

        {/* Options-Liste (offen) */}
        {open && (
          <div style={{
            position:"absolute", top:"calc(100% + 2px)", left:0, right:0, zIndex:50,
            background:"white", border:"1px solid var(--st-gray-border)", borderRadius:4,
            boxShadow:"0 4px 12px rgba(0,0,0,0.12)", overflow:"hidden",
          }}>
            {models.map(m => {
              const isActive = value === m.name;
              const isRec = m.name === recModel;
              return (
                <button key={m.name} type="button" title={m.name}
                  onClick={() => { onChange(m.name); setOpen(false); }}
                  style={{
                    width:"100%", display:"flex", alignItems:"center", justifyContent:"space-between",
                    gap:8, padding:"7px 10px", cursor:"pointer", fontSize:12, textAlign:"left",
                    border:"none", borderBottom:"1px solid var(--st-gray-light)",
                    fontWeight: isActive ? 700 : 400,
                    background: isActive ? "var(--st-red)" : "white",
                    color: isActive ? "white" : "var(--st-text-soft)",
                  }}>
                  <span>{friendlyModelLabel(m.name, names)}</span>
                  {isRec && <Chip light={isActive} />}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ── copyFormatted (v19.5.6) ────────────────────────────────────────────────
// Problem: navigator.clipboard.writeText() legt NUR text/plain in die
// Zwischenablage. Rich-Text-Ziele (Confluence-Editor, Word, Outlook)
// kollabieren beim Einfuegen von text/plain die \n\n-Absatztrennungen zu
// einem einzigen Textblock - Ueberschriften kleben dann am Folgetext.
// Loesung: zusaetzlich eine text/html-Variante mitschreiben, in der jeder
// \n\n-Absatz ein eigenes <p> ist und Abschnitts-Ueberschriften (kurze
// Zeile ohne Satz-Endzeichen) als <p><strong> ausgezeichnet sind.
// text/plain bleibt unveraendert erhalten (fuer <textarea>-Ziele).
// Fallback auf writeText, wenn ClipboardItem nicht verfuegbar ist
// (aeltere Firefox-Versionen) oder write() scheitert.

function _escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Heuristik: eigenstaendiger Absatz aus EINER Zeile, max. 6 Woerter,
// endet nicht auf Satzzeichen → Abschnitts-Ueberschrift
// ("Auftragsklärung", "Relevante Gesprächsinhalte", ...).
function _isHeading(par) {
  if (par.includes("\n")) return false;
  const words = par.trim().split(/\s+/);
  if (words.length === 0 || words.length > 6) return false;
  return !/[.:;!?]$/.test(par.trim());
}

function _textToHtml(text) {
  const paragraphs = text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  return paragraphs
    .map((p) => {
      const inner = _escapeHtml(p).replace(/\n/g, "<br/>");
      return _isHeading(p)
        ? `<p><strong>${inner}</strong></p>`
        : `<p>${inner}</p>`;
    })
    .join("\n");
}

async function copyFormatted(text) {
  const t = text || "";
  try {
    if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
      const item = new ClipboardItem({
        "text/plain": new Blob([t], { type: "text/plain" }),
        "text/html":  new Blob([_textToHtml(t)], { type: "text/html" }),
      });
      await navigator.clipboard.write([item]);
      return;
    }
  } catch (e) {
    // z.B. Permission-Fehler im iframe → plain-Fallback
    console.warn("copyFormatted: HTML-Clipboard fehlgeschlagen, Fallback auf writeText", e);
  }
  await navigator.clipboard.writeText(t);
}

export { JobProgressBar, Dropzone, ModelSelector, InputTabs, Card, PromptEditor, Output, Tags, JobModelPicker, copyFormatted };
