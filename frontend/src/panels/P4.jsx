// ────────────────────────────────────────────────────────────────────────────
// src/panels/P4.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { apiFetch, generate, getApiBase, pollJob } from "../api.jsx";
import { useJobResult } from "../hooks.jsx";
import { P_ENTL } from "../prompt-defaults.jsx";
import { RepairBundle, ResultVersionsTabs } from "../qa.jsx";
import { clearActiveJob, friendlyError, getEmptyWarning, loadActiveJob } from "../shared.jsx";
import { Card, Dropzone, InputTabs, Output, PromptEditor, JobModelPicker } from "../ui.jsx";



function P4({ toast, resumeJob, onResumed, model }) {
  // Modellwahl fuer DIESEN Job (JobModelPicker); leer = globaler Fallback
  const [jobModel, setJobModel] = useState("");
  const [bericht, setBericht]     = useState(null);
  const [verlauf, setVerlauf]     = useState(null);
  const [style, setStyle]         = useState(null);
  const [styleText, setStyleText] = useState("");
  const [fokus, setFokus]         = useState("");
  // v18: Workflow-Anweisungen als editierbares Feld (advanced option)
  const [prompt, setPrompt]       = useState(P_ENTL);
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
    if (!resumeJob || resumeJob.page !== "p4") return;
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
      let patientNameExplicit = null;
      if (kuerzel.trim()) {
        const kurz = kuerzel.trim().replace(/\.?$/, ".");
        if (geschlecht === "w")      patientNameExplicit = `Frau ${kurz}`;
        else if (geschlecht === "m") patientNameExplicit = `Herr ${kurz}`;
        else                          patientNameExplicit = kurz;
      }
      const result = await generate("entlassbericht", prompt, "", {
        antragsvorlage: bericht,  // Vorbericht/Verlängerungsantrag → Diagnosen/Anamnese/Befund/Name
        verlauf:        verlauf,  // Verlaufsdokumentation
        style:          style,
        styleText:      styleText || null,
        bullets:        fokus || null,
        model:          jobModel || model || null,
        patientName:    patientNameExplicit,
        onJobId:        setCurrentJobId,
        signal:         ac.signal,
      }, "p4");
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
        <div className="page-eyebrow">Workflow 4</div>
        <h2>Entlassbericht</h2>
        <p>Synthetisiert alle Verlaufsnotizen zu einem vollständigen Entlassbericht</p>
      </div>
      <div className="page-body">
        <div className="workflow">
          <Card num="A" title="Verlaufsdokumentation" badge="req">
            <Dropzone label="Verlaufsdokumentation hochladen" hint=".pdf — gesamte Dokumentation des Aufenthalts" accept=".pdf" icon="&#128202;" file={verlauf} onFile={setVerlauf} />
            <div className="info-note" style={{marginTop:8}}>Alle Verlaufsnotizen des stationären Aufenthalts als PDF.</div>
          </Card>

          <Card num="B" title="Antragsvorlage" badge="req">
            <Dropzone label="Vorlage hochladen" hint=".docx — vorheriger Bericht/Verlängerungsantrag mit Diagnosen und Anamnese" accept=".docx,.pdf" icon="&#128196;" file={bericht} onFile={setBericht} />
            <div className="info-note" style={{marginTop:8}}>Diagnosen, Anamnese und Befund werden aus dieser Vorlage extrahiert.</div>
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
                  <textarea rows={5} placeholder="Beispiel-Entlassbericht einfügen ..." value={styleText} onChange={(e) => setStyleText(e.target.value)} style={{marginTop:0}} />
                  <div className="field-note">Schreibstil des eingefügten Texts wird übernommen</div>
                </>)}
              </>)}
            </InputTabs>
          </Card>

          <Card num="D" title="Fokus-Themen" badge="opt" open={false}>
            <label className="field-label">Schwerpunkte für diesen Entlassbericht</label>
            <textarea rows={4}
              placeholder={"Optionale Schwerpunkte, z.B.:\n– Wächteranteil Türsteher, Arbeit mit inneren Anteilen\n– Gruppenarbeit und soziale Integration\n– Familien- und Paardynamik\n– Entschluss zur räumlichen Trennung"}
              value={fokus}
              onChange={e => setFokus(e.target.value)}
            />
            <div className="field-note">Werden als Hinweis an das Modell weitergegeben – nur Themen die in der Verlaufsdoku belegt sind werden aufgegriffen.</div>
          </Card>

          <Card num="E" title="Prompt-Vorlage (advanced)" badge="opt" open={false}>
            <PromptEditor value={prompt} onChange={setPrompt} def={P_ENTL} />
            <div className="field-note">Inhaltliche Workflow-Anweisungen. Anpassen nur wenn nötig – Stil-/Quellenregeln und Halluzinationsschutz liegen im Backend und sind nicht hier editierbar.</div>
          </Card>

          <div className="action-bar">
        <JobModelPicker workflow="entlassbericht" value={jobModel} onChange={setJobModel} />
            {busy
              ? <button className="btn-secondary" onClick={cancelRun}>✕ Abbrechen</button>
              : <button
                  className="btn-primary"
                  onClick={run}
                  disabled={!verlauf || !bericht}
                  title={
                    !verlauf ? "Verlaufsdokumentation erforderlich"
                    : !bericht ? "Antragsvorlage erforderlich (Diagnosen + Anamnese)"
                    : ""
                  }
                >Entlassbericht erstellen</button>
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
                setVerlauf(null); setBericht(null); setStyle(null); setStyleText("");
                setFokus(""); setPrompt(P_ENTL); setOut(""); setOutWarn(null); setLastJobId(null);
                jobOps.reset();
                toast("Formular zurückgesetzt");
              }}>+ Neuer Entlassbericht</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export { P4 };
