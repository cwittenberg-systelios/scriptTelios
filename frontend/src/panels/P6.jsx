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
import { useState, useRef, useEffect, useMemo } from "react";
import { apiFetch, getApiBase, pollJob, startJob } from "../api.js";
import { AudioInput } from "../audio.jsx";
import { useDraftCache, useResumeWorkflowJob } from "../hooks.jsx";
import { P_ISM } from "../prompt-defaults.jsx";
import { clearActiveJob, friendlyError, loadActiveJob } from "../shared.js";
import { Card, FeedbackButton, JobModelPicker, JobProgressBar, PromptEditor } from "../ui.jsx";


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
function IsmItemEditor({ item, index, onChange, onDelete }) {
  const set = (patch) => onChange(index, patch);
  return (
    <div style={{
      border: "1px solid var(--st-gray-border)", borderRadius: 6,
      padding: "10px 12px", marginBottom: 10, background: "var(--st-bg)",
    }}>
      <textarea
        rows={2}
        value={item.frage}
        onChange={(e) => set({ frage: e.target.value })}
        placeholder="Heute konnte ich ..."
        style={{ width: "100%", fontWeight: 600, fontSize: 13, resize: "vertical",
                 border: "1px solid transparent", background: "transparent",
                 padding: "2px 4px" }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6 }}>
        <input
          type="text"
          value={item.pol_min}
          onChange={(e) => set({ pol_min: e.target.value })}
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
          placeholder="Pol rechts (100)"
          title="Label am rechten Pol (Wert 100)"
          style={{ flex: "1 1 0", fontSize: 11, padding: "3px 6px",
                   border: "1px solid var(--st-gray-border)", borderRadius: 3,
                   color: "var(--st-text-soft)", background: "var(--st-bg)",
                   textAlign: "right" }}
        />
        <button
          onClick={() => onDelete(index)}
          title="Item entfernen"
          style={{ border: "none", background: "none", cursor: "pointer",
                   color: "var(--st-text-soft)", fontSize: 14, padding: "0 2px" }}
        >✕</button>
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
  const [qc, setQc] = useState(null);
  const [busy, setBusy] = useState(false);
  const [currentJobId, setCurrentJobId] = useState(null);
  const [lastJobId, setLastJobId] = useState(null);
  const [xmlBusy, setXmlBusy] = useState(false);

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

  const attachedRef = useRef(null);
  function attach(jobId) {
    if (attachedRef.current === jobId) return;
    attachedRef.current = jobId;
    setBusy(true);
    setCurrentJobId(jobId);
    pollJob(jobId, 1200)
      .then((j) => {
        if (!j) return; // cancelled
        applyResult(j);
        setLastJobId(jobId);
      })
      .catch((e) => { setIsm(null); setIsmError("Fehler: " + friendlyError(e)); })
      .finally(() => { setBusy(false); setCurrentJobId(null); });
  }

  useEffect(() => {
    if (!resumeJob || resumeJob.page !== "p6") return;
    attach(resumeJob.jobId);
    onResumed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeJob]);

  useResumeWorkflowJob("ism_fragebogen", attach, !resumeJob);

  function cancelRun() {
    const jobId = currentJobId || loadActiveJob()?.jobId;
    if (jobId) {
      apiFetch(`${getApiBase()}/jobs/${jobId}`, { method: "DELETE" }).catch(() => {});
    }
    clearActiveJob();
    setBusy(false);
    setCurrentJobId(null);
    attachedRef.current = null;
  }

  async function run() {
    setBusy(true);
    setIsm(null); setIsmError(null); setQc(null);
    setLastJobId(null);
    attachedRef.current = null;
    try {
      const jobId = await startJob("ism_fragebogen", prompt, "", {
        audio:       audio,                 // __p0recording -> p0_recording_id
        bullets:     themen || null,
        model:       jobModel || null,
        patientName: kennung.trim(),        // -> patient_kuerzel (Job-Liste + XML-Name)
        ismNItems:   nItems,
      });
      attach(jobId);
    } catch (e) {
      setIsmError("Fehler: " + friendlyError(e));
      setBusy(false);
      setCurrentJobId(null);
    }
  }

  // ── Editier-Helfer fuer die Vorschau ──────────────────────────────────
  function patchItem(index, patch) {
    setIsm((prev) => ({
      ...prev,
      items: prev.items.map((it, i) => (i === index ? { ...it, ...patch } : it)),
    }));
  }
  function deleteItem(index) {
    setIsm((prev) => ({ ...prev, items: prev.items.filter((_, i) => i !== index) }));
  }
  function addItem(faktorId) {
    setIsm((prev) => ({
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
    return r.json(); // { xml, filename }
  }

  async function copyXml() {
    setXmlBusy(true);
    try {
      const { xml } = await fetchXml();
      await navigator.clipboard.writeText(xml);
      toast("SNS-XML kopiert");
    } catch (e) { toast("XML-Export fehlgeschlagen: " + friendlyError(e)); }
    finally { setXmlBusy(false); }
  }

  async function downloadXml() {
    setXmlBusy(true);
    try {
      const { xml, filename } = await fetchXml();
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

  const qcIssues = qc?.issues?.length ? qc.issues : null;

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

          <div className="action-bar">
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
            {busy
              ? <button className="btn-secondary" onClick={cancelRun}>✕ Abbrechen</button>
              : <button
                  className="btn-primary"
                  onClick={run}
                  disabled={!canGenerate}
                  title={
                    !hasP0 ? "Bitte ein Gespräch aus der P0-Aufnahmeliste auswählen"
                    : !kennung.trim() ? "SNS-Kennung ist erforderlich (wird Fragebogenname im XML)"
                    : ""
                  }
                >ISM-Fragebogen erstellen</button>
            }
          </div>

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
                  {qcIssues && (
                    <div style={{
                      border: "1px solid #f0d060", background: "#fffbe6",
                      borderRadius: 4, padding: "8px 10px", marginBottom: 12,
                      fontSize: 12, color: "#7a6000", lineHeight: 1.5,
                    }}>
                      <b>Hinweise aus der Qualitätsprüfung:</b>
                      <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                        {qcIssues.map((iss, i) => (
                          <li key={i}>{iss.message}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <div style={{ marginBottom: 14 }}>
                    <label className="field-label">Begrüßung (täglicher Einstieg)</label>
                    <textarea rows={2} value={ism.begruessung}
                      onChange={(e) => setIsm({ ...ism, begruessung: e.target.value })}
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
                        />
                      ))}
                    </div>
                  ))}

                  <div style={{ marginTop: 8 }}>
                    <label className="field-label">Verabschiedung (täglicher Abschluss)</label>
                    <textarea rows={2} value={ism.verabschiedung}
                      onChange={(e) => setIsm({ ...ism, verabschiedung: e.target.value })}
                      style={{ width: "100%" }} />
                  </div>

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
                setIsm(null); setIsmError(null); setQc(null); setLastJobId(null);
                attachedRef.current = null;
                toast("Formular zurückgesetzt");
              }}>+ Neuer ISM-Fragebogen</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export { P6 };
