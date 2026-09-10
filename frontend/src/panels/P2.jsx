// ────────────────────────────────────────────────────────────────────────────
// src/panels/P2.jsx — Anamnese (P2) + Akutantrag (P2b). Extrahiert aus
// klinische-dokumentation.jsx (R4, 2026-07-01); v19.21 (S5): Job-Skelett
// (attach/poll/resume/cancel/Action-Bar) in ../workflow-run.jsx.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useCallback, useEffect, useMemo } from "react";
import { downloadTranscript } from "../api.js";
import { AudioInput } from "../audio.jsx";
import { useDraftCache } from "../hooks.jsx";
import { P_AKUT, P_ANAMNESE, P_BEFUND_VORLAGE } from "../prompt-defaults.jsx";
import { RepairBundle, ResultVersionsTabs } from "../qa.jsx";
import { buildPatientName } from "../shared.js";
import { Card, Dropzone, InputTabs, Output, PromptEditor, Tags, JobModelPicker, copyFormatted, FeedbackButton, StyleSourceCard } from "../ui.jsx";
import { useWorkflowRun, WorkflowActionBar, KlientControls } from "../workflow-run.jsx";


// Sprint Draft-Persistence B1: P2 Text-Felder die in localStorage persistiert
// werden. Files (selbst, befunde, audio, txtFile, style) bleiben aussen vor.
const P2_DRAFT_DEFAULT = {
  text: "", dx: [], styleText: "",
  prompt: P_ANAMNESE,
  befundVorlage: P_BEFUND_VORLAGE,
  geschlecht: "", kuerzel: "",
};

function P2({ toast, resumeJob, onResumed }) {
  // Modellwahl fuer DIESEN Job (JobModelPicker); leer = globaler Fallback
  const [jobModel, setJobModel] = useState("");
  // File-Felder bleiben in-memory only (ueberleben weder Tab-Wechsel noch F5)
  const [selbst, setSelbst]       = useState(null);
  const [befunde, setBefunde]     = useState(null);
  const [audio, setAudio]         = useState(null);
  const [txtFile, setTxtFile]     = useState(null);
  const [style, setStyle]         = useState(null);

  // B1: Text-Felder ueber useDraftCache - ueberleben Tab-Wechsel + F5.
  // Adapter-Setter unten halten die JSX-Aufrufseite kompatibel
  // (value={text}/onChange={setText} bleibt unveraendert).
  const [draft, updateDraft, clearDraft] = useDraftCache("st_draft_p2", P2_DRAFT_DEFAULT);
  const { text, dx, styleText, prompt, befundVorlage, geschlecht, kuerzel } = draft;

  // v19.12: einmalige Migration persistierter Drafts ("auto" entfaellt).
  useEffect(() => {
    if (draft.geschlecht === "auto") updateDraft({ geschlecht: "" });
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const setText          = useCallback(v => updateDraft({ text: v }),          [updateDraft]);
  const setDx            = useCallback(v => updateDraft({ dx: v }),            [updateDraft]);
  const setStyleText     = useCallback(v => updateDraft({ styleText: v }),     [updateDraft]);
  const setPrompt        = useCallback(v => updateDraft({ prompt: v }),        [updateDraft]);
  const setBefundVorlage = useCallback(v => updateDraft({ befundVorlage: v }), [updateDraft]);
  const setGeschlecht    = useCallback(v => updateDraft({ geschlecht: v }),    [updateDraft]);
  const setKuerzel       = useCallback(v => updateDraft({ kuerzel: v }),       [updateDraft]);

  // B2 (S5): Reset-Button auch ohne Output anbieten sobald der Draft
  // vom Default abweicht.
  const draftDirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(P2_DRAFT_DEFAULT),
    [draft]
  );

  // Job-Output-State (nicht persistiert - kommt vom Backend bei Bedarf).
  // v19.21 (S5c): Skelett aus ../workflow-run.jsx; P2-spezifisch bleiben
  // Befund-Text und Transkript-Flag (onResult).
  const [befundOut, setBefundOut] = useState("");
  const [tab, setTab]             = useState("Anamnese");
  const [hasTranscript, setHasTranscript] = useState(false);
  const wr = useWorkflowRun({
    workflow: "anamnese", page: "p2", resumeJob, onResumed,
    onResult: (j) => { setBefundOut(j.befund_text || ""); setHasTranscript(j.has_transcript || false); },
  });

  function run() {
    setHasTranscript(false);
    setBefundOut("");
    const dxStr = dx.length ? dx.join(", ") : "noch nicht festgelegt";
    // v19.12 (F2a): KLIENT-GESCHLECHT-Hinweis kommt ausschliesslich vom
    // Backend (jobs.py v19.8, Marker-Guard) - siehe P1.
    const sys = prompt.replace("{diagnosen}", dxStr);
    // v19.4 Bugfix: KEIN clearDraft() beim Generieren - die Eingaben bleiben
    // stehen (Re-Generierung/Tweak moeglich); geleert wird nur ueber "Neu".
    wr.start(sys, "", {
      selbst:    selbst,
      vorbef:    befunde,
      audio:     audio,
      txtFile:   txtFile || null,
      style:     style,
      styleText: styleText || null,
      bullets:   text || null,
      // v19.5.5 Bugfix: Diagnosen auch als eigenes Form-Feld senden. Bisher
      // wurden sie nur via {diagnosen}-Ersetzung in den Anamnese-Prompt
      // geschrieben; der SEPARATE Befund-Call (build_system_prompt workflow=
      // "befund") liest sie aber aus dem 'diagnosen'-Feld (-> dx_list).
      // Ohne dieses Feld war dx_list leer -> Befund endete mit
      // "DIAGNOSEN gemäß ICD: noch nicht festgelegt" trotz eingegebener Diagnose.
      diagnosen: dx.length ? dx.join(", ") : null,
      model:     jobModel || null,
      patientName: buildPatientName(kuerzel, geschlecht),
      geschlecht:  geschlecht,   // v19.8: strukturiert, unabhaengig vom Kuerzel
      // v18: editierbare Befund-Vorlage fuer den separaten Befund-Call
      befundVorlage: befundVorlage || null,
    });
  }

  const shownText = wr.job.hasRepair
    ? (tab === "Anamnese" ? wr.job.text : wr.job.befundText)
    : (tab === "Anamnese" ? wr.out : befundOut);

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Workflow 2</div>
        <h2>Anamnese &amp; Psychopathologischer Befund</h2>
        <p>Aus Selbstauskunft, Vorbefunden und Aufnahmegespraech</p>
      </div>
      <div className="page-body">
        <div className="workflow">
          <Card num="A" title="Selbstauskunft und weitere Befunde" badge="req">
            <div className="upload-grid">
              <div>
                <div className="upload-col-label">Selbstauskunft des Klienten</div>
                <Dropzone label="PDF hochladen" hint="Ausgefuellter Patientenfragebogen" accept=".pdf" icon="&#128203;" file={selbst} onFile={setSelbst} />
              </div>
              <div>
                <div className="upload-col-label">Vorbefunde / weitere Befunde</div>
                <Dropzone label="PDF oder Bild hochladen" hint=".pdf  .jpg  .png" accept=".pdf,image/*" icon="&#127973;" file={befunde} onFile={setBefunde} />
              </div>
            </div>
          </Card>

          <Card num="B" title="Aufnahmegespräch" badge="opt" open={false} hasContent={!!(text || "").trim()}>
            <InputTabs tabs={[
              { id:"audio", icon:"🎙", label:"Aufnahme" },
              { id:"file",  icon:"📄", label:"Datei"    },
              { id:"text",  icon:"✏️", label:"Text"     },
            ]}>
              {(activeTab) => (<>
                {activeTab === "audio" && (
                  <AudioInput file={audio} onFile={setAudio} />
                )}
                {activeTab === "file" && (
                  <div style={{display:"flex",flexDirection:"column",gap:10}}>
                    <div>
                      <div style={{fontSize:11,fontWeight:600,color:"var(--st-text-soft)",marginBottom:4}}>Transkript-Datei</div>
                      <Dropzone label="Transkript hochladen" hint=".txt  .docx" accept=".txt,.docx" icon="&#128196;" file={txtFile} onFile={setTxtFile} />
                    </div>
                    <div>
                      <div style={{fontSize:11,fontWeight:600,color:"var(--st-text-soft)",marginBottom:4}}>oder Audiodatei</div>
                      <Dropzone label="Audiodatei hochladen" hint=".mp3 · .m4a · .wav · .ogg · .webm · .flac" accept=".mp3,.m4a,.wav,.ogg,.webm,.flac,.aac,audio/*" icon="&#128266;" file={audio && !audio.__p0recording ? audio : null} onFile={(f) => setAudio(f)} />
                    </div>
                  </div>
                )}
                {activeTab === "text" && (
                  <textarea rows={5} placeholder="Gesprächsinhalt des Aufnahmegesprächs direkt einfügen ..." value={text} onChange={(e) => setText(e.target.value)} style={{marginTop:0}} />
                )}
              </>)}
            </InputTabs>
          </Card>

          <Card num="C" title="Diagnosen" badge="req">
            <label className="field-label">ICD-10 oder ICD-11 Diagnosen</label>
            <Tags list={dx} onChange={setDx} />
            <div className="field-note">Enter oder Komma zum Hinzufuegen — z.B. F32.1, F41.1, Z73.0</div>
          </Card>

          <StyleSourceCard
            num="D"
            style={style} onStyle={setStyle}
            styleText={styleText} onStyleText={setStyleText}
            placeholder="Beispieldokumentation hier einfügen ..."
            rows={6}
            fileNote="Schreibstil des hochgeladenen Textes wird übernommen."
            textNote="Direkt eingefügter Beispieltext als Stilvorlage"
          />

          <Card num="E" title="Prompt/Modell anpassen (advanced)" open={false} hasContent={prompt !== P_ANAMNESE}>
            <JobModelPicker workflow="anamnese" value={jobModel} onChange={setJobModel} />
            <PromptEditor value={prompt} onChange={setPrompt} def={P_ANAMNESE} />
            <div className="field-note" style={{marginTop:8}}>Inhaltliche Anweisungen fuer die Anamnese. Stil-/Quellenregeln liegen im Backend.</div>
          </Card>

          <Card num="F" title="Befundvorlage (advanced)" open={false} hasContent={befundVorlage !== P_BEFUND_VORLAGE}>
            <PromptEditor value={befundVorlage} onChange={setBefundVorlage} def={P_BEFUND_VORLAGE} />
            <div className="field-note" style={{marginTop:8}}>AMDP-Vorlage fuer den separaten Befund-Call. Wird vom Modell mit Inhalten aus der Selbstauskunft gefuellt. Anpassen nur wenn die Standardvorlage nicht passt.</div>
          </Card>

          <WorkflowActionBar
            busy={wr.busy} onRun={run} onCancel={wr.cancel}
            runLabel="Anamnese und Befund generieren"
            disabled={!selbst}
          >
            <KlientControls geschlecht={geschlecht} onGeschlecht={setGeschlecht} kuerzel={kuerzel} onKuerzel={setKuerzel} />
          </WorkflowActionBar>

          <ResultVersionsTabs
            hasRepair={wr.job.hasRepair}
            active={wr.job.activeVersion}
            onChange={wr.jobOps.setActiveVersion}
            disabled={wr.job.repairBusy}
          />
          <Output
            text={shownText}
            loading={wr.busy} jobId={wr.currentJobId}
            warn={tab === "Anamnese" ? wr.outWarn : (befundOut ? null : wr.outWarn)}
            tabs={["Anamnese", "Psych. Befund"]}
            activeTab={tab} onTab={setTab}
            onCopy={() => { copyFormatted(shownText); toast("Kopiert"); }}
            extraButtons={hasTranscript ? [
              { label: "Transkript ↓", onClick: () => downloadTranscript(wr.lastJobId) }
            ] : []} />

          <RepairBundle job={wr.job} ops={wr.jobOps} toast={toast} />

          <FeedbackButton jobId={wr.lastJobId} workflow="anamnese" context={tab === "Anamnese" ? "anamnese" : "befund"} toast={toast} />

          {(wr.out || befundOut || draftDirty || selbst || befunde || audio || txtFile || style) && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setSelbst(null); setBefunde(null); setAudio(null);
                setTxtFile(null); setStyle(null);
                // B1: setText/setDx/setStyleText geht jetzt durch updateDraft
                // - aber clearDraft() ist sauberer (setzt ALLE Text-Felder
                // inkl. prompt/befundVorlage auf Defaults zurueck und entfernt
                // den localStorage-Eintrag).
                clearDraft();
                wr.reset();
                setBefundOut(""); setHasTranscript(false);
                toast("Formular zurückgesetzt");
              }}>+ Neue Anamnese</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// P2b · Akutantrag (eigener Tab, vorher Checkbox in P2)
// Inputs: Antragsvorlage (req), Stilvorlage (opt), Fokus (opt), Prompt (opt)
// Backend-Workflow: "akutantrag"
// ─────────────────────────────────────────────────────────────────
// Sprint B2: persistierte Text-Felder P2b (Files bleiben aussen vor)
const P2B_DRAFT_DEFAULT = {
  styleText: "", fokus: "", prompt: P_AKUT,
  // v19.12: geschlecht/kuerzel entfernt - beides kommt aus der
  // Antragsvorlage (Backend-Extraktion, Kandidaten-Konsens).
};

function P2b({ toast, resumeJob, onResumed }) {
  // Modellwahl fuer DIESEN Job (JobModelPicker); leer = globaler Fallback
  const [jobModel, setJobModel] = useState("");
  const [antrag, setAntrag]       = useState(null);
  const [style, setStyle]         = useState(null);

  // B2: Text-Felder ueber useDraftCache (Pattern aus P2/B1)
  const [draft, updateDraft, clearDraft] = useDraftCache("st_draft_p2b", P2B_DRAFT_DEFAULT);
  const { styleText, fokus, prompt } = draft;
  const setStyleText  = useCallback(v => updateDraft({ styleText: v }),  [updateDraft]);
  const setFokus      = useCallback(v => updateDraft({ fokus: v }),      [updateDraft]);
  const setPrompt     = useCallback(v => updateDraft({ prompt: v }),     [updateDraft]);

  const draftDirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(P2B_DRAFT_DEFAULT),
    [draft]
  );

  const wr = useWorkflowRun({ workflow: "akutantrag", page: "p2b", resumeJob, onResumed });

  function run() {
    // v19.12: patientName/geschlecht kommen aus der Antragsvorlage
    // (Backend-Extraktion) - kein manueller Override mehr.
    wr.start(prompt, "", {
        antragsvorlage: antrag,   // Pflicht: Anamnese, Befund, Diagnosen
        style:          style,
        styleText:      styleText || null,
        bullets:        fokus || null,
        model:          jobModel || null,
    });
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Workflow 2b</div>
        <h2>Akutantrag</h2>
        <p>Begründung für Akutaufnahme – auf Basis Anamnese/Befund/Diagnosen</p>
      </div>
      <div className="page-body">
        <div className="workflow">
          <Card num="A" title="Akutantrag (zu vervollständigen)" badge="req">
            <Dropzone label="Akutantrag hochladen" hint=".docx oder .pdf — der zu vervollständigende Akutantrag mit Anamnese, Befund, Diagnosen (keine Muster-/Stilvorlage)" accept=".docx,.pdf" icon="&#128196;" file={antrag} onFile={setAntrag} />
            <div className="info-note" style={{marginTop:8}}>Anamnese, psychischer Befund und Einweisungsdiagnosen werden aus dieser Vorlage extrahiert.</div>
          </Card>

          <StyleSourceCard
            num="B"
            style={style} onStyle={setStyle}
            styleText={styleText} onStyleText={setStyleText}
            placeholder="Beispiel-Akutantrag einfügen ..."
            textNote="Schreibstil des eingefügten Texts wird übernommen. Wenn keine Akutantrag-Stilvorlage vorliegt, fällt das Backend auf Verlängerungs-Stilbeispiele zurück."
          />

          <Card num="C" title="Fokus-Themen" badge="opt" open={false} hasContent={!!(fokus || "").trim()}>
            <label className="field-label">Schwerpunkte für die Akutbegründung</label>
            <textarea rows={4}
              placeholder={"Optionale Schwerpunkte, z.B.:\n– Akute Suizidalität\n– Dekompensation nach Auslöser-Ereignis\n– Ambulant nicht ausreichend, weil ..."}
              value={fokus}
              onChange={e => setFokus(e.target.value)}
            />
            <div className="field-note">Werden als Hinweis an das Modell weitergegeben – nur Themen die in der Antragsvorlage belegt sind werden aufgegriffen.</div>
          </Card>

          <Card num="D" title="Prompt/Modell anpassen (advanced)" badge="opt" open={false} hasContent={prompt !== P_AKUT}>
            <JobModelPicker workflow="akutantrag" value={jobModel} onChange={setJobModel} />
            <PromptEditor value={prompt} onChange={setPrompt} def={P_AKUT} />
            <div className="field-note">Inhaltliche Workflow-Anweisungen. Anpassen nur wenn nötig – Stil-/Quellenregeln und Halluzinationsschutz liegen im Backend und sind nicht hier editierbar.</div>
          </Card>

          {/* v19.12: Klient-Controls (Geschlecht + Kuerzel) entfernt.
                Beides wird backend-seitig aus der Antragsvorlage extrahiert
                (Kandidaten-Konsens ueber Adressblock + "wir berichten ueber",
                Kreuzcheck gegen den Verlaufsdoku-Kopf). Spacer erhaelt das
                Button-Layout der action-bar. */}
          <WorkflowActionBar
            busy={wr.busy} onRun={run} onCancel={wr.cancel}
            runLabel="Akutantrag erstellen"
            disabled={!antrag}
            title={!antrag ? "Akutantrag (zu vervollständigen) erforderlich" : ""}
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

          <FeedbackButton jobId={wr.lastJobId} workflow="akutantrag" toast={toast} />

          {(wr.out || draftDirty || antrag || style) && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setAntrag(null); setStyle(null);
                clearDraft();  // B2: setzt ALLE Text-Felder auf Default + raeumt localStorage
                wr.reset();
                toast("Formular zurückgesetzt");
              }}>+ Neuer Akutantrag</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export { P2_DRAFT_DEFAULT, P2, P2b };
