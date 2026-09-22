// ────────────────────────────────────────────────────────────────────────────
// src/panels/P6.jsx — v19.18 (PX): ISM-Fragebogen-Generierung.
//
// Erstellt aus einem P0-Gespraech einen individualisierten ISM-Fragebogen
// (6-Faktoren-Struktur, Slider-Items mit Pol-Labels) fuer das SNS
// (Synergetisches Navigationssystem, G. Schiepek).
//
// Inputs:
//   A (req)  Gespraechsauswahl aus P0 (AudioInput, Gate auf __p0recording)
//   B (opt)  Einzelne Fragen/Themen (Stichpunkte -> bullets)
//   C (opt)  Prompt/Modell (JobModelPicker + PromptEditor, Default P_ISM)
//   Action-Bar: SNS-Kennung (req, wird XML-Fragebogenname) + Itemanzahl (4-12)
//
// Output:
//   Editierbare Vorschau (Begruessung/Verabschiedung + Items gruppiert nach
//   Faktor, Slider-Optik mit min/max-Labels) + "XML kopieren" / "XML
//   herunterladen". Das XML rendert der Backend-Endpoint POST /api/ism/xml
//   aus dem EDITIERTEN Zustand (Single-Source-Renderer, D5).
//
// Backend-Workflow: "ism_fragebogen" (result_text = JSON, siehe
// app/services/ism.py).
// ────────────────────────────────────────────────────────────────────────────
import { useState, useMemo, useRef } from "react";
import { apiFetch, getApiBase } from "../api.js";
import { confirmExportText, exportNeedsConfirm, issuesForItem, useIsmLiveCheck } from "../ism-check.jsx";
import { QualityCheckPanel } from "../qa.jsx";
import { AudioInput } from "../audio.jsx";
import { useDraftCache } from "../hooks.jsx";
import { P_ISM } from "../prompt-defaults.jsx";
import { friendlyError } from "../shared.js";
import { Card, FeedbackButton, JobModelPicker, JobProgressBar, PromptEditor } from "../ui.jsx";
import { useWorkflowRun, WorkflowActionBar } from "../workflow-run.jsx";


// Faktoren-Anzeige (Spiegel von backend ISM_FAKTOREN - nur Darstellungsdaten,
// die fachliche Wahrheit inkl. XML-Beschreibungen liegt im Backend).
const ISM_FAKTOR_LABELS = {
  0: "I · Zielerleben",
  1: "II · Ressourcen",
  2: "III · Hindernisse",
  3: "IV · Hilfreiche Auswirkungen",
  4: "V · Herausfordernde Auswirkungen",
  5: "VI · Utilisierung",
};

const P6_DRAFT_DEFAULT = {
  themen: "", prompt: P_ISM, kennung: "", nItems: 6,
};

// ── Editierbare Item-Zeile mit Slider-Vorschau ──────────────────────────────
// Kleines Papierkorb-Icon (inline SVG - Codebase nutzt keine Icon-Lib).
function TrashIcon({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="2" strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  );
}

function IsmItemEditor({ item, index, onChange, onDelete, onBlur = null, issues = [], focused = false }) {
  const set = (patch) => onChange(index, patch);
  // v19.29: Markierung, wenn der Live-QC auf dieses Item zeigt.
  const sev = issues.some((i) => i.severity === "critical") ? "critical"
    : issues.some((i) => i.severity === "warning") ? "warning"
    : issues.length ? "info" : null;
  const cls = ["ism-item",
    sev && sev !== "info" ? "ism-item-flagged" : "",
    sev === "critical" ? "ism-item-critical" : "",
    focused ? "ism-item-focus" : ""].filter(Boolean).join(" ");
  const blur = () => onBlur && onBlur();
  function confirmDelete() {
    const kurz = (item.frage || "").trim();
    const label = kurz.length > 60 ? kurz.slice(0, 60) + "\u2026" : kurz || "(ohne Frage)";
    if (!window.confirm(`Item entfernen?\n\n\u201E${label}\u201C`)) return;
    onDelete(index);
  }
  return (
    <div id={`ism-item-${index}`} className={cls} data-testid={`ism-item-${index}`} style={{
      border: "1px solid var(--st-gray-border)", borderRadius: 6,
      padding: "10px 12px", marginBottom: 10, background: "var(--st-bg)",
      position: "relative",
    }}>
      {sev && sev !== "info" && (
        <span className="ism-item-marker" title={issues.map((i) => i.message).join("\n")}>
          {issues.length} {issues.length === 1 ? "Hinweis" : "Hinweise"}
        </span>
      )}
      <button
        onClick={confirmDelete}
        title="Item entfernen"
        aria-label="Item entfernen"
        style={{ position: "absolute", top: 6, right: 6, border: "none",
                 background: "none", cursor: "pointer",
                 color: "var(--st-text-soft)", padding: 2, lineHeight: 1,
                 display: "flex", alignItems: "center" }}
        onMouseEnter={(e) => (e.currentTarget.style.color = "#c0392b")}
        onMouseLeave={(e) => (e.currentTarget.style.color = "var(--st-text-soft)")}
      >
        <TrashIcon />
      </button>
      <textarea
        rows={2}
        value={item.frage}
        onChange={(e) => set({ frage: e.target.value })}
        onBlur={blur}
        placeholder="Heute konnte ich ..."
        style={{ width: "100%", fontWeight: 600, fontSize: 13, resize: "vertical",
                 border: "1px solid transparent", background: "transparent",
                 padding: "2px 4px", paddingRight: 26 }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6 }}>
        <input
          type="text"
          value={item.pol_min}
          onChange={(e) => set({ pol_min: e.target.value })}
          onBlur={blur}
          placeholder="Pol links (0)"
          title="Label am linken Pol (Wert 0)"
          style={{ flex: "1 1 0", fontSize: 11, padding: "3px 6px",
                   border: "1px solid var(--st-gray-border)", borderRadius: 3,
                   color: "var(--st-text-soft)", background: "var(--st-bg)" }}
        />
        <input
          type="range" min={0} max={100} defaultValue={50} disabled
          style={{ flex: "1.4 1 0", accentColor: "#0A4B71", opacity: 0.85 }}
          title="Vorschau – im SNS beantwortet der Klient hier täglich"
        />
        <input
          type="text"
          value={item.pol_max}
          onChange={(e) => set({ pol_max: e.target.value })}
          onBlur={blur}
          placeholder="Pol rechts (100)"
          title="Label am rechten Pol (Wert 100)"
          style={{ flex: "1 1 0", fontSize: 11, padding: "3px 6px",
                   border: "1px solid var(--st-gray-border)", borderRadius: 3,
                   color: "var(--st-text-soft)", background: "var(--st-bg)",
                   textAlign: "right" }}
        />
      </div>
    </div>
  );
}

function P6({ toast, resumeJob, onResumed }) {
  const [jobModel, setJobModel] = useState("");
  const [audio, setAudio] = useState(null);

  const [draft, updateDraft, clearDraft] = useDraftCache("st_draft_p6", P6_DRAFT_DEFAULT);
  const { themen, prompt, kennung, nItems } = draft;

  const draftDirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(P6_DRAFT_DEFAULT),
    [draft]
  );

  // ism = editierbarer Fragebogen-Zustand (aus result_text JSON geparst)
  const [ism, setIsm] = useState(null);
  const [ismError, setIsmError] = useState(null);
  // v19.29: Live-QC (D4=B: Blur / Item-Wechsel / vor Export) statt statischem Kasten.
  const { qc, setQc, check: runCheck } = useIsmLiveCheck(null);
  const ismRef = useRef(null);
  ismRef.current = ism;
  const [focusedItem, setFocusedItem] = useState(null);
  const [xmlBusy, setXmlBusy] = useState(false);

  // Blur-Handler: prueft den aktuellen (bereits per setIsm uebernommenen) Stand.
  function checkNow() { runCheck(ismRef.current); }
  function checkAfter(updater) {
    setIsm((prev) => {
      const next = updater(prev);
      ismRef.current = next;
      Promise.resolve().then(() => runCheck(next));
      return next;
    });
  }
  function focusItem(index) {
    setFocusedItem(index);
    const el = typeof document !== "undefined" ? document.getElementById(`ism-item-${index}`) : null;
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => setFocusedItem((cur) => (cur === index ? null : cur)), 2000);
  }

  function applyResult(j) {
    setQc(j.quality_check || null);
    try {
      const parsed = JSON.parse(j.result_text || "");
      if (!parsed || !Array.isArray(parsed.items)) throw new Error("items fehlen");
      setIsm(parsed);
      setIsmError(null);
    } catch (e) {
      setIsm(null);
      setIsmError(
        "Das Ergebnis konnte nicht als Fragebogen gelesen werden (" +
        (e.message || e) + "). Details im QualityCheck / Job-Log."
      );
    }
  }

  // v19.21 (S5d): Job-Skelett aus ../workflow-run.jsx. P6 zeigt keinen
  // Fliesstext (wr.out ungenutzt), sondern parst result_text als Fragebogen
  // (applyResult) und meldet Fehler in der ISM-Vorschau (onError).
  const wr = useWorkflowRun({
    workflow: "ism_fragebogen", page: "p6", resumeJob, onResumed,
    onResult: applyResult,
    onError: (msg) => { setIsm(null); setIsmError(msg); },
  });
  const { busy, currentJobId, lastJobId } = wr;

  function run() {
    setIsm(null); setIsmError(null); setQc(null);
    wr.start(prompt, "", {
      audio:       audio,                 // __p0recording -> p0_recording_id
      bullets:     themen || null,
      model:       jobModel || null,
      patientName: kennung.trim(),        // -> patient_kuerzel (Job-Liste + XML-Name)
      ismNItems:   nItems,
    });
  }

  // ── Editier-Helfer fuer die Vorschau ──────────────────────────────────
  function patchItem(index, patch) {
    setIsm((prev) => ({
      ...prev,
      items: prev.items.map((it, i) => (i === index ? { ...it, ...patch } : it)),
    }));
  }
  function deleteItem(index) {
    checkAfter((prev) => ({ ...prev, items: prev.items.filter((_, i) => i !== index) }));
  }
  function addItem(faktorId) {
    checkAfter((prev) => ({
      ...prev,
      items: [...prev.items, {
        faktor_id: faktorId,
        frage: "Heute ...",
        pol_min: "ich übe noch...",
        pol_max: "...gelungen",
      }],
    }));
  }

  // ── XML-Export (Backend-Renderer, D5/A1) ─────────────────────────────
  async function fetchXml() {
    const name = `${kennung.trim()} individualisiert`;
    const r = await apiFetch(`${getApiBase()}/ism/xml`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, fragebogen: ism }),
    });
    if (!r.ok) {
      let detail = r.statusText;
      try { detail = JSON.stringify((await r.json()).detail); } catch (_) {}
      throw new Error(detail);
    }
    const data = await r.json(); // { xml, filename, quality_check }
    // v19.29 (S4/D3): QC auf genau dem exportierten Stand; bei offenen
    // Beanstandungen nachfragen, nie blockieren.
    if (data.quality_check) setQc(data.quality_check);
    if (exportNeedsConfirm(data.quality_check) && !window.confirm(confirmExportText(data.quality_check))) {
      return null;
    }
    return data;
  }

  async function copyXml() {
    setXmlBusy(true);
    try {
      const res = await fetchXml();
      if (!res) return;
      const { xml } = res;
      await navigator.clipboard.writeText(xml);
      toast("SNS-XML kopiert");
    } catch (e) { toast("XML-Export fehlgeschlagen: " + friendlyError(e)); }
    finally { setXmlBusy(false); }
  }

  async function downloadXml() {
    setXmlBusy(true);
    try {
      const res = await fetchXml();
      if (!res) return;
      const { xml, filename } = res;
      const blob = new Blob([xml], { type: "application/xml" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { toast("XML-Export fehlgeschlagen: " + friendlyError(e)); }
    finally { setXmlBusy(false); }
  }

  // ── Gates ────────────────────────────────────────────────────────────
  const hasP0 = !!(audio && audio.__p0recording);
  const canGenerate = hasP0 && kennung.trim().length > 0;
  const canExport = !!ism && ism.items.length > 0 && kennung.trim().length > 0;

  // Items nach Faktor gruppieren (Anzeige); Index in ism.items bleibt
  // massgeblich fuer die Editier-Callbacks.
  const grouped = useMemo(() => {
    if (!ism) return [];
    const byFactor = new Map();
    ism.items.forEach((it, idx) => {
      const fid = Number.isInteger(it.faktor_id) ? it.faktor_id : 0;
      if (!byFactor.has(fid)) byFactor.set(fid, []);
      byFactor.get(fid).push({ item: it, idx });
    });
    return [...byFactor.entries()].sort((a, b) => a[0] - b[0]);
  }, [ism]);

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Workflow 6</div>
        <h2>ISM-Fragebogen</h2>
        <p>Individualisierter Prozess-Fragebogen aus dem Therapiegespräch – Vorschau bearbeiten, als SNS-XML exportieren</p>
      </div>
      <div className="page-body">
        <div className="workflow">
          <Card num="A" title="Gespräch aus Aufnahmen (P0)" badge="req" open={true}>
            <AudioInput file={audio} onFile={setAudio} />
            {audio && !audio.__p0recording && (
              <div className="upload-warn" style={{ marginTop: 8 }}>
                Für den ISM-Fragebogen bitte ein Gespräch aus der P0-Aufnahmeliste
                auswählen (Direkt-Uploads sind hier nicht vorgesehen).
              </div>
            )}
          </Card>

          <Card num="B" title="Einzelne Fragen / Themen" badge="opt" open={false}>
            <label className="field-label">Gewünschte Schwerpunkte oder konkrete Frage-Ideen</label>
            <textarea rows={4}
              placeholder={"– Abgrenzung in der Familie\n– Umgang mit dem inneren Antreiber\n– \u201EHeute konnte ich meine Pausen ernst nehmen\u201C ..."}
              value={themen}
              onChange={(e) => updateDraft({ themen: e.target.value })}
            />
            <div className="field-note">Werden als Hinweis an das Modell weitergegeben – nur Themen, die im Gespräch belegt sind, werden aufgegriffen. Fertig formulierte Frage-Wünsche dürfen als Item übernommen werden.</div>
          </Card>

          <Card num="C" title="Prompt/Modell anpassen (advanced)" badge="opt" open={false}>
            <JobModelPicker workflow="ism_fragebogen" value={jobModel} onChange={setJobModel} />
            <PromptEditor value={prompt} onChange={(v) => updateDraft({ prompt: v })} def={P_ISM} />
            <div className="field-note">Inhaltliche Anweisungen (Item-Form, Tonalität). JSON-Form, Faktorregeln und Quellenregel liegen im Backend und sind nicht editierbar.</div>
          </Card>

          <WorkflowActionBar
            busy={wr.busy} onRun={run} onCancel={wr.cancel}
            runLabel="ISM-Fragebogen erstellen"
            disabled={!canGenerate}
            title={!hasP0 ? "Bitte ein Gespräch aus der P0-Aufnahmeliste auswählen"
                    : !kennung.trim() ? "SNS-Kennung ist erforderlich (wird Fragebogenname im XML)"
                    : ""}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginRight: "auto", flexWrap: "wrap" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ fontSize: 11, color: "var(--st-text-soft)" }}>
                  SNS-Kennung <span style={{ color: "#c0392b", fontWeight: 700 }}>*</span>
                </span>
                <input
                  type="text"
                  value={kennung}
                  onChange={(e) => updateDraft({ kennung: e.target.value })}
                  placeholder="MJ28585IND"
                  maxLength={24}
                  required
                  title="User-Kennung des Klienten im SNS – wird Fragebogenname im XML"
                  style={{
                    width: 110, padding: "3px 6px", fontSize: 12, borderRadius: 3,
                    border: kennung.trim() ? "1px solid var(--st-gray-border)" : "1px solid #c0392b",
                    background: "var(--st-bg)", color: "var(--st-text)", fontFamily: "inherit",
                  }}
                />
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ fontSize: 11, color: "var(--st-text-soft)" }}>Items</span>
                <select
                  value={nItems}
                  onChange={(e) => updateDraft({ nItems: parseInt(e.target.value, 10) })}
                  style={{ padding: "3px 6px", fontSize: 12, borderRadius: 3,
                           border: "1px solid var(--st-gray-border)",
                           background: "var(--st-bg)", color: "var(--st-text)" }}
                >
                  {[4, 5, 6, 7, 8, 9, 10, 11, 12].map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
              </div>
            </div>
          </WorkflowActionBar>

          {/* ── Ergebnis: editierbare Vorschau ─────────────────────────── */}
          <div className="output-card">
            <div className="output-head">
              <span className="output-title">Fragebogen-Vorschau (editierbar)</span>
              <div className="output-btns">
                {canExport && (
                  <button className="btn-out" onClick={copyXml} disabled={xmlBusy}>
                    {xmlBusy ? "…" : "XML kopieren"}
                  </button>
                )}
                {canExport && (
                  <button className="btn-out" onClick={downloadXml} disabled={xmlBusy}>XML herunterladen</button>
                )}
              </div>
            </div>
            <div className={"output-text" + (!ism && !busy && !ismError ? " empty" : "")}
                 style={{ whiteSpace: "normal" }}>
              {busy && (currentJobId
                ? <JobProgressBar jobId={currentJobId} />
                : "Wird generiert ...")}
              {!busy && ismError && (
                <span style={{ color: "var(--st-error,#b00)", fontWeight: 500 }}>{ismError}</span>
              )}
              {!busy && !ism && !ismError && "Die Fragebogen-Vorschau erscheint hier."}

              {!busy && ism && (
                <div>
                  <div style={{ marginBottom: 14 }}>
                    <label className="field-label">Begrüßung (täglicher Einstieg)</label>
                    <textarea rows={2} value={ism.begruessung}
                      onChange={(e) => setIsm({ ...ism, begruessung: e.target.value })}
                      onBlur={checkNow}
                      style={{ width: "100%" }} />
                  </div>

                  {grouped.map(([fid, entries]) => (
                    <div key={fid} style={{ marginBottom: 6 }}>
                      <div style={{
                        fontSize: 11, fontWeight: 700, letterSpacing: "0.06em",
                        textTransform: "uppercase", color: "#0A4B71",
                        margin: "12px 0 6px",
                        borderBottom: "1px solid var(--st-gray-border)",
                        paddingBottom: 3,
                        display: "flex", alignItems: "center",
                      }}>
                        <span>{ISM_FAKTOR_LABELS[fid] || `Faktor ${fid}`}</span>
                        <button
                          onClick={() => addItem(fid)}
                          title="Item in diesem Faktor ergänzen"
                          style={{ marginLeft: "auto", border: "none", background: "none",
                                   cursor: "pointer", color: "#0A4B71", fontSize: 13,
                                   fontWeight: 700 }}
                        >＋</button>
                      </div>
                      {entries.map(({ item, idx }) => (
                        <IsmItemEditor
                          key={idx}
                          item={item}
                          index={idx}
                          onChange={patchItem}
                          onDelete={deleteItem}
                          onBlur={checkNow}
                          issues={issuesForItem(qc, idx)}
                          focused={focusedItem === idx}
                        />
                      ))}
                    </div>
                  ))}

                  <div style={{ marginTop: 8 }}>
                    <label className="field-label">Verabschiedung (täglicher Abschluss)</label>
                    <textarea rows={2} value={ism.verabschiedung}
                      onChange={(e) => setIsm({ ...ism, verabschiedung: e.target.value })}
                      onBlur={checkNow}
                      style={{ width: "100%" }} />
                  </div>

                  {/* v19.29 (D5=A): QC-Panel auf dem editierten Stand, read-only */}
                  <QualityCheckPanel data={qc} readOnly onFocusItem={focusItem} />

                  <div className="field-note" style={{ marginTop: 10 }}>
                    Änderungen hier fließen direkt in den XML-Export.
                    Fragebogenname im SNS: <b>{kennung.trim() || "…"} individualisiert</b>
                    {" · "}{ism.items.length} Items
                  </div>
                </div>
              )}
            </div>
          </div>

          <FeedbackButton jobId={lastJobId} workflow="ism_fragebogen" toast={toast} />

          {(ism || ismError || draftDirty || audio) && !busy && (
            <div style={{ marginTop: 12, textAlign: "right" }}>
              <button className="btn-secondary" onClick={() => {
                setAudio(null);
                clearDraft();
                setIsm(null); setIsmError(null); setQc(null);
                wr.reset();
                toast("Formular zurückgesetzt");
              }}>+ Neuer ISM-Fragebogen</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export { P6, IsmItemEditor };
