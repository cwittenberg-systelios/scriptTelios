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
import { MAX_THEMEN, MODALITAETEN, issueLabel, parseFallformel, serializeFallformel } from "../fallformel.js";

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
  // v19.28.1: strukturiert statt Markdown-Text (die Therapeut:in editiert
  // Felder, nie das Markdown - Serialisierung erst beim Senden).
  struktur: STRUKTUR_MODALITAET, fallformel: null, fallformelSel: [], fallformelProposal: "",
};

const EMPTY_FF = () => ({
  auftrag: "", themen: [], symptom: "", offen: "", extra: {},
  wendepunkte: Object.fromEntries(MODALITAETEN.map(m => [m, []])),
});

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

// Fallformel-Card (v19.28.1): strukturierter Editor. Jeder Abschnitt wird
// einzeln gerendert und feldweise editiert; Themen werden per Nummernkreis
// an-/abgewaehlt (Nummer = Reihenfolge im Bericht, max. MAX_THEMEN) und mit
// Pfeilen geordnet. Abgewaehlte Themen bleiben sichtbar, gehen beim Senden
// aber NICHT mit (Entscheidung 2026-09-22). Kein Markdown im UI.
const _lbl = { fontSize:11, letterSpacing:"0.06em", textTransform:"uppercase", color:"var(--st-text-soft)", fontWeight:600 };
const _ib = { border:"1px solid var(--st-gray-border)", background:"transparent", color:"var(--st-text-soft)", borderRadius:3, width:24, height:24, fontSize:12, cursor:"pointer", display:"grid", placeItems:"center", padding:0 };
const _inp = { width:"100%", boxSizing:"border-box", fontFamily:"inherit", fontSize:13, padding:"4px 6px", border:"1px solid var(--st-gray-border)", borderRadius:3, background:"transparent", color:"inherit" };

function FallformelCard({ ff, proposalText, audit, selection, onChange, onSelection, onReset, onRerun, rerunDisabled, rerunTitle, busy }) {
  const issues = (audit && audit.issues) || [];
  const applied = !!(audit && audit.applied);
  const themen = ff.themen || [];
  const sel = (selection || []).filter(i => themen[i]);
  const rank = (i) => sel.indexOf(i);
  const update = (patch) => onChange({ ...ff, ...patch });
  const setThema = (i, patch) => update({ themen: themen.map((t, k) => (k === i ? { ...t, ...patch } : t)) });
  const toggle = (i) => {
    if (sel.includes(i)) onSelection(sel.filter(x => x !== i));
    else if (sel.length < MAX_THEMEN) onSelection([...sel, i]);
  };
  const move = (i, d) => {
    const j = i + d; if (j < 0 || j >= themen.length) return;
    const arr = themen.slice(); [arr[i], arr[j]] = [arr[j], arr[i]];
    // Auswahl folgt den Indizes mit
    const remap = (x) => (x === i ? j : x === j ? i : x);
    update({ themen: arr }); onSelection(sel.map(remap));
  };
  const remove = (i) => {
    update({ themen: themen.filter((_, k) => k !== i) });
    onSelection(sel.filter(x => x !== i).map(x => (x > i ? x - 1 : x)));
  };
  const addThema = () => {
    update({ themen: [...themen, { titel: "", desc: "", belege: "" }] });
    if (sel.length < MAX_THEMEN) onSelection([...sel, themen.length]);
  };
  const setWp = (mod, list) => update({ wendepunkte: { ...ff.wendepunkte, [mod]: list } });
  const nWp = MODALITAETEN.reduce((a, m) => a + ((ff.wendepunkte || {})[m] || []).filter(x => x.trim()).length, 0);
  const preview = serializeFallformel(ff, sel);

  return (
    <Card num="H" title="Fallformel (Gerüst des thematischen Berichts)" badge="opt" open={true} hasContent={themen.length > 0}>
      {!applied && (
        <div className="info-note">
          Für diesen Lauf wurde keine Fallformel erstellt{audit && audit.fallback_reason ? ` (${audit.fallback_reason})` : ""} – der Bericht wurde thematisch ohne Gerüst geschrieben. Du kannst unten selbst ein Gerüst anlegen.
        </div>
      )}
      {applied && (
        <div className="info-note" style={{marginBottom:10}}>
          {audit.source === "therapeut" ? "Von dir bestätigte Fallformel." : "Vom Modell vorgeschlagen."} Klick auf die Nummer, um ein Thema zu wählen oder abzuwählen (max. {MAX_THEMEN}; die Nummer ist die Reihenfolge im Bericht). Texte lassen sich direkt ändern.
        </div>
      )}

      <div style={{marginBottom:12}}>
        <div style={_lbl}>Auftrag <span style={{fontWeight:400, textTransform:"none", letterSpacing:0}}>· Teil 1</span></div>
        <textarea rows={2} style={_inp} aria-label="Auftrag" value={ff.auftrag} onChange={e => update({ auftrag: e.target.value })} placeholder="Anliegen des Klienten zu Beginn …" />
      </div>

      <div style={{marginBottom:12}}>
        <div style={{..._lbl, display:"flex", gap:8}}>Themenkandidaten <span style={{fontWeight:400, textTransform:"none", letterSpacing:0, marginLeft:"auto"}}>{sel.length} von {themen.length} gewählt (max. {MAX_THEMEN}) · Teil 2</span></div>
        {themen.map((t, i) => {
          const on = sel.includes(i);
          return (
            <div key={i} data-testid={`thema-${i}`} style={{display:"grid", gridTemplateColumns:"24px 1fr auto", gap:10, alignItems:"start", padding:"8px 10px", border:"1px solid " + (on ? "var(--st-red)" : "var(--st-gray-border)"), borderRadius:4, marginTop:6, opacity: on ? 1 : 0.6}}>
              <button type="button" role="checkbox" aria-checked={on} aria-label={`Thema ${i + 1}${t.titel ? ": " + t.titel : ""}`}
                title={on ? "Abwählen" : (sel.length >= MAX_THEMEN ? `Höchstens ${MAX_THEMEN} Themen` : "Wählen")}
                onClick={() => toggle(i)} disabled={!on && sel.length >= MAX_THEMEN}
                style={{width:24, height:24, borderRadius:"50%", fontSize:11, fontWeight:700, cursor:"pointer", padding:0, marginTop:2,
                  background: on ? "var(--st-red)" : "transparent", color: on ? "white" : "var(--st-text-soft)",
                  border: "1.5px solid " + (on ? "var(--st-red)" : "var(--st-text-soft)")}}>
                {on ? rank(i) + 1 : ""}
              </button>
              <div style={{display:"grid", gap:4}}>
                <input type="text" style={{..._inp, fontWeight:600}} aria-label={`Titel Thema ${i + 1}`} value={t.titel} placeholder="Kurztitel des Musters" onChange={e => setThema(i, { titel: e.target.value })} />
                <textarea rows={2} style={_inp} aria-label={`Beschreibung Thema ${i + 1}`} value={t.desc} placeholder="Was ist das Muster, woher kommt es laut Protokoll?" onChange={e => setThema(i, { desc: e.target.value })} />
                <input type="text" style={{..._inp, fontFamily:"ui-monospace, Menlo, monospace", fontSize:11.5, color:"var(--st-text-soft)"}} aria-label={`Belege Thema ${i + 1}`} value={t.belege} placeholder="Belege: (Einzel 23.12.), …" onChange={e => setThema(i, { belege: e.target.value })} />
              </div>
              <div style={{display:"flex", flexDirection:"column", gap:4}}>
                <button type="button" style={_ib} title="Nach oben" aria-label={`Thema ${i + 1} nach oben`} disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                <button type="button" style={_ib} title="Nach unten" aria-label={`Thema ${i + 1} nach unten`} disabled={i === themen.length - 1} onClick={() => move(i, 1)}>↓</button>
                <button type="button" style={_ib} title="Thema entfernen" aria-label={`Thema ${i + 1} entfernen`} onClick={() => remove(i)}>×</button>
              </div>
            </div>
          );
        })}
        <button type="button" className="btn-secondary" style={{marginTop:8, fontSize:12}} onClick={addThema}>+ Eigenes Thema hinzufügen</button>
      </div>

      <div style={{marginBottom:12}}>
        <div style={_lbl}>Wendepunkte je Modalität <span style={{fontWeight:400, textTransform:"none", letterSpacing:0}}>· Teil 3</span></div>
        {MODALITAETEN.map(mod => {
          const list = (ff.wendepunkte || {})[mod] || [];
          return (
            <div key={mod} style={{display:"grid", gridTemplateColumns:"minmax(120px, 160px) 1fr", gap:"6px 12px", alignItems:"start", padding:"6px 0"}}>
              <div style={{fontSize:12.5, fontWeight:600, paddingTop:5}}>{mod}</div>
              <div>
                {list.length === 0 && <div style={{fontSize:12.5, color:"var(--st-text-soft)", fontStyle:"italic", padding:"5px 0"}}>keine Wendepunkte dokumentiert</div>}
                {list.map((w, j) => (
                  <div key={j} style={{display:"flex", gap:6, marginBottom:4}}>
                    <input type="text" style={_inp} aria-label={`${mod} Wendepunkt ${j + 1}`} value={w} onChange={e => setWp(mod, list.map((x, k) => (k === j ? e.target.value : x)))} placeholder="Wendepunkt mit Datum …" />
                    <button type="button" style={{..._ib, flex:"none"}} title="Zeile entfernen" aria-label={`${mod} Wendepunkt ${j + 1} entfernen`} onClick={() => setWp(mod, list.filter((_, k) => k !== j))}>×</button>
                  </div>
                ))}
                <button type="button" className="btn-secondary" style={{fontSize:11.5, padding:"3px 8px"}} onClick={() => setWp(mod, [...list, ""])}>+ Wendepunkt</button>
              </div>
            </div>
          );
        })}
      </div>

      <div style={{marginBottom:12}}>
        <div style={_lbl}>Symptomveränderung <span style={{fontWeight:400, textTransform:"none", letterSpacing:0}}>· Teil 4 (Prozessreflexion und Testwerte kommen aus den Quellen)</span></div>
        <textarea rows={2} style={_inp} aria-label="Symptomveränderung" value={ff.symptom} onChange={e => update({ symptom: e.target.value })} placeholder="Ausgangszustand → Endzustand laut Verlauf …" />
      </div>
      <div style={{marginBottom:12}}>
        <div style={_lbl}>Offene Themen <span style={{fontWeight:400, textTransform:"none", letterSpacing:0}}>· Teil 5</span></div>
        <textarea rows={2} style={_inp} aria-label="Offene Themen" value={ff.offen} onChange={e => update({ offen: e.target.value })} placeholder="Was laut Protokoll offen bleibt …" />
      </div>

      {(issues.length > 0 || sel.length === 0) && (
        <div className="field-note" style={{marginTop:6}}>
          {sel.length === 0 && <div>⚠ Kein Thema gewählt – Teil 2 wird als Themenübersicht ohne Muster geschrieben.</div>}
          {issues.length > 0 && <div>Prüfhinweise des Modells: {issues.map((it, i) => <div key={i}>{issueLabel(it)}</div>)}</div>}
        </div>
      )}

      <div style={{display:"flex", gap:10, alignItems:"center", flexWrap:"wrap", marginTop:12}}>
        <span style={{fontSize:12, color:"var(--st-text-soft)", marginRight:"auto"}}>
          Gesendet werden {sel.length} {sel.length === 1 ? "Thema" : "Themen"}, {nWp} Wendepunkte. Belege in Klammern gehen nicht in den Bericht.
        </span>
        {proposalText && <button type="button" className="btn-secondary" onClick={onReset}>Vorschlag des Modells wiederherstellen</button>}
        <button className="btn-primary" type="button" onClick={onRerun} disabled={rerunDisabled || busy} title={rerunTitle}>
          Mit dieser Fallformel neu generieren
        </button>
      </div>
      <details style={{marginTop:8}}>
        <summary style={{fontSize:12, color:"var(--st-text-soft)", cursor:"pointer"}}>Vorschau: so geht die Fallformel an das Modell</summary>
        <pre data-testid="ff-preview" style={{fontSize:11, whiteSpace:"pre-wrap", background:"var(--st-gray-light)", padding:8, borderRadius:3, overflowX:"auto"}}>{preview}</pre>
      </details>
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
  const { styleText, fokus, prompt, struktur, fallformel, fallformelSel, fallformelProposal } = draft;
  const setStyleText  = useCallback(v => updateDraft({ styleText: v }),  [updateDraft]);
  const setFokus      = useCallback(v => updateDraft({ fokus: v }),      [updateDraft]);
  const setPrompt     = useCallback(v => updateDraft({ prompt: v }),     [updateDraft]);
  const setFallformel     = useCallback(v => updateDraft({ fallformel: v }),     [updateDraft]);
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
        // v19.28.1: einmal parsen, danach nur noch Struktur (kein Markdown im UI)
        const ff = parseFallformel(txt);
        updateDraft({ fallformel: ff, fallformelProposal: txt, fallformelSel: ff.themen.map((_, i) => i).slice(0, MAX_THEMEN) });
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

  // Rerun mit der von der Therapeut:in gewaehlten/editierten Fallformel:
  // Struktur -> Markdown erst hier; abgewaehlte Themen fallen weg.
  function rerunMitFallformel() {
    run(serializeFallformel(fallformel || EMPTY_FF(), fallformelSel || []));
  }
  function resetFallformel() {
    if (!fallformelProposal) return;
    const ff = parseFallformel(fallformelProposal);
    updateDraft({ fallformel: ff, fallformelSel: ff.themen.map((_, i) => i).slice(0, MAX_THEMEN) });
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

          <Card num="E" title="Aufbau des Berichts" badge="opt" open={false} hasContent={thematisch}>
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

          {thematisch && (fallformel || fallformelAudit) && (
            <FallformelCard
              ff={fallformel || EMPTY_FF()}
              proposalText={fallformelProposal}
              audit={fallformelAudit}
              selection={fallformelSel || []}
              onSelection={setFallformelSel}
              onChange={setFallformel}
              onReset={resetFallformel}
              onRerun={rerunMitFallformel}
              rerunDisabled={filesMissing}
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

export { P4, StrukturSwitch, FallformelCard, P4_DRAFT_DEFAULT, EMPTY_FF };
