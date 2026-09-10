// ────────────────────────────────────────────────────────────────────────────
// src/panels/P3.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useCallback, useMemo } from "react";
import { useDraftCache } from "../hooks.jsx";
import { P_VERL, P_VERL_FOLGE } from "../prompt-defaults.jsx";
import { RepairBundle, ResultVersionsTabs } from "../qa.jsx";
import { Card, Dropzone, Output, PromptEditor, JobModelPicker, copyFormatted, FeedbackButton, StyleSourceCard } from "../ui.jsx";
import { useWorkflowRun, WorkflowActionBar } from "../workflow-run.jsx";


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

  const wr = useWorkflowRun({ workflow: "verlaengerung", page: "p3", resumeJob, onResumed });

  function run() {
    // v19.12: patientName/geschlecht kommen aus der Antragsvorlage
    // (Backend-Extraktion, Kandidaten-Konsens) - kein manueller Override.
    wr.start(prompt, "", {
      antragsvorlage: antrag,   // Antragsvorlage → Diagnosen/Anamnese/Name
      verlauf:        verlauf,  // Verlaufsdokumentation
      style:          style,
      styleText:      styleText || null,
      bullets:        fokus || null,
      model:          jobModel || null,
    });
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

          <Card num="B" title="Verlängerungsantrag (zu vervollständigen)" badge="req">
            <Dropzone label="Verlängerungsantrag hochladen" hint=".docx oder .pdf — der zu vervollständigende Antrag; Diagnosen und Anamnese werden entnommen (keine Muster-/Stilvorlage)" accept=".docx,.pdf" icon="&#128196;" file={antrag} onFile={setAntrag} />
            <div className="info-note" style={{marginTop:8}}>Diagnosen, Anamnese und Befund werden aus dieser Vorlage für den neuen Antrag übernommen.</div>
          </Card>

          <StyleSourceCard
            num="C"
            style={style} onStyle={setStyle}
            styleText={styleText} onStyleText={setStyleText}
            placeholder="Beispiel-Verlängerungsantrag einfügen ..."
          />

          <Card num="D" title="Fokus-Themen" badge="opt" open={false} hasContent={!!(fokus || "").trim()}>
            <label className="field-label">Schwerpunkte für diesen Antrag</label>
            <textarea rows={4}
              placeholder={"Optionale Schwerpunkte, z.B.:\n– Wächteranteil Türsteher\n– Gruppenarbeit, soziale Integration\n– Entschluss zur räumlichen Trennung"}
              value={fokus}
              onChange={e => setFokus(e.target.value)}
            />
            <div className="field-note">Werden als Hinweis an das Modell weitergegeben – nur Themen die in der Verlaufsdoku belegt sind werden aufgegriffen.</div>
          </Card>

          <Card num="E" title="Prompt/Modell anpassen (advanced)" badge="opt" open={false} hasContent={prompt !== P_VERL}>
            <JobModelPicker workflow="verlaengerung" value={jobModel} onChange={setJobModel} />
            <PromptEditor value={prompt} onChange={setPrompt} def={P_VERL} />
            <div className="field-note">Inhaltliche Workflow-Anweisungen. Anpassen nur wenn nötig – Stil-/Quellenregeln und Halluzinationsschutz liegen im Backend und sind nicht hier editierbar.</div>
          </Card>

          <WorkflowActionBar
            busy={wr.busy} onRun={run} onCancel={wr.cancel}
            runLabel="Verlängerungsantrag erstellen"
            disabled={!verlauf || !antrag}
            title={!verlauf ? "Verlaufsdokumentation erforderlich"
                    : !antrag ? "Verlängerungsantrag (zu vervollständigen) erforderlich (Diagnosen + Anamnese)"
                    : ""}
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

          <FeedbackButton jobId={wr.lastJobId} workflow="verlaengerung" toast={toast} />

          {(wr.out || draftDirty || verlauf || antrag || style) && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setVerlauf(null); setAntrag(null); setStyle(null);
                clearDraft();  // B2: setzt ALLE Text-Felder auf Default + raeumt localStorage
                wr.reset();
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

  const wr = useWorkflowRun({ workflow: "folgeverlaengerung", page: "p3b", resumeJob, onResumed });

  function run() {
    // v19.12: patientName/geschlecht kommen aus der Antragsvorlage
    // (Backend-Extraktion, Kandidaten-Konsens) - kein manueller Override.
    wr.start(prompt, "", {
      verlauf:        verlauf,
      antragsvorlage: antrag,
      vorantrag:      vorantrag,
      style:          style,
      styleText:      styleText || null,
      bullets:        fokus || null,
      model:          jobModel || null,
    });
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

          <Card num="B" title="Folgeverlängerungsantrag (zu vervollständigen)" badge="opt" open={false}>
            <Dropzone label="Folgeverlängerungsantrag hochladen" hint=".docx oder .pdf — der zu vervollständigende aktuelle Antrag (Diagnosen, Anamnese)" accept=".docx,.pdf" icon="&#128196;" file={antrag} onFile={setAntrag} />
            <div className="info-note" style={{marginTop:8}}>Diagnosen, Anamnese und Befund werden aus dieser Vorlage extrahiert.</div>
          </Card>

          <Card num="C" title="Letzter Verlängerungsantrag" badge="opt" open={false}>
            <Dropzone label="Vorherigen Antrag hochladen" hint=".docx oder .pdf — der vorige Verlängerungsantrag mit Verlaufsabschnitt" accept=".docx,.pdf" icon="&#128196;" file={vorantrag} onFile={setVorantrag} />
            <div className="info-note" style={{marginTop:8}}>An diesen Verlauf wird der neue Text inhaltlich anknüpfen ("seit dem letzten Antrag ...").</div>
          </Card>

          <StyleSourceCard
            num="D"
            style={style} onStyle={setStyle}
            styleText={styleText} onStyleText={setStyleText}
            placeholder="Beispiel-Folgeverlängerung einfügen ..."
            textNote="Schreibstil des eingefügten Texts wird übernommen. Wenn keine Folgeverlängerungs-Stilvorlage vorliegt, fällt das Backend auf Verlängerungs-Stilbeispiele zurück."
          />

          <Card num="E" title="Fokus-Themen" badge="opt" open={false} hasContent={!!(fokus || "").trim()}>
            <label className="field-label">Schwerpunkte für die Folgeverlängerung</label>
            <textarea rows={4}
              placeholder={"Optionale Schwerpunkte, z.B.:\n– Vertiefte Traumabearbeitung seit Antrag\n– Neue Wendepunkte\n– Noch offene Therapieziele"}
              value={fokus}
              onChange={e => setFokus(e.target.value)}
            />
            <div className="field-note">Werden als Hinweis an das Modell weitergegeben – nur Themen die in der Verlaufsdoku oder dem Vorantrag belegt sind werden aufgegriffen.</div>
          </Card>

          <Card num="F" title="Prompt/Modell anpassen (advanced)" badge="opt" open={false} hasContent={prompt !== P_VERL_FOLGE}>
            <JobModelPicker workflow="folgeverlaengerung" value={jobModel} onChange={setJobModel} />
            <PromptEditor value={prompt} onChange={setPrompt} def={P_VERL_FOLGE} />
            <div className="field-note">Inhaltliche Workflow-Anweisungen. Anpassen nur wenn nötig – Stil-/Quellenregeln und Halluzinationsschutz liegen im Backend und sind nicht hier editierbar.</div>
          </Card>

          {/* v19.12: Klient-Controls (Geschlecht + Kuerzel) entfernt.
                Beides wird backend-seitig aus der Antragsvorlage extrahiert
                (Kandidaten-Konsens ueber Adressblock + "wir berichten ueber",
                Kreuzcheck gegen den Verlaufsdoku-Kopf). Spacer erhaelt das
                Button-Layout der action-bar. */}
          <WorkflowActionBar
            busy={wr.busy} onRun={run} onCancel={wr.cancel}
            runLabel="Folgeverlängerung erstellen"
            disabled={!verlauf}
            title={!verlauf ? "Verlaufsdokumentation erforderlich" : ""}
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

          <FeedbackButton jobId={wr.lastJobId} workflow="folgeverlaengerung" toast={toast} />

          {(wr.out || draftDirty || verlauf || antrag || vorantrag || style) && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setVerlauf(null); setAntrag(null); setVorantrag(null);
                setStyle(null);
                clearDraft();  // B2: setzt ALLE Text-Felder auf Default + raeumt localStorage
                wr.reset();
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
