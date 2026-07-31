// ────────────────────────────────────────────────────────────────────────────
// src/panels/P2.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { apiFetch, downloadTranscript, getApiBase, pollJob, startJob } from "../api.js";
import { AudioInput } from "../audio.jsx";
import { useDraftCache, useJobResult, useResumeWorkflowJob } from "../hooks.jsx";
import { P_AKUT, P_ANAMNESE, P_BEFUND_VORLAGE } from "../prompt-defaults.jsx";
import { RepairBundle, ResultVersionsTabs } from "../qa.jsx";
import { clearActiveJob, friendlyError, getEmptyWarning, loadActiveJob } from "../shared.js";
import { Card, Dropzone, InputTabs, Output, PromptEditor, Tags, JobModelPicker, copyFormatted, FeedbackButton } from "../ui.jsx";


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

  // Job-Output-State (nicht persistiert - kommt vom Backend bei Bedarf)
  const [out, setOut]             = useState("");
  const [outWarn, setOutWarn]       = useState(null);
  const [befundOut, setBefundOut] = useState("");
  const [job, jobOps]               = useJobResult();
  const [tab, setTab]             = useState("Anamnese");
  const [lastJobId, setLastJobId] = useState(null);
  const [hasTranscript, setHasTranscript] = useState(false);
  const [busy, setBusy]           = useState(false);
  const [currentJobId, setCurrentJobId] = useState(null);
  const abortRef = useRef(null);

  // B1: zentrale `attach`-Funktion - kapselt die "ab jetzt zeigt P2 diesen Job"
  // Logik. Wird von 3 Aufrufern getriggert (Resume-Banner, Auto-Resume, run()),
  // und ist V1-ready als zukuenftiger Callback fuer eine JobListPane-Klick.
  function attach(jobId) {
    setBusy(true);
    setCurrentJobId(jobId);
    pollJob(jobId, 1200)
      .then(j => {
        if (!j) { setBusy(false); setCurrentJobId(null); return; }  // cancelled
        setOut(j.result_text || "");
        setOutWarn(getEmptyWarning(j.result_text));
        setBefundOut(j.befund_text || "");
        jobOps.applyOriginal(j);
        setLastJobId(jobId);
        setHasTranscript(j.has_transcript || false);
      })
      .catch(e => { setOut("Fehler: " + friendlyError(e)); })
      .finally(() => { setBusy(false); setCurrentJobId(null); });
  }

  // Resume-Banner-Prop (gesetzt vom App-Root nach F5 wenn ein Job lief).
  // Hoehere Prioritaet als Auto-Resume (siehe `!resumeJob` unten).
  useEffect(() => {
    if (!resumeJob || resumeJob.page !== "p2") return;
    attach(resumeJob.jobId);
    onResumed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeJob]);

  // Auto-Resume beim Mount: nur wenn kein Resume-Banner aktiv ist (sonst
  // wuerde attach() doppelt laufen - der Banner-useEffect oben hat Vorrang).
  // Greift beim Szenario: Therapeut startet Job, wechselt zu anderem P,
  // kommt zurueck - der Job laeuft im Backend weiter und wird hier sichtbar.
  useResumeWorkflowJob("anamnese", attach, !resumeJob);

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
    // B1: nicht-blockierender Pfad (startJob -> attach). AbortController wird
    // weiterhin gehalten, weil cancelRun() einen abort() versuchen kann -
    // praktisch erreicht der abort den fetch nur in den ersten ms vor dem
    // Backend-Response, danach uebernimmt der Backend-Cancel-Endpoint.
    const ac = new AbortController();
    abortRef.current = ac;
    setLastJobId(null);
    setHasTranscript(false);
    setBefundOut("");
    setOut("");
    setOutWarn(null);
    jobOps.reset();
    const dxStr = dx.length ? dx.join(", ") : "noch nicht festgelegt";

    // v19.12 (F2a): KLIENT-GESCHLECHT-Hinweis kommt jetzt ausschliesslich
    // vom Backend (jobs.py v19.8, Marker-Guard) - siehe P1.
    const k = kuerzel.trim().replace(/\.?$/, ".");

    const sys = prompt.replace("{diagnosen}", dxStr);

    // Expliziten Patientennamen fuer Backend zusammensetzen (P2)
    let patientNameExplicit = null;
    if (kuerzel.trim()) {
      const kurz = k;
      if (geschlecht === "w")      patientNameExplicit = `Frau ${kurz}`;
      else if (geschlecht === "m") patientNameExplicit = `Herr ${kurz}`;
      else                          patientNameExplicit = kurz;
    }

    try {
      const jobId = await startJob("anamnese", sys, "", {
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
        patientName: patientNameExplicit,
        geschlecht:  geschlecht,   // v19.8: strukturiert, unabhaengig vom Kuerzel
        // v18: editierbare Befund-Vorlage fuer den separaten Befund-Call
        befundVorlage: befundVorlage || null,
      });
      // v19.4 Bugfix: NICHT clearDraft() beim Generieren — das setzte den Draft
      // (inkl. Diagnosen-Tags dx) sofort auf Default zurueck, sodass die
      // Diagnose im Formfeld direkt nach "Generieren" verschwand. Die Eingaben
      // bleiben jetzt stehen (Re-Generierung/Tweak moeglich); geleert wird nur
      // ueber den expliziten "Neu/Reset"-Button.
      // attach() setzt busy=true und startet pollJob - User sieht den Progress.
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

          <Card num="B" title="Aufnahmegespräch" badge="opt" open={false}>
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

          <Card num="D" title="Stilvorlage (Textbeispiel)" badge="opt" open={false}>
            <InputTabs tabs={[
              { id:"file", icon:"📎", label:"Datei"   },
              { id:"text", icon:"✏️", label:"Text C&P" },
            ]}>
              {(activeTab) => (<>
                {activeTab === "file" && (<>
                  <Dropzone label="Beispieltext hochladen" hint="PDF, DOCX oder TXT" accept=".pdf,.docx,.txt" icon="&#128221;" file={style} onFile={setStyle} />
                  <div className="info-note" style={{marginTop:8}}>Schreibstil des hochgeladenen Textes wird übernommen.</div>
                </>)}
                {activeTab === "text" && (<>
                  <textarea rows={6} placeholder="Beispieldokumentation hier einfügen ..." value={styleText} onChange={(e) => setStyleText(e.target.value)} style={{marginTop:0}} />
                  <div className="field-note">Direkt eingefügter Beispieltext als Stilvorlage</div>
                </>)}
              </>)}
            </InputTabs>
          </Card>

          <Card num="E" title="Prompt/Modell anpassen (advanced)" open={false}>
            <JobModelPicker workflow="anamnese" value={jobModel} onChange={setJobModel} />
            <PromptEditor value={prompt} onChange={setPrompt} def={P_ANAMNESE} />
            <div className="field-note" style={{marginTop:8}}>Inhaltliche Anweisungen fuer die Anamnese. Stil-/Quellenregeln liegen im Backend.</div>
          </Card>

          <Card num="F" title="Befundvorlage (advanced)" open={false}>
            <PromptEditor value={befundVorlage} onChange={setBefundVorlage} def={P_BEFUND_VORLAGE} />
            <div className="field-note" style={{marginTop:8}}>AMDP-Vorlage fuer den separaten Befund-Call. Wird vom Modell mit Inhalten aus der Selbstauskunft gefuellt. Anpassen nur wenn die Standardvorlage nicht passt.</div>
          </Card>

          <div className="action-bar">
            <div style={{display:"flex", alignItems:"center", gap:6, marginRight:"auto", flexWrap:"wrap"}}>
              <span style={{fontSize:11, fontWeight:600, color:"var(--st-text-soft)", textTransform:"uppercase", letterSpacing:"0.06em"}}>Klient</span>
              {[
                { val:"w", label:"♀ weiblich" },
                { val:"m", label:"♂ männlich" },
              ].map(({ val, label }) => (
                <button key={val} onClick={() => setGeschlecht(val)} style={{
                  padding:"4px 10px", borderRadius:3, cursor:"pointer",
                  fontSize:12, fontWeight: geschlecht === val ? 700 : 400,
                  background: geschlecht === val ? "var(--st-red)" : "var(--st-gray-light)",
                  color: geschlecht === val ? "white" : "var(--st-text-soft)",
                  border: geschlecht === val ? "1px solid var(--st-red)" : "1px solid var(--st-gray-border)",
                  transition:"all 0.12s",
                }}>{label}</button>
              ))}
              <div style={{display:"flex", alignItems:"center", gap:4, marginLeft:4}}>
                <span style={{fontSize:11, color:"var(--st-text-soft)"}}>Kürzel</span>
                <input type="text" value={kuerzel} onChange={e => setKuerzel(e.target.value)}
                  placeholder="K." maxLength={8} style={{
                    width:48, padding:"3px 6px", fontSize:12, borderRadius:3,
                    border:"1px solid var(--st-gray-border)", background:"var(--st-bg)",
                    color:"var(--st-text)", fontFamily:"inherit",
                  }} />
              </div>
            </div>
            {busy
              ? <button className="btn-secondary" onClick={cancelRun}>✕ Abbrechen</button>
              : <button className="btn-primary" onClick={run} disabled={!selbst}>Anamnese und Befund generieren</button>
            }
          </div>

          <ResultVersionsTabs
            hasRepair={job.hasRepair}
            active={job.activeVersion}
            onChange={jobOps.setActiveVersion}
            disabled={job.repairBusy}
          />
          <Output
            text={
              job.hasRepair
                ? (tab === "Anamnese" ? job.text : job.befundText)
                : (tab === "Anamnese" ? out : befundOut)
            }
            loading={busy} jobId={currentJobId}
            warn={tab === "Anamnese" ? outWarn : (befundOut ? null : outWarn)}
            tabs={["Anamnese", "Psych. Befund"]}
            activeTab={tab} onTab={setTab}
            onCopy={() => {
              const t = job.hasRepair
                ? (tab === "Anamnese" ? job.text : job.befundText)
                : (tab === "Anamnese" ? out : befundOut);
              copyFormatted(t);
              toast("Kopiert");
            }}
            extraButtons={hasTranscript ? [
              { label: "Transkript ↓", onClick: () => downloadTranscript(lastJobId) }
            ] : []} />

          <RepairBundle job={job} ops={jobOps} toast={toast} />

          <FeedbackButton jobId={lastJobId} workflow="anamnese" context={tab === "Anamnese" ? "anamnese" : "befund"} toast={toast} />

          {(out || befundOut || draftDirty || selbst || befunde || audio || txtFile || style) && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setSelbst(null); setBefunde(null); setAudio(null);
                setTxtFile(null); setStyle(null);
                // B1: setText/setDx/setStyleText geht jetzt durch updateDraft
                // - aber clearDraft() ist sauberer (setzt ALLE Text-Felder
                // inkl. prompt/befundVorlage auf Defaults zurueck und entfernt
                // den localStorage-Eintrag).
                clearDraft();
                setOut(""); setBefundOut(""); setOutWarn(null);
                jobOps.reset();
                setLastJobId(null); setHasTranscript(false);
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

  const [out, setOut]             = useState("");
  const [outWarn, setOutWarn]       = useState(null);
  const [job, jobOps]               = useJobResult();
  const [lastJobId, setLastJobId] = useState(null);
  const [busy, setBusy]           = useState(false);
  const [currentJobId, setCurrentJobId] = useState(null);

  // B2: attach-Pattern (wie P2 oben). attachedRef verhindert Doppel-Attach
  // im Race zwischen Resume-Banner und Auto-Resume.
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
    if (!resumeJob || resumeJob.page !== "p2b") return;
    attach(resumeJob.jobId);
    onResumed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeJob]);

  useResumeWorkflowJob("akutantrag", attach, !resumeJob);

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
    // B2: non-blocking Pfad (startJob -> attach) wie in P2
    setBusy(true);
    setOut(""); setOutWarn(null);
    jobOps.reset();
    setLastJobId(null);
    attachedRef.current = null;
    try {
      // v19.12: patientName/geschlecht kommen aus der Antragsvorlage
      // (Backend-Extraktion) - kein manueller Override mehr.
      const jobId = await startJob("akutantrag", prompt, "", {
        antragsvorlage: antrag,   // Pflicht: Anamnese, Befund, Diagnosen
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

          <Card num="B" title="Stilvorlage (Textbeispiel)" badge="opt" open={false}>
            <InputTabs tabs={[
              { id:"file", icon:"📎", label:"Datei"   },
              { id:"text", icon:"✏️", label:"Text C&P" },
            ]}>
              {(activeTab) => (<>
                {activeTab === "file" && (
                  <Dropzone label="Beispieltext hochladen" hint="PDF, DOCX oder TXT" accept=".pdf,.docx,.txt" icon="&#128221;" file={style} onFile={setStyle} />
                )}
                {activeTab === "text" && (<>
                  <textarea rows={5} placeholder="Beispiel-Akutantrag einfügen ..." value={styleText} onChange={(e) => setStyleText(e.target.value)} style={{marginTop:0}} />
                  <div className="field-note">Schreibstil des eingefügten Texts wird übernommen. Wenn keine Akutantrag-Stilvorlage vorliegt, fällt das Backend auf Verlängerungs-Stilbeispiele zurück.</div>
                </>)}
              </>)}
            </InputTabs>
          </Card>

          <Card num="C" title="Fokus-Themen" badge="opt" open={false}>
            <label className="field-label">Schwerpunkte für die Akutbegründung</label>
            <textarea rows={4}
              placeholder={"Optionale Schwerpunkte, z.B.:\n– Akute Suizidalität\n– Dekompensation nach Auslöser-Ereignis\n– Ambulant nicht ausreichend, weil ..."}
              value={fokus}
              onChange={e => setFokus(e.target.value)}
            />
            <div className="field-note">Werden als Hinweis an das Modell weitergegeben – nur Themen die in der Antragsvorlage belegt sind werden aufgegriffen.</div>
          </Card>

          <Card num="D" title="Prompt/Modell anpassen (advanced)" badge="opt" open={false}>
            <JobModelPicker workflow="akutantrag" value={jobModel} onChange={setJobModel} />
            <PromptEditor value={prompt} onChange={setPrompt} def={P_AKUT} />
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
                  disabled={!antrag}
                  title={!antrag ? "Akutantrag (zu vervollständigen) erforderlich" : ""}
                >Akutantrag erstellen</button>
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

          <FeedbackButton jobId={lastJobId} workflow="akutantrag" toast={toast} />

          {(out || draftDirty || antrag || style) && (
            <div style={{marginTop:12, textAlign:"right"}}>
              <button className="btn-secondary" onClick={() => {
                setAntrag(null); setStyle(null);
                clearDraft();  // B2: setzt ALLE Text-Felder auf Default + raeumt localStorage
                setOut(""); setOutWarn(null); setLastJobId(null);
                jobOps.reset();
                attachedRef.current = null;
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
