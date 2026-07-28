// ────────────────────────────────────────────────────────────────────────────
// src/panels/P4.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { apiFetch, getApiBase, pollJob, startJob } from "../api.js";
import { useDraftCache, useJobResult, useResumeWorkflowJob } from "../hooks.jsx";
import { P_ENTL } from "../prompt-defaults.jsx";
import { RepairBundle, ResultVersionsTabs } from "../qa.jsx";
import { clearActiveJob, friendlyError, getEmptyWarning, loadActiveJob } from "../shared.js";
import { Card, Dropzone, InputTabs, Output, PromptEditor, JobModelPicker, copyFormatted, FeedbackButton } from "../ui.jsx";


// Sprint B2: persistierte Text-Felder P4 (Files bleiben aussen vor)
const P4_DRAFT_DEFAULT = {
  styleText: "", fokus: "", prompt: P_ENTL,
  // v19.12: geschlecht/kuerzel entfernt - beides kommt aus der
  // Antragsvorlage (Backend-Extraktion, Kandidaten-Konsens).
};

function P4({ toast, resumeJob, onResumed }) {
  // Modellwahl fuer DIESEN Job (JobModelPicker); leer = globaler Fallback
  const [jobModel, setJobModel] = useState("");
  const [bericht, setBericht]     = useState(null);
  const [verlauf, setVerlauf]     = useState(null);
  const [style, setStyle]         = useState(null);
  // v19.13: Prozessreflexion des Klienten (optional, .pdf/.docx).
  // Kein useDraftCache-Eintrag - Files bleiben aussen vor (B2-Konvention).
  const [reflexion, setReflexion] = useState(null);

  // B2: Text-Felder ueber useDraftCache (Pattern aus P2/B1)
  const [draft, updateDraft, clearDraft] = useDraftCache("st_draft_p4", P4_DRAFT_DEFAULT);
  const { styleText, fokus, prompt } = draft;
  const setStyleText  = useCallback(v => updateDraft({ styleText: v }),  [updateDraft]);
  const setFokus      = useCallback(v => updateDraft({ fokus: v }),      [updateDraft]);
  const setPrompt     = useCallback(v => updateDraft({ prompt: v }),     [updateDraft]);

  const draftDirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(P4_DRAFT_DEFAULT),
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
    if (!resumeJob || resumeJob.page !== "p4") return;
    attach(resumeJob.jobId);
    onResumed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeJob]);

  useResumeWorkflowJob("entlassbericht", attach, !resumeJob);

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
      const jobId = await startJob("entlassbericht", prompt, "", {
        antragsvorlage: bericht,  // Vorbericht/Verlängerungsantrag → Diagnosen/Anamnese/Befund/Name
        verlauf:        verlauf,  // Verlaufsdokumentation
        prozessreflexion: reflexion,  // v19.13: Abschlussreflexion des Klienten (opt)
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

          <Card num="C" title="Prozessreflexion des Klienten" badge="opt" open={false}>
            <Dropzone label="Prozessreflexion hochladen" hint=".pdf oder .docx — Abschlussreflexion des Klienten" accept=".pdf,.docx" icon="&#128172;" file={reflexion} onFile={setReflexion} />
            <div className="info-note" style={{marginTop:8}}>Fließt als eigener Absatz am Ende des Behandlungsverlaufs ein („Zum Abschluss ihres Prozesses reflektierte die Klientin …", indirekte Rede). Offene Themen daraus fließen in die Therapieempfehlungen. Feedback an das Team wird nicht übernommen.</div>
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
                  <textarea rows={5} placeholder="Beispiel-Entlassbericht einfügen ..." value={styleText} onChange={(e) => setStyleText(e.target.value)} style={{marginTop:0}} />
                  <div className="field-note">Schreibstil des eingefügten Texts wird übernommen</div>
                </>)}
              </>)}
            </InputTabs>
          </Card>

          <Card num="E" title="Fokus-Themen" badge="opt" open={false}>
            <label className="field-label">Schwerpunkte für diesen Entlassbericht</label>
            <textarea rows={4}
              placeholder={"Optionale Schwerpunkte, z.B.:\n– Wächteranteil Türsteher, Arbeit mit inneren Anteilen\n– Gruppenarbeit und soziale Integration\n– Familien- und Paardynamik\n– Entschluss zur räumlichen Trennung"}
              value={fokus}
              onChange={e => setFokus(e.target.value)}
            />
            <div className="field-note">Werden als Hinweis an das Modell weitergegeben – nur Themen die in der Verlaufsdoku belegt sind werden aufgegriffen.</div>
          </Card>

          <Card num="F" title="Prompt/Modell anpassen (advanced)" badge="opt" open={false}>
            <JobModelPicker workflow="entlassbericht" value={jobModel} onChange={setJobModel} />
            <PromptEditor value={prompt} onChange={setPrompt} def={P_ENTL} />
            <div className="field-note">Inhaltliche Workflow-Anweisungen. Anpassen nur wenn nötig – Stil-/Quellenregeln und Halluzinationsschutz liegen im Backend und sind nicht hier editierbar.</div>
          </Card>

          <div className="action-bar">
            {/* v19.12: Klient-Controls (v19.8 nachgeruestet) wieder entfernt.
                Geschlecht + Kuerzel werden backend-seitig aus der
                Antragsvorlage extrahiert (Kandidaten-Konsens ueber
                Adressblock + "wir berichten ueber", Kreuzcheck gegen den
                Verlaufsdoku-Kopf). Spacer erhaelt das Button-Layout. */}
            <div style={{marginRight:"auto"}} />
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
            onCopy={() => { copyFormatted(job.hasRepair ? job.text : out); toast("Kopiert"); }} />

          <RepairBundle job={job} ops={jobOps} toast={toast} />

          <FeedbackButton jobId={lastJobId} workflow="entlassbericht" toast={toast} />

          {(out || draftDirty || verlauf || bericht || style || reflexion) && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setVerlauf(null); setBericht(null); setStyle(null); setReflexion(null);
                clearDraft();  // B2: setzt ALLE Text-Felder auf Default + raeumt localStorage
                setOut(""); setOutWarn(null); setLastJobId(null);
                jobOps.reset();
                attachedRef.current = null;
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
