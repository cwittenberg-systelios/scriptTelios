// ────────────────────────────────────────────────────────────────────────────
// src/workflow-run.jsx — gemeinsames Panel-Skelett fuer die Job-Workflows
// (v19.21 Sprint S5). Vorher trug jedes Panel (P2, P2b, P3, P3b, P4, P6)
// dieselben ~60 Zeilen: Output-State, attach()+pollJob, Resume-Banner-Effekt,
// Auto-Resume, cancelRun(), run()-Vorlauf und die Action-Bar-JSX
// (jscpd: 21 Klone, 371 Zeilen). Hier lebt das einmal.
//
//   useWorkflowRun({ workflow, page, resumeJob, onResumed, onResult, onError })
//     -> { out, outWarn, job, jobOps, lastJobId, busy, currentJobId,
//          displayText, start(prompt, userContent, files), cancel(), reset() }
//
//   <WorkflowActionBar busy onRun onCancel runLabel disabled title>
//     {optionale linke Controls, z.B. <KlientControls/>}
//   </WorkflowActionBar>
//
//   <KlientControls geschlecht onGeschlecht kuerzel onKuerzel />
//   buildPatientName(kuerzel, geschlecht) -> "Frau K." | "Herr K." | "K." | null
// ────────────────────────────────────────────────────────────────────────────
import { useEffect, useRef, useState } from "react";
import { apiFetch, getApiBase, pollJob, startJob } from "./api.js";
import { useJobResult, useResumeWorkflowJob } from "./hooks.jsx";
import { buildPatientName, clearActiveJob, friendlyError, getEmptyWarning, loadActiveJob } from "./shared.js";

const POLL_MS = 1200;

function useWorkflowRun({ workflow, page, resumeJob, onResumed, onResult, onError }) {
  const [out, setOut]                   = useState("");
  const [outWarn, setOutWarn]           = useState(null);
  const [job, jobOps]                   = useJobResult();
  const [lastJobId, setLastJobId]       = useState(null);
  const [busy, setBusy]                 = useState(false);
  const [currentJobId, setCurrentJobId] = useState(null);

  // Verhindert Doppel-Attach im Race zwischen Resume-Banner (resumeJob-Prop)
  // und Auto-Resume (useResumeWorkflowJob) - beide koennen beim Mount
  // denselben laufenden Job finden.
  const attachedRef = useRef(null);
  // Callbacks in Refs, damit attach() (in Effekten referenziert) immer die
  // aktuelle Panel-Logik sieht, ohne die Effekte neu zu binden.
  const onResultRef = useRef(onResult); onResultRef.current = onResult;
  const onErrorRef  = useRef(onError);  onErrorRef.current  = onError;

  function attach(jobId) {
    if (attachedRef.current === jobId) return;
    attachedRef.current = jobId;
    setBusy(true);
    setCurrentJobId(jobId);
    pollJob(jobId, POLL_MS)
      .then(j => {
        if (!j) return;  // cancelled
        setOut(j.result_text || "");
        setOutWarn(getEmptyWarning(j.result_text));
        jobOps.applyOriginal(j);
        setLastJobId(jobId);
        onResultRef.current?.(j);
      })
      .catch(e => {
        const msg = "Fehler: " + friendlyError(e);
        if (onErrorRef.current) onErrorRef.current(msg, e);
        else setOut(msg);
      })
      .finally(() => { setBusy(false); setCurrentJobId(null); });
  }

  // Resume-Banner-Prop (App-Root nach F5). Hoehere Prioritaet als Auto-Resume.
  useEffect(() => {
    if (!resumeJob || resumeJob.page !== page) return;
    attach(resumeJob.jobId);
    onResumed?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeJob]);

  // Auto-Resume beim Mount: laufenden Job dieses Workflows wiederfinden.
  useResumeWorkflowJob(workflow, attach, !resumeJob);

  function cancel() {
    const jobId = currentJobId || loadActiveJob()?.jobId;
    if (jobId) {
      apiFetch(`${getApiBase()}/jobs/${jobId}`, { method: "DELETE" }).catch(() => {});
    }
    clearActiveJob();
    setBusy(false);
    setCurrentJobId(null);
    attachedRef.current = null;
  }

  // Output-Zustand leeren (vor einem neuen Lauf und beim "Neu"-Button).
  function reset() {
    setOut(""); setOutWarn(null);
    setLastJobId(null);
    jobOps.reset();
    attachedRef.current = null;
  }

  // Non-blocking Pfad: POST -> job_id -> attach. Der Job laeuft im Backend
  // weiter, auch bei Tab-Wechsel oder F5; useResumeWorkflowJob findet ihn.
  async function start(prompt, userContent, files) {
    setBusy(true);
    reset();
    try {
      const jobId = await startJob(workflow, prompt, userContent, files);
      attach(jobId);
      return jobId;
    } catch (e) {
      const msg = "Fehler: " + friendlyError(e);
      if (onErrorRef.current) onErrorRef.current(msg, e);
      else setOut(msg);
      setBusy(false);
      setCurrentJobId(null);
      return null;
    }
  }

  return {
    out, outWarn, job, jobOps, lastJobId, busy, currentJobId,
    displayText: job.hasRepair ? job.text : out,
    start, cancel, reset,
  };
}

function KlientControls({ geschlecht, onGeschlecht, kuerzel, onKuerzel }) {
  return (
    <div style={{display:"flex", alignItems:"center", gap:6, marginRight:"auto", flexWrap:"wrap"}}>
      <span style={{fontSize:11, fontWeight:600, color:"var(--st-text-soft)", textTransform:"uppercase", letterSpacing:"0.06em"}}>Klient</span>
      {[
        { val:"w", label:"♀ weiblich" },
        { val:"m", label:"♂ männlich" },
      ].map(({ val, label }) => (
        <button key={val} onClick={() => onGeschlecht(val)} style={{
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
        <input type="text" value={kuerzel} onChange={e => onKuerzel(e.target.value)}
          placeholder="K." maxLength={8} style={{
            width:48, padding:"3px 6px", fontSize:12, borderRadius:3,
            border:"1px solid var(--st-gray-border)", background:"var(--st-bg)",
            color:"var(--st-text)", fontFamily:"inherit",
          }} />
      </div>
    </div>
  );
}

// Start/Abbrechen-Leiste. `children` = optionale Controls links (Klient,
// Kennung, ...); ohne children ein Spacer, damit der Button rechts bleibt.
function WorkflowActionBar({ busy, onRun, onCancel, runLabel, disabled = false, title = "", children }) {
  return (
    <div className="action-bar">
      {children ?? <div style={{marginRight:"auto"}} />}
      {busy
        ? <button className="btn-secondary" onClick={onCancel}>✕ Abbrechen</button>
        : <button className="btn-primary" onClick={onRun} disabled={disabled} title={title}>{runLabel}</button>
      }
    </div>
  );
}

export { useWorkflowRun, WorkflowActionBar, KlientControls, buildPatientName };
