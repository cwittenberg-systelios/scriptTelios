// ────────────────────────────────────────────────────────────────────────────
// src/panels/P4.jsx — Entlassbericht. Extrahiert aus klinische-dokumentation.jsx
// (R4, 2026-07-01); v19.21 (S5a): Job-Skelett (attach/poll/resume/cancel/
// Action-Bar) nach ../workflow-run.jsx ausgelagert.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useCallback, useMemo } from "react";
import { useDraftCache } from "../hooks.jsx";
import { P_ENTL } from "../prompt-defaults.jsx";
import { RepairBundle, ResultVersionsTabs } from "../qa.jsx";
import { Card, Dropzone, Output, PromptEditor, JobModelPicker, copyFormatted, FeedbackButton, StyleSourceCard } from "../ui.jsx";
import { useWorkflowRun, WorkflowActionBar } from "../workflow-run.jsx";


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

  const wr = useWorkflowRun({ workflow: "entlassbericht", page: "p4", resumeJob, onResumed });

  function run() {
    // v19.12: patientName/geschlecht kommen aus der Antragsvorlage
    // (Backend-Extraktion, Kandidaten-Konsens) - kein manueller Override.
    wr.start(prompt, "", {
      antragsvorlage: bericht,  // Vorbericht/Verlängerungsantrag → Diagnosen/Anamnese/Befund/Name
      verlauf:        verlauf,  // Verlaufsdokumentation
      prozessreflexion: reflexion,  // v19.13: Abschlussreflexion des Klienten (opt)
      style:          style,
      styleText:      styleText || null,
      bullets:        fokus || null,
      model:          jobModel || null,
    });
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

          <Card num="B" title="Entlassbericht (zu vervollständigen)" badge="req">
            <Dropzone label="Entlassbericht hochladen" hint=".docx oder .pdf — der zu vervollständigende Entlassbericht ohne den psychotherapeutischen Verlauf (keine Muster-/Stilvorlage)" accept=".docx,.pdf" icon="&#128196;" file={bericht} onFile={setBericht} />
            <div className="info-note" style={{marginTop:8}}>Diagnosen, Anamnese und Befund werden aus diesem Dokument extrahiert; der psychotherapeutische Verlaufsteil wird generiert. Für reine Stil-/Musterbeispiele bitte Feld D verwenden.</div>
          </Card>

          <Card num="C" title="Prozessreflexion des Klienten" badge="opt" open={false}>
            <Dropzone label="Prozessreflexion hochladen" hint=".pdf oder .docx — Abschlussreflexion des Klienten" accept=".pdf,.docx" icon="&#128172;" file={reflexion} onFile={setReflexion} />
            <div className="info-note" style={{marginTop:8}}>Fließt als eigener Absatz am Ende des Behandlungsverlaufs ein („Zum Abschluss ihres Prozesses reflektierte die Klientin …", indirekte Rede). Offene Themen daraus fließen in die Therapieempfehlungen. Feedback an das Team wird nicht übernommen.</div>
          </Card>

          <StyleSourceCard
            num="D"
            style={style} onStyle={setStyle}
            styleText={styleText} onStyleText={setStyleText}
            placeholder="Beispiel-Entlassbericht einfügen ..."
          />

          <Card num="E" title="Fokus-Themen" badge="opt" open={false} hasContent={!!(fokus || "").trim()}>
            <label className="field-label">Schwerpunkte für diesen Entlassbericht</label>
            <textarea rows={4}
              placeholder={"Optionale Schwerpunkte, z.B.:\n– Wächteranteil Türsteher, Arbeit mit inneren Anteilen\n– Gruppenarbeit und soziale Integration\n– Familien- und Paardynamik\n– Entschluss zur räumlichen Trennung"}
              value={fokus}
              onChange={e => setFokus(e.target.value)}
            />
            <div className="field-note">Werden als Hinweis an das Modell weitergegeben – nur Themen die in der Verlaufsdoku belegt sind werden aufgegriffen.</div>
          </Card>

          <Card num="F" title="Prompt/Modell anpassen (advanced)" badge="opt" open={false} hasContent={prompt !== P_ENTL}>
            <JobModelPicker workflow="entlassbericht" value={jobModel} onChange={setJobModel} />
            <PromptEditor value={prompt} onChange={setPrompt} def={P_ENTL} />
            <div className="field-note">Inhaltliche Workflow-Anweisungen. Anpassen nur wenn nötig – Stil-/Quellenregeln und Halluzinationsschutz liegen im Backend und sind nicht hier editierbar.</div>
          </Card>

          {/* v19.12: Geschlecht + Kuerzel werden backend-seitig aus der
              Antragsvorlage extrahiert (Kandidaten-Konsens) - keine
              Klient-Controls in der Action-Bar. */}
          <WorkflowActionBar
            busy={wr.busy} onRun={run} onCancel={wr.cancel}
            runLabel="Entlassbericht erstellen"
            disabled={!verlauf || !bericht}
            title={
              !verlauf ? "Verlaufsdokumentation erforderlich"
              : !bericht ? "Entlassbericht (zu vervollständigen) erforderlich (Diagnosen + Anamnese)"
              : ""
            }
          />

          <ResultVersionsTabs
            hasRepair={wr.job.hasRepair}
            active={wr.job.activeVersion}
            onChange={wr.jobOps.setActiveVersion}
            disabled={wr.job.repairBusy}
          />
          <Output text={wr.displayText} loading={wr.busy} jobId={wr.currentJobId} warn={wr.outWarn}
            onCopy={() => { copyFormatted(wr.displayText); toast("Kopiert"); }} />

          <RepairBundle job={wr.job} ops={wr.jobOps} toast={toast} />

          <FeedbackButton jobId={wr.lastJobId} workflow="entlassbericht" toast={toast} />

          {(wr.out || draftDirty || verlauf || bericht || style || reflexion) && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setVerlauf(null); setBericht(null); setStyle(null); setReflexion(null);
                clearDraft();  // B2: setzt ALLE Text-Felder auf Default + raeumt localStorage
                wr.reset();
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
