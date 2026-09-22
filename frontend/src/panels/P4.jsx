// ────────────────────────────────────────────────────────────────────────────
// src/panels/P4.jsx — Entlassbericht. Extrahiert aus klinische-dokumentation.jsx
// (R4, 2026-07-01); v19.21 (S5a): Job-Skelett (attach/poll/resume/cancel/
// Action-Bar) nach ../workflow-run.jsx ausgelagert.
// v19.28 (S5): Struktur-Schalter (Status quo = modalitaetsbasiert, D5) und
// Fallformel-Card fuer den thematischen Aufbau (Themenkandidaten waehlen,
// Text editieren, mit angepasster Fallformel neu generieren - D1=B, D2).
// ────────────────────────────────────────────────────────────────────────────
import { useState, useCallback, useMemo } from "react";
import { useDraftCache } from "../hooks.jsx";
import { P_ENTL, P_ENTL_THEMATISCH } from "../prompt-defaults.jsx";
import { RepairBundle, ResultVersionsTabs } from "../qa.jsx";
import { Card, Dropzone, Output, PromptEditor, JobModelPicker, copyFormatted, FeedbackButton, StyleSourceCard } from "../ui.jsx";
import { useWorkflowRun, WorkflowActionBar } from "../workflow-run.jsx";
import { MAX_THEMEN, applyThemenAuswahl, issueLabel, parseThemen, themaTitel } from "../fallformel.js";

const STRUKTUR_MODALITAET = "modalitaet";
const STRUKTUR_THEMATISCH = "thematisch";
const PROMPT_DEFAULT_FOR = { [STRUKTUR_MODALITAET]: P_ENTL, [STRUKTUR_THEMATISCH]: P_ENTL_THEMATISCH };

// Sprint B2: persistierte Text-Felder P4 (Files bleiben aussen vor)
const P4_DRAFT_DEFAULT = {
  styleText: "", fokus: "", prompt: P_ENTL,
  // v19.12: geschlecht/kuerzel entfernt - beides kommt aus der
  // Antragsvorlage (Backend-Extraktion, Kandidaten-Konsens).
  // v19.28: Struktur-Schalter (Default Status quo) + editierte Fallformel
  // des letzten Laufs (fuer F5 / Rerun); Auswahl der Themen als Indizes.
  struktur: STRUKTUR_MODALITAET, fallformelText: "", fallformelSel: [],
};

// Segmented Control fuer den Struktur-Schalter (Muster: KlientControls).
function StrukturSwitch({ value, onChange, disabled }) {
  const opts = [
    { val: STRUKTUR_MODALITAET, label: "Nach Therapieformen (Standard)" },
    { val: STRUKTUR_THEMATISCH, label: "Thematisch (Auftrag → Thema → Prozess)" },
  ];
  return (
    <div role="radiogroup" aria-label="Aufbau des Berichts" style={{display:"flex", gap:6, flexWrap:"wrap"}}>
      {opts.map(({ val, label }) => (
        <button key={val} type="button" role="radio" aria-checked={value === val} disabled={disabled}
          onClick={() => onChange(val)} style={{
            padding:"6px 12px", borderRadius:3, cursor: disabled ? "not-allowed" : "pointer",
            fontSize:12, fontWeight: value === val ? 700 : 400,
            background: value === val ? "var(--st-red)" : "var(--st-gray-light)",
            color: value === val ? "white" : "var(--st-text-soft)",
            border: value === val ? "1px solid var(--st-red)" : "1px solid var(--st-gray-border)",
            transition:"all 0.12s",
          }}>{label}</button>
      ))}
    </div>
  );
}

// Fallformel-Card: Themenkandidaten (Checkboxen, max. MAX_THEMEN), Volltext
// editierbar, Stage-1b-Signale, Rerun-Button.
function FallformelCard({ text, audit, selection, onSelection, onText, onRerun, rerunDisabled, rerunTitle, busy }) {
  const themen = useMemo(() => parseThemen(text), [text]);
  const issues = (audit && audit.issues) || [];
  const applied = !!(audit && audit.applied);
  const toggle = (i) => {
    const has = selection.includes(i);
    if (has) onSelection(selection.filter(x => x !== i));
    else if (selection.length < MAX_THEMEN) onSelection([...selection, i]);
  };
  return (
    <Card num="H" title="Fallformel (Gerüst des thematischen Berichts)" badge="opt" open={true} hasContent={!!text}>
      {!applied && (
        <div className="info-note">
          Für diesen Lauf wurde keine Fallformel erstellt{audit && audit.fallback_reason ? ` (${audit.fallback_reason})` : ""} – der Bericht wurde thematisch ohne Gerüst geschrieben.
        </div>
      )}
      {applied && (
        <div className="info-note" style={{marginBottom:8}}>
          {audit.source === "therapeut" ? "Von Ihnen bestätigte Fallformel." : "Vom Modell vorgeschlagen."} Wählen Sie bis zu {MAX_THEMEN} Themen (je weniger, desto klarer der rote Faden), passen Sie den Text an und generieren Sie neu.
        </div>
      )}
      {themen.length > 0 && (
        <div style={{marginBottom:10}}>
          <label className="field-label">Themenkandidaten</label>
          {themen.map((item, i) => (
            <label key={i} style={{display:"flex", gap:8, alignItems:"flex-start", padding:"4px 0", cursor:"pointer"}}>
              <input type="checkbox" checked={selection.includes(i)} onChange={() => toggle(i)}
                disabled={!selection.includes(i) && selection.length >= MAX_THEMEN}
                aria-label={`Thema ${i + 1}: ${themaTitel(item)}`} />
              <span style={{fontSize:13}}><strong>{themaTitel(item)}</strong>{item.includes("–") ? " – " + item.split("–").slice(1).join("–").trim() : ""}</span>
            </label>
          ))}
        </div>
      )}
      <label className="field-label">Fallformel (editierbar)</label>
      <textarea rows={12} value={text} onChange={e => onText(e.target.value)} aria-label="Fallformel" style={{fontFamily:"inherit", fontSize:13}} />
      {issues.length > 0 && (
        <div className="field-note" style={{marginTop:6}}>
          Prüfhinweise des Modells: {issues.map((it, i) => <div key={i}>{issueLabel(it)}</div>)}
        </div>
      )}
      <div style={{marginTop:10, textAlign:"right"}}>
        <button className="btn-primary" type="button" onClick={onRerun} disabled={rerunDisabled || busy} title={rerunTitle}>
          Mit dieser Fallformel neu generieren
        </button>
      </div>
    </Card>
  );
}

function P4({ toast, resumeJob, onResumed }) {
  // Modellwahl fuer DIESEN Job (JobModelPicker); leer = globaler Fallback
  const [jobModel, setJobModel] = useState("");
  const [bericht, setBericht]     = useState(null);
  const [verlauf, setVerlauf]     = useState(null);
  const [style, setStyle]         = useState(null);
  // v19.13: Prozessreflexion des Klienten (optional, .pdf/.docx).
  // Kein useDraftCache-Eintrag - Files bleiben aussen vor (B2-Konvention).
  const [reflexion, setReflexion] = useState(null);
  // v19.28: Audit der Fallformel des letzten Laufs (Quelle, Signale).
  const [fallformelAudit, setFallformelAudit] = useState(null);

  // B2: Text-Felder ueber useDraftCache (Pattern aus P2/B1)
  const [draft, updateDraft, clearDraft] = useDraftCache("st_draft_p4", P4_DRAFT_DEFAULT);
  const { styleText, fokus, prompt, struktur, fallformelText, fallformelSel } = draft;
  const setStyleText  = useCallback(v => updateDraft({ styleText: v }),  [updateDraft]);
  const setFokus      = useCallback(v => updateDraft({ fokus: v }),      [updateDraft]);
  const setPrompt     = useCallback(v => updateDraft({ prompt: v }),     [updateDraft]);
  const setFallformelText = useCallback(v => updateDraft({ fallformelText: v }), [updateDraft]);
  const setFallformelSel  = useCallback(v => updateDraft({ fallformelSel: v }),  [updateDraft]);

  // v19.28: Schalter tauscht den Prompt-Default mit - aber nur, wenn der
  // Editor noch den Default der bisherigen Struktur zeigt (eigene
  // Anpassungen bleiben erhalten).
  const setStruktur = useCallback((next) => {
    const patch = { struktur: next };
    if (draft.prompt === PROMPT_DEFAULT_FOR[draft.struktur]) patch.prompt = PROMPT_DEFAULT_FOR[next];
    updateDraft(patch);
  }, [draft.prompt, draft.struktur, updateDraft]);

  const promptDefault = PROMPT_DEFAULT_FOR[struktur] || P_ENTL;
  const thematisch = struktur === STRUKTUR_THEMATISCH;

  const draftDirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(P4_DRAFT_DEFAULT),
    [draft]
  );

  const wr = useWorkflowRun({
    workflow: "entlassbericht", page: "p4", resumeJob, onResumed,
    // v19.28: Fallformel + Audit aus dem Job-Result uebernehmen; Auswahl =
    // alle vorgeschlagenen Themen (Backend hat auf MAX_THEMEN gekuerzt).
    onResult: (j) => {
      const txt = j.fallformel_text || "";
      setFallformelAudit(j.fallformel_audit || null);
      if (txt) {
        updateDraft({ fallformelText: txt, fallformelSel: parseThemen(txt).map((_, i) => i).slice(0, MAX_THEMEN) });
      }
    },
  });

  function run(fallformelOverride) {
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
      // v19.28: Schalter nur senden, wenn thematisch (Backend-Default = Status quo)
      ebStruktur:     thematisch ? STRUKTUR_THEMATISCH : null,
      fallformel:     thematisch && fallformelOverride ? fallformelOverride : null,
    });
  }

  // Rerun mit der von der Therapeut:in gewaehlten/editierten Fallformel.
  function rerunMitFallformel() {
    const txt = applyThemenAuswahl(fallformelText, fallformelSel);
    setFallformelText(txt);
    setFallformelSel(parseThemen(txt).map((_, i) => i));
    run(txt);
  }

  const filesMissing = !verlauf || !bericht;
  const filesTitle = !verlauf ? "Verlaufsdokumentation erforderlich"
    : !bericht ? "Entlassbericht (zu vervollständigen) erforderlich (Diagnosen + Anamnese)" : "";

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
            <div className="info-note" style={{marginTop:8}}>Fließt als eigener Absatz ein („Zum Abschluss ihres Prozesses reflektierte die Klientin …", indirekte Rede) – im Standard-Aufbau am Ende des Behandlungsverlaufs, im thematischen Aufbau zu Beginn von „Reflexion und Symptomveränderung“. Offene Themen daraus fließen in die Therapieempfehlungen. Feedback an das Team wird nicht übernommen.</div>
          </Card>

          <StyleSourceCard
            num="D"
            style={style} onStyle={setStyle}
            styleText={styleText} onStyleText={setStyleText}
            placeholder="Beispiel-Entlassbericht einfügen ..."
          />

          <Card num="E" title="Aufbau des Berichts" badge="opt" open={true} hasContent={thematisch}>
            <StrukturSwitch value={struktur} onChange={setStruktur} disabled={wr.busy} />
            <div className="field-note" style={{marginTop:8}}>
              {thematisch
                ? "Auftrag des Klienten → Erarbeitung des zentralen Themas/Musters → Prozessfortschritte in Einzel-, Gruppen- und nonverbaler Therapie → Reflexion und Symptomveränderung → Empfehlungen. Das Modell schlägt zuvor eine Fallformel (Auftrag, Themenkandidaten, Wendepunkte) vor, die Sie unten prüfen, anpassen und erneut generieren können. Das Stilbeispiel (D) liefert dabei nur den Schreibstil, nicht die Gliederung."
                : "Behandlungsverlauf mit je einem Absatz pro Therapieform (Einzel, Gruppe, nonverbal) → Epikrise → Empfehlungen. Bewährter Standard."}
            </div>
          </Card>

          <Card num="F" title="Fokus-Themen" badge="opt" open={false} hasContent={!!(fokus || "").trim()}>
            <label className="field-label">Schwerpunkte für diesen Entlassbericht</label>
            <textarea rows={4}
              placeholder={"Optionale Schwerpunkte, z.B.:\n– Wächteranteil Türsteher, Arbeit mit inneren Anteilen\n– Gruppenarbeit und soziale Integration\n– Familien- und Paardynamik\n– Entschluss zur räumlichen Trennung"}
              value={fokus}
              onChange={e => setFokus(e.target.value)}
            />
            <div className="field-note">Werden als Hinweis an das Modell weitergegeben – nur Themen die in der Verlaufsdoku belegt sind werden aufgegriffen.</div>
          </Card>

          <Card num="G" title="Prompt/Modell anpassen (advanced)" badge="opt" open={false} hasContent={prompt !== promptDefault}>
            <JobModelPicker workflow="entlassbericht" value={jobModel} onChange={setJobModel} />
            <PromptEditor value={prompt} onChange={setPrompt} def={promptDefault} />
            <div className="field-note">Inhaltliche Workflow-Anweisungen. Anpassen nur wenn nötig – Stil-/Quellenregeln und Halluzinationsschutz liegen im Backend und sind nicht hier editierbar.</div>
          </Card>

          {/* v19.12: Geschlecht + Kuerzel werden backend-seitig aus der
              Antragsvorlage extrahiert (Kandidaten-Konsens) - keine
              Klient-Controls in der Action-Bar. */}
          <WorkflowActionBar
            busy={wr.busy} onRun={() => run(null)} onCancel={wr.cancel}
            runLabel="Entlassbericht erstellen"
            disabled={filesMissing}
            title={filesTitle}
          />

          <ResultVersionsTabs
            hasRepair={wr.job.hasRepair}
            active={wr.job.activeVersion}
            onChange={wr.jobOps.setActiveVersion}
            disabled={wr.job.repairBusy}
          />
          <Output text={wr.displayText} loading={wr.busy} jobId={wr.currentJobId} warn={wr.outWarn}
            onCopy={() => { copyFormatted(wr.displayText); toast("Kopiert"); }} />

          {thematisch && (fallformelText || fallformelAudit) && (
            <FallformelCard
              text={fallformelText}
              audit={fallformelAudit}
              selection={fallformelSel || []}
              onSelection={setFallformelSel}
              onText={setFallformelText}
              onRerun={rerunMitFallformel}
              rerunDisabled={filesMissing || !(fallformelText || "").trim()}
              rerunTitle={filesMissing ? filesTitle + " – Dateien nach Neuladen bitte erneut hochladen" : ""}
              busy={wr.busy}
            />
          )}

          <RepairBundle job={wr.job} ops={wr.jobOps} toast={toast} />

          <FeedbackButton jobId={wr.lastJobId} workflow="entlassbericht" toast={toast} />

          {(wr.out || draftDirty || verlauf || bericht || style || reflexion) && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setVerlauf(null); setBericht(null); setStyle(null); setReflexion(null);
                setFallformelAudit(null);
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

export { P4, StrukturSwitch, FallformelCard, P4_DRAFT_DEFAULT };
