// ────────────────────────────────────────────────────────────────────────────
// src/panels/P3.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { apiFetch, generate, getApiBase, pollJob } from "../api.jsx";
import { useJobResult } from "../hooks.jsx";
import { P_VERL, P_VERL_FOLGE } from "../prompt-defaults.jsx";
import { RepairBundle, ResultVersionsTabs } from "../qa.jsx";
import { clearActiveJob, friendlyError, getEmptyWarning, loadActiveJob } from "../shared.jsx";
import { Card, Dropzone, InputTabs, Output, PromptEditor, JobModelPicker } from "../ui.jsx";



function P3({ toast, resumeJob, onResumed }) {
  // Modellwahl fuer DIESEN Job (JobModelPicker); leer = globaler Fallback
  const [jobModel, setJobModel] = useState("");
  const [antrag, setAntrag]       = useState(null);
  const [verlauf, setVerlauf]     = useState(null);
  const [style, setStyle]         = useState(null);
  const [styleText, setStyleText] = useState("");
  const [fokus, setFokus]         = useState("");
  // v18: Workflow-Anweisungen als editierbares Feld (advanced option)
  const [prompt, setPrompt]       = useState(P_VERL);
  // v16 Audit-Patch A3: gleicher Patient-Override wie in P1+P2
  const [geschlecht, setGeschlecht] = useState("auto");
  const [kuerzel, setKuerzel]       = useState("");
  const [out, setOut]             = useState("");
  const [outWarn, setOutWarn]       = useState(null);
  const [job, jobOps]               = useJobResult();
  const [lastJobId, setLastJobId] = useState(null);
  const [busy, setBusy]           = useState(false);
  const [currentJobId, setCurrentJobId] = useState(null);
  const abortRef = useRef(null);

  // Resume: laufenden Job nach Reload wieder aufnehmen
  useEffect(() => {
    if (!resumeJob || resumeJob.page !== "p3") return;
    setBusy(true);
    setCurrentJobId(resumeJob.jobId);
    pollJob(resumeJob.jobId, 1200)
      .then(job => {
        if (!job) { setBusy(false); onResumed(); return; } // cancelled
        setOut(job.result_text || "");
        jobOps.applyOriginal(job);
        setLastJobId(resumeJob.jobId);
        onResumed();
      })
      .catch(e => { setOut("Fehler: " + friendlyError(e)); onResumed(); })
      .finally(() => setBusy(false));
  }, [resumeJob]);

  function cancelRun() {
    if (abortRef.current) abortRef.current.abort();
    const jobId = currentJobId || loadActiveJob()?.jobId;
    if (jobId) {
      apiFetch(`${getApiBase()}/jobs/${jobId}`, { method: "DELETE" }).catch(() => {});
    }
    clearActiveJob();
    setBusy(false);
    setCurrentJobId(null);
  }

  async function run() {
    const ac = new AbortController();
    abortRef.current = ac;
    setBusy(true);
    setOut(""); setOutWarn(null);
    jobOps.reset();
    setLastJobId(null);
    try {
      // v16 Audit-Patch A3: patientName-Override ans Backend durchreichen
      // (vorher fehlte das in P3/P4 - Bug entdeckt im Audit)
      let patientNameExplicit = null;
      if (kuerzel.trim()) {
        const kurz = kuerzel.trim().replace(/\.?$/, ".");
        if (geschlecht === "w")      patientNameExplicit = `Frau ${kurz}`;
        else if (geschlecht === "m") patientNameExplicit = `Herr ${kurz}`;
        else                          patientNameExplicit = kurz;
      }
      const result = await generate("verlaengerung", prompt, "", {
        antragsvorlage: antrag,   // Antragsvorlage → Diagnosen/Anamnese/Name
        verlauf:        verlauf,  // Verlaufsdokumentation
        style:          style,
        styleText:      styleText || null,
        bullets:        fokus || null,
        model:          jobModel || null,
        patientName:    patientNameExplicit,
        onJobId:        setCurrentJobId,
        signal:         ac.signal,
      }, "p3");
      if (!result) { setBusy(false); setCurrentJobId(null); return; }
      setOut(result.text || "");
      setOutWarn(getEmptyWarning(result.text));
      jobOps.applyOriginal(result);
      setLastJobId(result.jobId);
    }
    catch (e) { setOut("Fehler: " + friendlyError(e)); }
    setBusy(false);
    setCurrentJobId(null);
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Workflow 3</div>
        <h2>Verlängerungsantrag</h2>
        <p>Befüllt vorhandene Antragsvorlagen aus der Verlaufsdokumentation</p>
      </div>
      <div className="page-body">
        <div className="workflow">
          <Card num="A" title="Verlaufsdokumentation" badge="req">
            <Dropzone label="Verlaufsdokumentation hochladen" hint=".pdf — alle Verlaufsnotizen des Aufenthalts" accept=".pdf" icon="&#128202;" file={verlauf} onFile={setVerlauf} />
            <div className="info-note" style={{marginTop:8}}>Alle Verlaufsnotizen des stationären Aufenthalts als PDF.</div>
          </Card>

          <Card num="B" title="Antragsvorlage" badge="req">
            <Dropzone label="Vorlage / Vorheriger Antrag hochladen" hint=".docx oder .pdf — Diagnosen und Anamnese werden entnommen" accept=".docx,.pdf" icon="&#128196;" file={antrag} onFile={setAntrag} />
            <div className="info-note" style={{marginTop:8}}>Diagnosen, Anamnese und Befund werden aus dieser Vorlage für den neuen Antrag übernommen.</div>
          </Card>

          <Card num="C" title="Stilvorlage" badge="opt" open={false}>
            <InputTabs tabs={[
              { id:"file", icon:"📎", label:"Datei"   },
              { id:"text", icon:"✏️", label:"Text C&P" },
            ]}>
              {(activeTab) => (<>
                {activeTab === "file" && (
                  <Dropzone label="Beispieltext hochladen" hint="PDF, DOCX oder TXT" accept=".pdf,.docx,.txt" icon="&#128221;" file={style} onFile={setStyle} />
                )}
                {activeTab === "text" && (<>
                  <textarea rows={5} placeholder="Beispiel-Verlängerungsantrag einfügen ..." value={styleText} onChange={(e) => setStyleText(e.target.value)} style={{marginTop:0}} />
                  <div className="field-note">Schreibstil des eingefügten Texts wird übernommen</div>
                </>)}
              </>)}
            </InputTabs>
          </Card>

          <Card num="D" title="Fokus-Themen" badge="opt" open={false}>
            <label className="field-label">Schwerpunkte für diesen Antrag</label>
            <textarea rows={4}
              placeholder={"Optionale Schwerpunkte, z.B.:\n– Wächteranteil Türsteher\n– Gruppenarbeit, soziale Integration\n– Entschluss zur räumlichen Trennung"}
              value={fokus}
              onChange={e => setFokus(e.target.value)}
            />
            <div className="field-note">Werden als Hinweis an das Modell weitergegeben – nur Themen die in der Verlaufsdoku belegt sind werden aufgegriffen.</div>
          </Card>

          <Card num="E" title="Prompt/Modell anpassen (advanced)" badge="opt" open={false}>
            <JobModelPicker workflow="verlaengerung" value={jobModel} onChange={setJobModel} />
            <PromptEditor value={prompt} onChange={setPrompt} def={P_VERL} />
            <div className="field-note">Inhaltliche Workflow-Anweisungen. Anpassen nur wenn nötig – Stil-/Quellenregeln und Halluzinationsschutz liegen im Backend und sind nicht hier editierbar.</div>
          </Card>

          <div className="action-bar">
            {busy
              ? <button className="btn-secondary" onClick={cancelRun}>✕ Abbrechen</button>
              : <button
                  className="btn-primary"
                  onClick={run}
                  disabled={!verlauf || !antrag}
                  title={
                    !verlauf ? "Verlaufsdokumentation erforderlich"
                    : !antrag ? "Antragsvorlage erforderlich (Diagnosen + Anamnese)"
                    : ""
                  }
                >Verlängerungsantrag erstellen</button>
            }
          </div>

          <ResultVersionsTabs
            hasRepair={job.hasRepair}
            active={job.activeVersion}
            onChange={jobOps.setActiveVersion}
            disabled={job.repairBusy}
          />
          <Output text={job.hasRepair ? job.text : out} loading={busy} jobId={currentJobId} warn={outWarn}
            onCopy={() => { navigator.clipboard.writeText(job.hasRepair ? job.text : out); toast("Kopiert"); }} />

          <RepairBundle job={job} ops={jobOps} toast={toast} />

          {out && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setVerlauf(null); setAntrag(null); setStyle(null); setStyleText("");
                setFokus(""); setPrompt(P_VERL); setOut(""); setOutWarn(null); setLastJobId(null);
                jobOps.reset();
                toast("Formular zurückgesetzt");
              }}>+ Neuer Verlängerungsantrag</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// P3b · Folgeverlängerung (eigener Tab)
// Knüpft an einen vorherigen Verlängerungsantrag an, beschreibt den
// Verlauf SEIT dem letzten Antrag.
// Inputs: Verlaufsdoku (req), Antragsvorlage (opt), Vorantrag (opt),
//          Stilvorlage (opt), Fokus (opt), Prompt (opt)
// Backend-Workflow: "folgeverlaengerung"
// ─────────────────────────────────────────────────────────────────
function P3b({ toast, resumeJob, onResumed }) {
  // Modellwahl fuer DIESEN Job (JobModelPicker); leer = globaler Fallback
  const [jobModel, setJobModel] = useState("");
  const [verlauf, setVerlauf]     = useState(null);
  const [antrag, setAntrag]       = useState(null);
  const [vorantrag, setVorantrag] = useState(null);
  const [style, setStyle]         = useState(null);
  const [styleText, setStyleText] = useState("");
  const [fokus, setFokus]         = useState("");
  const [prompt, setPrompt]       = useState(P_VERL_FOLGE);
  const [geschlecht, setGeschlecht] = useState("auto");
  const [kuerzel, setKuerzel]       = useState("");
  const [out, setOut]             = useState("");
  const [outWarn, setOutWarn]       = useState(null);
  const [job, jobOps]               = useJobResult();
  const [lastJobId, setLastJobId] = useState(null);
  const [busy, setBusy]           = useState(false);
  const [currentJobId, setCurrentJobId] = useState(null);
  const abortRef = useRef(null);

  useEffect(() => {
    if (!resumeJob || resumeJob.page !== "p3b") return;
    setBusy(true);
    setCurrentJobId(resumeJob.jobId);
    pollJob(resumeJob.jobId, 1200)
      .then(job => {
        if (!job) { setBusy(false); onResumed(); return; }
        setOut(job.result_text || "");
        jobOps.applyOriginal(job);
        setLastJobId(resumeJob.jobId);
        onResumed();
      })
      .catch(e => { setOut("Fehler: " + friendlyError(e)); onResumed(); })
      .finally(() => setBusy(false));
  }, [resumeJob]);

  function cancelRun() {
    if (abortRef.current) abortRef.current.abort();
    const jobId = currentJobId || loadActiveJob()?.jobId;
    if (jobId) {
      apiFetch(`${getApiBase()}/jobs/${jobId}`, { method: "DELETE" }).catch(() => {});
    }
    clearActiveJob();
    setBusy(false);
    setCurrentJobId(null);
  }

  async function run() {
    const ac = new AbortController();
    abortRef.current = ac;
    setBusy(true);
    setOut(""); setOutWarn(null);
    jobOps.reset();
    setLastJobId(null);
    try {
      let patientNameExplicit = null;
      if (kuerzel.trim()) {
        const kurz = kuerzel.trim().replace(/\.?$/, ".");
        if (geschlecht === "w")      patientNameExplicit = `Frau ${kurz}`;
        else if (geschlecht === "m") patientNameExplicit = `Herr ${kurz}`;
        else                          patientNameExplicit = kurz;
      }
      const result = await generate("folgeverlaengerung", prompt, "", {
        verlauf:        verlauf,
        antragsvorlage: antrag,
        vorantrag:      vorantrag,
        style:          style,
        styleText:      styleText || null,
        bullets:        fokus || null,
        model:          jobModel || null,
        patientName:    patientNameExplicit,
        onJobId:        setCurrentJobId,
        signal:         ac.signal,
      }, "p3b");
      if (!result) { setBusy(false); setCurrentJobId(null); return; }
      setOut(result.text || "");
      setOutWarn(getEmptyWarning(result.text));
      jobOps.applyOriginal(result);
      setLastJobId(result.jobId);
    }
    catch (e) { setOut("Fehler: " + friendlyError(e)); }
    setBusy(false);
    setCurrentJobId(null);
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Workflow 3b</div>
        <h2>Folgeverlängerung</h2>
        <p>Anschluss-Verlängerungsantrag – knüpft an einen vorherigen Antrag an</p>
      </div>
      <div className="page-body">
        <div className="workflow">
          <Card num="A" title="Verlaufsdokumentation" badge="req">
            <Dropzone label="Verlaufsdokumentation hochladen" hint=".pdf — Verlaufsnotizen seit dem letzten Antrag" accept=".pdf" icon="&#128202;" file={verlauf} onFile={setVerlauf} />
            <div className="info-note" style={{marginTop:8}}>Idealerweise nur die Notizen seit dem letzten Antrag, sonst alle.</div>
          </Card>

          <Card num="B" title="Antragsvorlage" badge="opt" open={false}>
            <Dropzone label="Antragsvorlage hochladen" hint=".docx oder .pdf — aktueller Antrag (Diagnosen, Anamnese)" accept=".docx,.pdf" icon="&#128196;" file={antrag} onFile={setAntrag} />
            <div className="info-note" style={{marginTop:8}}>Diagnosen, Anamnese und Befund werden aus dieser Vorlage extrahiert.</div>
          </Card>

          <Card num="C" title="Letzter Verlängerungsantrag" badge="opt" open={false}>
            <Dropzone label="Vorherigen Antrag hochladen" hint=".docx oder .pdf — der vorige Verlängerungsantrag mit Verlaufsabschnitt" accept=".docx,.pdf" icon="&#128196;" file={vorantrag} onFile={setVorantrag} />
            <div className="info-note" style={{marginTop:8}}>An diesen Verlauf wird der neue Text inhaltlich anknüpfen ("seit dem letzten Antrag ...").</div>
          </Card>

          <Card num="D" title="Stilvorlage" badge="opt" open={false}>
            <InputTabs tabs={[
              { id:"file", icon:"📎", label:"Datei"   },
              { id:"text", icon:"✏️", label:"Text C&P" },
            ]}>
              {(activeTab) => (<>
                {activeTab === "file" && (
                  <Dropzone label="Beispieltext hochladen" hint="PDF, DOCX oder TXT" accept=".pdf,.docx,.txt" icon="&#128221;" file={style} onFile={setStyle} />
                )}
                {activeTab === "text" && (<>
                  <textarea rows={5} placeholder="Beispiel-Folgeverlängerung einfügen ..." value={styleText} onChange={(e) => setStyleText(e.target.value)} style={{marginTop:0}} />
                  <div className="field-note">Schreibstil des eingefügten Texts wird übernommen. Wenn keine Folgeverlängerungs-Stilvorlage vorliegt, fällt das Backend auf Verlängerungs-Stilbeispiele zurück.</div>
                </>)}
              </>)}
            </InputTabs>
          </Card>

          <Card num="E" title="Fokus-Themen" badge="opt" open={false}>
            <label className="field-label">Schwerpunkte für die Folgeverlängerung</label>
            <textarea rows={4}
              placeholder={"Optionale Schwerpunkte, z.B.:\n– Vertiefte Traumabearbeitung seit Antrag\n– Neue Wendepunkte\n– Noch offene Therapieziele"}
              value={fokus}
              onChange={e => setFokus(e.target.value)}
            />
            <div className="field-note">Werden als Hinweis an das Modell weitergegeben – nur Themen die in der Verlaufsdoku oder dem Vorantrag belegt sind werden aufgegriffen.</div>
          </Card>

          <Card num="F" title="Prompt/Modell anpassen (advanced)" badge="opt" open={false}>
            <JobModelPicker workflow="folgeverlaengerung" value={jobModel} onChange={setJobModel} />
            <PromptEditor value={prompt} onChange={setPrompt} def={P_VERL_FOLGE} />
            <div className="field-note">Inhaltliche Workflow-Anweisungen. Anpassen nur wenn nötig – Stil-/Quellenregeln und Halluzinationsschutz liegen im Backend und sind nicht hier editierbar.</div>
          </Card>

          <div className="action-bar">
            <div style={{display:"flex", alignItems:"center", gap:6, marginRight:"auto", flexWrap:"wrap"}}>
              <span style={{fontSize:11, fontWeight:600, color:"var(--st-text-soft)", textTransform:"uppercase", letterSpacing:"0.06em"}}>Klient</span>
              {[
                { val:"w", label:"♀ weiblich" },
                { val:"m", label:"♂ männlich" },
                { val:"auto", label:"Auto"    },
              ].map(({ val, label }) => (
                <button key={val} onClick={() => setGeschlecht(val)} style={{
                  padding:"3px 8px", fontSize:12, borderRadius:3, cursor:"pointer",
                  border: geschlecht === val ? "1px solid var(--st-accent)" : "1px solid var(--st-gray-border)",
                  background: geschlecht === val ? "var(--st-accent-bg)" : "var(--st-bg)",
                  color: geschlecht === val ? "var(--st-accent)" : "var(--st-text)",
                }}>{label}</button>
              ))}
              <div style={{display:"flex", alignItems:"center", gap:4, marginLeft:4}}>
                <span style={{fontSize:11, color:"var(--st-text-soft)"}}>Kürzel</span>
                <input
                  type="text"
                  value={kuerzel}
                  onChange={e => setKuerzel(e.target.value)}
                  placeholder="K."
                  maxLength={8}
                  style={{
                    width:48, padding:"3px 6px", fontSize:12, borderRadius:3,
                    border:"1px solid var(--st-gray-border)", background:"var(--st-bg)",
                    color:"var(--st-text)", fontFamily:"inherit",
                  }}
                />
              </div>
            </div>
            {busy
              ? <button className="btn-secondary" onClick={cancelRun}>✕ Abbrechen</button>
              : <button
                  className="btn-primary"
                  onClick={run}
                  disabled={!verlauf}
                  title={!verlauf ? "Verlaufsdokumentation erforderlich" : ""}
                >Folgeverlängerung erstellen</button>
            }
          </div>

          <ResultVersionsTabs
            hasRepair={job.hasRepair}
            active={job.activeVersion}
            onChange={jobOps.setActiveVersion}
            disabled={job.repairBusy}
          />
          <Output text={job.hasRepair ? job.text : out} loading={busy} jobId={currentJobId} warn={outWarn}
            onCopy={() => { navigator.clipboard.writeText(job.hasRepair ? job.text : out); toast("Kopiert"); }} />

          <RepairBundle job={job} ops={jobOps} toast={toast} />

          {out && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setVerlauf(null); setAntrag(null); setVorantrag(null);
                setStyle(null); setStyleText("");
                setFokus(""); setPrompt(P_VERL_FOLGE); setOut(""); setOutWarn(null); setLastJobId(null);
                jobOps.reset();
                toast("Formular zurückgesetzt");
              }}>+ Neue Folgeverlängerung</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export { P3, P3b };
