// ────────────────────────────────────────────────────────────────────────────
// src/panels/P3.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { apiFetch, getApiBase, pollJob, startJob } from "../api.js";
import { useDraftCache, useJobResult, useResumeWorkflowJob } from "../hooks.jsx";
import { P_VERL, P_VERL_FOLGE } from "../prompt-defaults.jsx";
import { RepairBundle, ResultVersionsTabs } from "../qa.jsx";
import { clearActiveJob, friendlyError, getEmptyWarning, loadActiveJob } from "../shared.js";
import { Card, Dropzone, InputTabs, Output, PromptEditor, JobModelPicker, copyFormatted, FeedbackButton } from "../ui.jsx";


// Sprint B2: Text-Felder die in localStorage persistiert werden - ueberleben
// Tab-Wechsel (via Keep-Mounted ohnehin) UND F5/Reload. Files (verlauf,
// antrag, style) bleiben in-memory only (localStorage-Quota).
const P3_DRAFT_DEFAULT = {
  styleText: "", fokus: "", prompt: P_VERL,
  // v19.12: geschlecht/kuerzel entfernt - beides kommt aus der
  // Antragsvorlage (Backend-Extraktion, Kandidaten-Konsens).
};

function P3({ toast, resumeJob, onResumed }) {
  // Modellwahl fuer DIESEN Job (JobModelPicker); leer = globaler Fallback
  const [jobModel, setJobModel] = useState("");
  const [antrag, setAntrag]       = useState(null);
  const [verlauf, setVerlauf]     = useState(null);
  const [style, setStyle]         = useState(null);

  // B2: Text-Felder ueber useDraftCache (Pattern aus P2/B1). Adapter-Setter
  // halten die JSX-Aufrufseite kompatibel.
  const [draft, updateDraft, clearDraft] = useDraftCache("st_draft_p3", P3_DRAFT_DEFAULT);
  const { styleText, fokus, prompt } = draft;
  const setStyleText  = useCallback(v => updateDraft({ styleText: v }),  [updateDraft]);
  const setFokus      = useCallback(v => updateDraft({ fokus: v }),      [updateDraft]);
  const setPrompt     = useCallback(v => updateDraft({ prompt: v }),     [updateDraft]);

  // B2 (S5): Reset-Button auch ohne Output anbieten sobald der Draft
  // vom Default abweicht (JSON-Compare - Keyreihenfolge ist stabil, da
  // useDraftCache Defaults zuerst spreadet).
  const draftDirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(P3_DRAFT_DEFAULT),
    [draft]
  );

  const [out, setOut]             = useState("");
  const [outWarn, setOutWarn]       = useState(null);
  const [job, jobOps]               = useJobResult();
  const [lastJobId, setLastJobId] = useState(null);
  const [busy, setBusy]           = useState(false);
  const [currentJobId, setCurrentJobId] = useState(null);

  // B2: zentrale attach-Funktion (Pattern aus P2/B1). attachedRef verhindert
  // Doppel-Attach im Race zwischen Resume-Banner (resumeJob-Prop) und
  // Auto-Resume (useResumeWorkflowJob) - beide koennen beim Mount denselben
  // laufenden Job finden.
  const attachedRef = useRef(null);
  function attach(jobId) {
    if (attachedRef.current === jobId) return;
    attachedRef.current = jobId;
    setBusy(true);
    setCurrentJobId(jobId);
    pollJob(jobId, 1200)
      .then(j => {
        if (!j) return;  // cancelled
        setOut(j.result_text || "");
        setOutWarn(getEmptyWarning(j.result_text));
        jobOps.applyOriginal(j);
        setLastJobId(jobId);
      })
      .catch(e => { setOut("Fehler: " + friendlyError(e)); })
      .finally(() => { setBusy(false); setCurrentJobId(null); });
  }

  // Resume-Banner-Prop (App-Root nach F5). Hoehere Prioritaet als Auto-Resume.
  useEffect(() => {
    if (!resumeJob || resumeJob.page !== "p3") return;
    attach(resumeJob.jobId);
    onResumed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeJob]);

  // Auto-Resume beim Mount: laufenden verlaengerung-Job wiederfinden.
  useResumeWorkflowJob("verlaengerung", attach, !resumeJob);

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
    // B2: non-blocking Pfad (startJob -> attach) wie in P2. Der Job laeuft
    // im Backend weiter, auch wenn der Tab gewechselt oder F5 gedrueckt wird;
    // useResumeWorkflowJob findet ihn dann wieder.
    setBusy(true);
    setOut(""); setOutWarn(null);
    jobOps.reset();
    setLastJobId(null);
    attachedRef.current = null;
    try {
      // v19.12: patientName/geschlecht kommen aus der Antragsvorlage
      // (Backend-Extraktion, Kandidaten-Konsens) - kein manueller Override.
      const jobId = await startJob("verlaengerung", prompt, "", {
        antragsvorlage: antrag,   // Antragsvorlage → Diagnosen/Anamnese/Name
        verlauf:        verlauf,  // Verlaufsdokumentation
        style:          style,
        styleText:      styleText || null,
        bullets:        fokus || null,
        model:          jobModel || null,
      });
      attach(jobId);
    }
    catch (e) {
      setOut("Fehler: " + friendlyError(e));
      setBusy(false);
      setCurrentJobId(null);
    }
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
            onCopy={() => { copyFormatted(job.hasRepair ? job.text : out); toast("Kopiert"); }} />

          <RepairBundle job={job} ops={jobOps} toast={toast} />

          <FeedbackButton jobId={lastJobId} workflow="verlaengerung" toast={toast} />

          {(out || draftDirty || verlauf || antrag || style) && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setVerlauf(null); setAntrag(null); setStyle(null);
                clearDraft();  // B2: setzt ALLE Text-Felder auf Default + raeumt localStorage
                setOut(""); setOutWarn(null); setLastJobId(null);
                jobOps.reset();
                attachedRef.current = null;
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
// Sprint B2: persistierte Text-Felder P3b (Files bleiben aussen vor)
const P3B_DRAFT_DEFAULT = {
  styleText: "", fokus: "", prompt: P_VERL_FOLGE,
  // v19.12: geschlecht/kuerzel entfernt - beides kommt aus der
  // Antragsvorlage (Backend-Extraktion, Kandidaten-Konsens).
};

function P3b({ toast, resumeJob, onResumed }) {
  // Modellwahl fuer DIESEN Job (JobModelPicker); leer = globaler Fallback
  const [jobModel, setJobModel] = useState("");
  const [verlauf, setVerlauf]     = useState(null);
  const [antrag, setAntrag]       = useState(null);
  const [vorantrag, setVorantrag] = useState(null);
  const [style, setStyle]         = useState(null);

  // B2: Text-Felder ueber useDraftCache (Pattern aus P2/B1)
  const [draft, updateDraft, clearDraft] = useDraftCache("st_draft_p3b", P3B_DRAFT_DEFAULT);
  const { styleText, fokus, prompt } = draft;
  const setStyleText  = useCallback(v => updateDraft({ styleText: v }),  [updateDraft]);
  const setFokus      = useCallback(v => updateDraft({ fokus: v }),      [updateDraft]);
  const setPrompt     = useCallback(v => updateDraft({ prompt: v }),     [updateDraft]);

  const draftDirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(P3B_DRAFT_DEFAULT),
    [draft]
  );

  const [out, setOut]             = useState("");
  const [outWarn, setOutWarn]       = useState(null);
  const [job, jobOps]               = useJobResult();
  const [lastJobId, setLastJobId] = useState(null);
  const [busy, setBusy]           = useState(false);
  const [currentJobId, setCurrentJobId] = useState(null);

  // B2: attach-Pattern (siehe P3-Kommentar)
  const attachedRef = useRef(null);
  function attach(jobId) {
    if (attachedRef.current === jobId) return;
    attachedRef.current = jobId;
    setBusy(true);
    setCurrentJobId(jobId);
    pollJob(jobId, 1200)
      .then(j => {
        if (!j) return;  // cancelled
        setOut(j.result_text || "");
        setOutWarn(getEmptyWarning(j.result_text));
        jobOps.applyOriginal(j);
        setLastJobId(jobId);
      })
      .catch(e => { setOut("Fehler: " + friendlyError(e)); })
      .finally(() => { setBusy(false); setCurrentJobId(null); });
  }

  useEffect(() => {
    if (!resumeJob || resumeJob.page !== "p3b") return;
    attach(resumeJob.jobId);
    onResumed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeJob]);

  useResumeWorkflowJob("folgeverlaengerung", attach, !resumeJob);

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
    // B2: non-blocking Pfad (startJob -> attach) wie in P2/P3
    setBusy(true);
    setOut(""); setOutWarn(null);
    jobOps.reset();
    setLastJobId(null);
    attachedRef.current = null;
    try {
      // v19.12: patientName/geschlecht kommen aus der Antragsvorlage
      // (Backend-Extraktion, Kandidaten-Konsens) - kein manueller Override.
      const jobId = await startJob("folgeverlaengerung", prompt, "", {
        verlauf:        verlauf,
        antragsvorlage: antrag,
        vorantrag:      vorantrag,
        style:          style,
        styleText:      styleText || null,
        bullets:        fokus || null,
        model:          jobModel || null,
      });
      attach(jobId);
    }
    catch (e) {
      setOut("Fehler: " + friendlyError(e));
      setBusy(false);
      setCurrentJobId(null);
    }
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
            {/* v19.12: Klient-Controls (Geschlecht + Kuerzel) entfernt.
                Beides wird backend-seitig aus der Antragsvorlage extrahiert
                (Kandidaten-Konsens ueber Adressblock + "wir berichten ueber",
                Kreuzcheck gegen den Verlaufsdoku-Kopf). Spacer erhaelt das
                Button-Layout der action-bar. */}
            <div style={{marginRight:"auto"}} />
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
            onCopy={() => { copyFormatted(job.hasRepair ? job.text : out); toast("Kopiert"); }} />

          <RepairBundle job={job} ops={jobOps} toast={toast} />

          <FeedbackButton jobId={lastJobId} workflow="folgeverlaengerung" toast={toast} />

          {(out || draftDirty || verlauf || antrag || vorantrag || style) && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setVerlauf(null); setAntrag(null); setVorantrag(null);
                setStyle(null);
                clearDraft();  // B2: setzt ALLE Text-Felder auf Default + raeumt localStorage
                setOut(""); setOutWarn(null); setLastJobId(null);
                jobOps.reset();
                attachedRef.current = null;
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
