// ────────────────────────────────────────────────────────────────────────────
// src/panels/P1.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { apiFetch, getApiBase, startJob } from "../api.jsx";
import { AudioInput } from "../audio.jsx";
import { useDraftCache, useJobResult } from "../hooks.jsx";
import { JobDetailPane, JobListPane } from "../joblist.jsx";
import { P_DOKU } from "../prompt-defaults.jsx";
import { clearActiveJob, friendlyError } from "../shared.jsx";
import { Card, Dropzone, InputTabs, PromptEditor, JobModelPicker } from "../ui.jsx";



// Hilfsfunktion: leerer Entwurf mit eindeutiger ID.
// Die ID-Praefix "draft-" macht die Unterscheidung Entwurf vs Job in der
// Selection-State eindeutig, ohne tuple-Logik.
function _emptyDraft() {
  const id = `draft-${Date.now()}-${Math.random().toString(36).slice(2,8)}`;
  return {
    id,
    audio: null,
    txtFile: null,
    text: "",
    bullets: "",
    style: null,
    styleText: "",
    prompt: P_DOKU,
    geschlecht: "auto",
    kuerzel: "",
    starting: false,
    createdAt: Date.now(),
  };
}

// Sprint Draft-Persistence B3: text-only Felder eines Drafts. Wird vom
// useDraftCache-Hook persistiert. Whitelist-Filter im updateDraft sorgt
// dafuer, dass File-Felder (audio, txtFile, style) und transiente Flags
// (id, starting, createdAt) NICHT ins localStorage gelangen.
const P1_DRAFT_TEXT_DEFAULT = {
  text: "", bullets: "", kuerzel: "", geschlecht: "auto",
  prompt: P_DOKU, styleText: "",
};
const P1_TEXT_FIELDS = Object.keys(P1_DRAFT_TEXT_DEFAULT);

function P1({ toast, resumeJob, onResumed }) {
  // Modellwahl fuer DIESEN Job (JobModelPicker); leer = globaler Fallback
  const [jobModel, setJobModel] = useState("");
  // Multi-Draft-State (NEU, Sprint B Part 2)
  // drafts: lokale Entwuerfe (Text-Felder ueberleben Reload via useDraftCache,
  //         File-Felder sind in-memory-only)
  // jobs:   Jobs vom Backend (laufend, fertig, fehlgeschlagen)
  // selected: { type: "draft"|"job", id: string } - was ist gerade im Detail-/Form-Pane?

  // Sprint Draft-Persistence B3: Cache fuer Text-Felder.
  // Liefert die letzten gespeicherten Werte (oder Defaults) - wird beim Initial-
  // Draft-Bau eingemerget, sodass halbausgefuellte Formulare F5 ueberleben.
  const [textCache, updateTextCache, clearTextCache] =
    useDraftCache("st_draft_p1", P1_DRAFT_TEXT_DEFAULT);

  // initialDraft wird einmalig beim Mount aus _emptyDraft() + textCache gebaut.
  // Wichtig: useMemo mit [] - textCache wird nur beim ersten Render konsumiert,
  // spaetere Cache-Aenderungen schreiben direkt ueber updateTextCache und sind
  // bereits im drafts-State sichtbar.
  const initialDraft = useMemo(
    () => ({ ..._emptyDraft(), ...textCache }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );
  const [drafts, setDrafts]               = useState([initialDraft]);
  const [jobs, setJobs]                   = useState([]);
  const [selected, setSelected]           = useState({ type: "draft", id: initialDraft.id });
  const [detail, setDetail]               = useState(null); // voller Job-Dict (nur bei type=job)
  const [listLoading, setListLoading]     = useState(true);
  const [listError, setListError]         = useState(null);

  // useJobResult-Bundle (fuer RepairBundle/Output); befuellt beim Selektieren
  // eines fertigen Jobs via applyOriginal.
  const [jobState, jobOps] = useJobResult();

  // Aktueller Draft (falls type=draft), sonst null
  const currentDraft = selected?.type === "draft"
    ? drafts.find(d => d.id === selected.id)
    : null;

  // Patch-Helfer fuer einen einzelnen Draft-Eintrag.
  // updateDraft(id, {text: "..."}) - merget patch ins Draft-Objekt.
  // B3: Text-Felder werden zusaetzlich in den useDraftCache geschrieben.
  // File-Felder (audio, txtFile, style) und transiente Flags (starting)
  // werden bewusst NICHT gecached - sie ueberleben Reload nicht.
  const updateDraft = useCallback((id, patch) => {
    setDrafts(prev => prev.map(d => d.id === id ? { ...d, ...patch } : d));
    const textPatch = {};
    for (const k of P1_TEXT_FIELDS) {
      if (k in patch) textPatch[k] = patch[k];
    }
    if (Object.keys(textPatch).length > 0) updateTextCache(textPatch);
  }, [updateTextCache]);

  // ── Liste laden + Polling alle 5s ───────────────────────────────────
  const reloadJobs = useCallback(async () => {
    try {
      const r = await apiFetch(`${getApiBase()}/jobs?workflow=dokumentation`);
      if (r.ok) {
        const data = await r.json();
        setJobs(data);
        setListError(null);
      } else {
        setListError("Liste konnte nicht geladen werden (" + r.status + ")");
      }
    } catch (e) {
      setListError(friendlyError(e));
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    reloadJobs();
    const id = setInterval(reloadJobs, 5000);
    return () => clearInterval(id);
  }, [reloadJobs]);

  // ── Resume: Banner verbrauchen, Job direkt selektieren ──────────────
  // Mit Multi-Job-P1 brauchen wir den Resume-Mechanismus nicht mehr fuer
  // Polling - der Job ist in der Liste sichtbar. Den Banner-State verbrauchen
  // wir trotzdem damit der Parent ihn deaktiviert.
  useEffect(() => {
    if (resumeJob && resumeJob.page === "p1") {
      setSelected({ type: "job", id: resumeJob.jobId });
      clearActiveJob();
      onResumed();
    }
  }, [resumeJob, onResumed]);

  // ── Detail-Load fuer den selektierten Job (kein Polling) ────────────
  // Strategie: einmal beim Selektieren fetchen. Wenn der Job dann noch
  // laeuft, mountet <Output> den <JobProgressBar> mit SSE - dessen
  // onTerminal-Callback triggert ein erneutes fetchDetail wenn der Job
  // fertig wird. So gibt es genau EINE SSE-Verbindung pro Job, keinen
  // Doppel-Subscribe und keinen unnoetigen Polling-Verkehr.
  //
  // Edge Cases werden vom JobProgressBar-internen Polling-Fallback
  // abgedeckt: wenn die SSE bricht (Cloudflare-Timeout, HTTP/2-Quirk),
  // pollt JobProgressBar selber und ruft onTerminal beim done-Status.
  const refetchDetail = useCallback(async (jobId) => {
    try {
      const r = await apiFetch(`${getApiBase()}/jobs/${jobId}`);
      if (r.status === 404) {
        // Job wurde von woanders geloescht
        setSelected(prev => (prev?.type === "job" && prev.id === jobId) ? null : prev);
        return;
      }
      if (!r.ok) return;
      const j = await r.json();
      // Race-Guard: User koennte inzwischen einen anderen Job ausgewaehlt haben
      setDetail(prev => {
        // setSelected ist async; wir vergleichen ueber den uebergebenen jobId
        // und vertrauen darauf dass setDetail vom nachfolgenden useEffect-Run
        // ueberschrieben wird falls selected wechselt
        return j.job_id === jobId ? j : prev;
      });
      if (j.status === "done" || j.status === "cancelled") {
        jobOps.applyOriginal(j);
      }
    } catch (_) { /* still bleiben, JobProgressBar-Polling-Fallback laeuft eh */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobOps]);

  useEffect(() => {
    if (selected?.type !== "job") { setDetail(null); jobOps.reset(); return; }
    refetchDetail(selected.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  // onTerminal-Callback fuer <JobProgressBar> via <Output> via <JobDetailPane>:
  // wird gefeuert sobald der selektierte Job durch die SSE oder den Polling-
  // Fallback einen terminalen Status erreicht. Triggert ein einmaliges
  // refetchDetail, das das Detail-Dict mit result_text/befund/etc. fuellt
  // und jobOps.applyOriginal() ruft (damit RepairBundle den Job kennt).
  const onJobTerminal = useCallback(() => {
    if (selected?.type === "job") refetchDetail(selected.id);
  }, [selected, refetchDetail]);

  // ── Draft-Aktionen ──────────────────────────────────────────────────
  // Sprint B v2: kein newDraft() mehr - es gibt immer GENAU EINEN Entwurf,
  // der entweder leer ist (= "+ Neues Gespräch"-Button-Look in der Liste)
  // oder gefuellt (= aktuelle Arbeit). Wenn der User die aktuelle Arbeit
  // verwerfen will, ersetzt discardDraft den Entwurf durch einen frischen
  // leeren - der dann wieder als Button erscheint.
  function discardDraft(id) {
    const fresh = _emptyDraft();
    setDrafts(prev => prev.map(d => d.id === id ? fresh : d));
    if (selected?.type === "draft" && selected.id === id) {
      setSelected({ type: "draft", id: fresh.id });
    }
    clearTextCache();  // B3: localStorage-Eintrag aufraeumen
  }

  // ── Generieren (non-blocking, draft -> job) ─────────────────────────
  async function run() {
    const d = currentDraft;
    if (!d) return;
    updateDraft(d.id, { starting: true });

    const k = d.kuerzel.trim().replace(/\.?$/, ".");
    // v15 Bug F2: Keine Beispieltexte wie "die Klientin/Klient" mehr
    const nameHinweis = d.kuerzel.trim()
      ? ` Verwende als Namenskürzel durchgehend "${k}" (z.B. "Frau ${k}" oder "Herr ${k}").`
      : "";
    const geschlechtHinweis = {
      "w":    `\n\nKLIENT-GESCHLECHT: weiblich – verwende konsequent weibliche Pronomen und Endungen.${nameHinweis}`,
      "m":    `\n\nKLIENT-GESCHLECHT: männlich – verwende konsequent männliche Pronomen und Endungen.${nameHinweis}`,
      "auto": `\n\nKLIENT-GESCHLECHT: Leite das Geschlecht aus dem Transkript ab (Namen, Pronomen, Anreden). Falls nicht erkennbar, verwende neutrale Formen.${nameHinweis}`,
    }[d.geschlecht];

    const promptMitGeschlecht = d.prompt + geschlechtHinweis;

    let patientNameExplicit = null;
    if (d.kuerzel.trim()) {
      const kurz = k;
      if (d.geschlecht === "w")      patientNameExplicit = `Frau ${kurz}`;
      else if (d.geschlecht === "m") patientNameExplicit = `Herr ${kurz}`;
      else                            patientNameExplicit = kurz;
    }

    try {
      const jobId = await startJob("dokumentation", promptMitGeschlecht, d.text || "", {
        audio:        d.audio,
        txtFile:      d.txtFile || null,
        style:        d.style,
        styleText:    d.styleText || null,
        bullets:      d.bullets || null,
        model:        jobModel || null,
        patientName:  patientNameExplicit,
      });
      // Submit erfolgreich. Den verbrauchten Draft durch einen frischen leeren
      // ersetzen - die Liste behaelt GENAU EINEN Entwurf (Invariante).
      // Der leere Draft erscheint dann automatisch als "+ Neues Gespräch"-Button.
      setDrafts(prev => prev.map(x => x.id === d.id ? _emptyDraft() : x));
      setSelected({ type: "job", id: jobId });
      reloadJobs();
      clearTextCache();  // B3: Form ist abgesendet - localStorage-Eintrag weg
    } catch (e) {
      toast("Fehler: " + friendlyError(e));
      updateDraft(d.id, { starting: false });
    }
  }

  // ── Job-Aktionen ────────────────────────────────────────────────────
  async function onDeleteJob(jobId, label) {
    if (!confirm(`Gespräch "${label}" endgültig löschen?`)) return;
    try {
      const r = await apiFetch(`${getApiBase()}/jobs/${jobId}/permanent`, { method: "DELETE" });
      if (!r.ok) {
        if (r.status === 409) toast("Job läuft noch – erst abbrechen.");
        else toast("Löschen fehlgeschlagen: " + r.statusText);
        return;
      }
      if (selected?.type === "job" && selected.id === jobId) {
        // Auf den ersten Entwurf zurueckfallen (oder einen neuen anlegen)
        if (drafts.length > 0) setSelected({ type: "draft", id: drafts[0].id });
        else {
          const fresh = _emptyDraft();
          setDrafts([fresh]);
          setSelected({ type: "draft", id: fresh.id });
        }
      }
      reloadJobs();
    } catch (e) {
      toast(friendlyError(e));
    }
  }

  async function onCancelJob(jobId) {
    try {
      await apiFetch(`${getApiBase()}/jobs/${jobId}`, { method: "DELETE" });
      reloadJobs();
    } catch (e) {
      toast(friendlyError(e));
    }
  }

  // ── Form-JSX (bound to currentDraft) ────────────────────────────────
  // currentDraft ist garantiert non-null wenn dieser Zweig gerendert wird
  // (run() und discardDraft() ersetzen den Entwurf, sie loeschen ihn nie -
  // damit existiert immer genau ein Entwurf).
  const formCanGenerate = currentDraft &&
    (currentDraft.audio || currentDraft.txtFile || currentDraft.text) &&
    currentDraft.kuerzel.trim();

  const formPane = currentDraft ? (
    <div className="workflow">
      <Card num="A" title="Gesprächsmaterial" badge="req" open={true}>
        <InputTabs
          tabs={[
            { id:"audio", icon:"🎙", label:"Aufnahme" },
            { id:"file",  icon:"📄", label:"Datei"    },
            { id:"text",  icon:"✏️", label:"Text"     },
          ]}
        >
          {(activeTab) => (<>
            {activeTab === "audio" && (
              <AudioInput file={currentDraft.audio} onFile={(f) => updateDraft(currentDraft.id, { audio: f })} />
            )}
            {activeTab === "file" && (
              <div style={{display:"flex",flexDirection:"column",gap:10}}>
                <div>
                  <div style={{fontSize:11,fontWeight:600,color:"var(--st-text-soft)",marginBottom:4}}>Transkript-Datei</div>
                  <Dropzone label="Transkript hochladen" hint=".txt  .docx" accept=".txt,.docx" icon="&#128196;" file={currentDraft.txtFile} onFile={(f) => updateDraft(currentDraft.id, { txtFile: f })} />
                </div>
                <div>
                  <div style={{fontSize:11,fontWeight:600,color:"var(--st-text-soft)",marginBottom:4}}>oder Audiodatei</div>
                  <Dropzone label="Audiodatei hochladen" hint=".mp3 · .m4a · .wav · .ogg · .webm · .flac" accept=".mp3,.m4a,.wav,.ogg,.webm,.flac,.aac,audio/*" icon="&#128266;"
                    file={currentDraft.audio && !currentDraft.audio.__p0recording ? currentDraft.audio : null}
                    onFile={(f) => updateDraft(currentDraft.id, { audio: f })} />
                </div>
              </div>
            )}
            {activeTab === "text" && (
              <textarea rows={6} placeholder="Gesprächsinhalt direkt hier einfügen ..."
                value={currentDraft.text}
                onChange={(e) => updateDraft(currentDraft.id, { text: e.target.value })}
                style={{marginTop:0}} />
            )}
          </>)}
        </InputTabs>
      </Card>

      <Card num="B" title="Stichpunkte" badge="opt" open={false}>
        <label className="field-label">Relevante Themen und Beobachtungen</label>
        <textarea rows={4}
          placeholder={"- Bericht ueber das Wochenende\n- Schlafprobleme anhaltend\n- Fortschritt bei Expositionsuebung ..."}
          value={currentDraft.bullets}
          onChange={(e) => updateDraft(currentDraft.id, { bullets: e.target.value })} />
        <div className="field-note">Ergaenzt oder ersetzt das Transkript bei Bedarf</div>
      </Card>

      <Card num="C" title="Stilvorlage" badge="opt" open={false}>
        <InputTabs
          tabs={[
            { id:"file", icon:"📎", label:"Datei"  },
            { id:"text", icon:"✏️", label:"Text C&P" },
          ]}
        >
          {(activeTab) => (<>
            {activeTab === "file" && (<>
              <Dropzone label="Beispieltext hochladen" hint="PDF, DOCX oder TXT" accept=".pdf,.docx,.txt" icon="&#128221;"
                file={currentDraft.style}
                onFile={(f) => updateDraft(currentDraft.id, { style: f })} />
              <div className="info-note" style={{marginTop:8}}>Der Schreibstil des hochgeladenen Textes wird bei der Generierung berücksichtigt.</div>
            </>)}
            {activeTab === "text" && (<>
              <textarea rows={6} placeholder="Beispieldokumentation hier einfügen – der Schreibstil wird übernommen ..."
                value={currentDraft.styleText}
                onChange={(e) => updateDraft(currentDraft.id, { styleText: e.target.value })}
                style={{marginTop:0}} />
              <div className="field-note">Direkt eingefügter Beispieltext als Stilvorlage</div>
            </>)}
          </>)}
        </InputTabs>
      </Card>

      <Card num="D" title="Prompt/Modell anpassen (advanced)" open={false}>
        <JobModelPicker workflow="dokumentation" value={jobModel} onChange={setJobModel} />
        <PromptEditor value={currentDraft.prompt}
          onChange={(v) => updateDraft(currentDraft.id, { prompt: v })}
          def={P_DOKU} />
      </Card>

      <div className="action-bar">
        <div style={{display:"flex", alignItems:"center", gap:6, marginRight:"auto", flexWrap:"wrap"}}>
          <span style={{fontSize:11, fontWeight:600, color:"var(--st-text-soft)", textTransform:"uppercase", letterSpacing:"0.06em"}}>Klient</span>
          {[
            { val:"w", label:"♀ weiblich" },
            { val:"m", label:"♂ männlich" },
            { val:"auto", label:"Auto"    },
          ].map(({ val, label }) => (
            <button key={val} onClick={() => updateDraft(currentDraft.id, { geschlecht: val })} style={{
              padding:"4px 10px", borderRadius:3, cursor:"pointer",
              fontSize:12, fontWeight: currentDraft.geschlecht === val ? 700 : 400,
              background: currentDraft.geschlecht === val ? "var(--st-red)" : "var(--st-gray-light)",
              color: currentDraft.geschlecht === val ? "white" : "var(--st-text-soft)",
              border: currentDraft.geschlecht === val ? "1px solid var(--st-red)" : "1px solid var(--st-gray-border)",
              transition:"all 0.12s",
            }}>{label}</button>
          ))}
          <div style={{display:"flex", alignItems:"center", gap:4, marginLeft:4}}>
            <span style={{fontSize:11, color:"var(--st-text-soft)"}}>
              Kürzel <span style={{color:"#c0392b", fontWeight:700}}>*</span>
            </span>
            <input
              type="text"
              value={currentDraft.kuerzel}
              onChange={e => updateDraft(currentDraft.id, { kuerzel: e.target.value })}
              placeholder="K."
              maxLength={8}
              required
              style={{
                width:48, padding:"3px 6px", fontSize:12, borderRadius:3,
                border: currentDraft.kuerzel.trim() ? "1px solid var(--st-gray-border)" : "1px solid #c0392b",
                background:"var(--st-bg)",
                color:"var(--st-text)", fontFamily:"inherit",
              }}
            />
          </div>
        </div>
        {currentDraft.starting
          ? <button className="btn-secondary" disabled>Wird gestartet…</button>
          : <button
              className="btn-primary"
              onClick={run}
              disabled={!formCanGenerate}
              title={
                (!currentDraft.audio && !currentDraft.txtFile && !currentDraft.text)
                  ? "Gespraechsmaterial erforderlich (Audio, Transkript oder Text)"
                  : !currentDraft.kuerzel.trim()
                    ? "Patientenkuerzel ist erforderlich"
                    : ""
              }
            >Verlaufsnotiz generieren</button>
        }
      </div>
    </div>
  ) : null;

  // Was rechts dargestellt wird haengt vom Selection-Type ab
  let rightPane;
  if (selected?.type === "job" && detail) {
    rightPane = (
      <JobDetailPane
        job={detail}
        jobState={jobState}
        jobOps={jobOps}
        toast={toast}
        onTerminal={onJobTerminal}
        onCancel={() => onCancelJob(detail.job_id)}
        onDelete={() => onDeleteJob(detail.job_id, detail.patient_kuerzel || "Gespräch")}
        onBack={() => {
          // Zurueck auf ersten Draft oder neuen anlegen
          if (drafts.length > 0) setSelected({ type: "draft", id: drafts[0].id });
          else {
            const fresh = _emptyDraft();
            setDrafts([fresh]);
            setSelected({ type: "draft", id: fresh.id });
          }
        }}
      />
    );
  } else if (selected?.type === "job" && !detail) {
    rightPane = (
      <div className="workflow">
        <div style={{padding:"20px 14px", fontSize:13, color:"var(--st-text-pale)"}}>Lade Job-Details…</div>
      </div>
    );
  } else {
    // type === "draft" oder null -> Formular zeigen
    rightPane = formPane;
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Workflow 1</div>
        <h2>Gespr&auml;chsdokumentation</h2>
        <p>Strukturierte Verlaufsnotizen aus Aufnahmen oder Transkripten</p>
      </div>
      <div className="page-body" style={{maxWidth:"none", paddingRight:24}}>
        <div style={{display:"grid", gridTemplateColumns:"240px minmax(0, 1fr)", gap:14, alignItems:"start"}}>
          <JobListPane
            jobs={jobs}
            drafts={drafts}
            selected={selected}
            onSelect={setSelected}
            onDelete={onDeleteJob}
            onCancel={onCancelJob}
            onDeleteDraft={discardDraft}
            loading={listLoading}
            error={listError}
          />
          {rightPane}
        </div>
      </div>
    </div>
  );
}

export { _emptyDraft, P1_DRAFT_TEXT_DEFAULT, P1_TEXT_FIELDS, P1 };
