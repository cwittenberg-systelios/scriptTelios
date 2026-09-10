// ────────────────────────────────────────────────────────────────────────────
// src/hooks.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useCallback, useEffect } from "react";
import { apiFetch, getApiBase } from "./api.js";
import { pickQualityCheck } from "./shared.js";


// ── useDraftCache (Sprint Draft-Persistence B0) ─────────────────────────────
// Persistiert Form-Felder in localStorage, sodass halbausgefuellte Eingaben
// Tab-Wechsel und Page-Reload ueberleben.
//
// Was persistiert wird:
//   - Strings, Zahlen, Booleans, Arrays, Plain Objects
//
// Was NICHT persistiert wird (bewusst gefiltert):
//   - File-Instanzen   (localStorage-Quota ~5-10MB, Files ggf. >10MB)
//   - Blob-Instanzen   (gleicher Grund)
//   - Funktionen       (nicht serialisierbar)
//   - Werte die identisch zum Default sind (Option-3-Strategie fuer Prompts:
//     wenn ein Prompt-Update im Code passiert, sehen User keinen alten
//     Cache-Wert sondern den neuen Default. Erst wenn der User aktiv
//     editiert, wird gecached.)
//
// Signatur:
//   const [draft, updateDraft, clearDraft] = useDraftCache(key, defaultDraft);
//
// Beispiel:
//   const [draft, updateDraft, clearDraft] = useDraftCache("st_draft_p2", {
//     text: "", kuerzel: "", prompt: P_ANAMNESE,
//   });
//   <input value={draft.text} onChange={(e) => updateDraft({ text: e.target.value })} />
//   <button onClick={clearDraft}>Reset</button>
function useDraftCache(localStorageKey, defaultDraft) {
  // Lade-Logik beim ersten Render: aus localStorage parsen, ueber default mergen.
  // Falls localStorage nicht verfuegbar (Inkognito mit deaktiviertem Storage,
  // Quota-Ueberschreitung beim Lesen): silent fallback auf Default.
  const [draft, setDraft] = useState(() => {
    try {
      const raw = localStorage.getItem(localStorageKey);
      if (!raw) return { ...defaultDraft };
      const cached = JSON.parse(raw);
      // v19.12: nur Keys mergen, die der aktuelle Default kennt. Entfernte
      // Felder (z.B. geschlecht/kuerzel in P2b/P3/P3b/P4) bleiben sonst als
      // verwaiste Cache-Keys im Draft und halten draftDirty dauerhaft an.
      const merged = { ...defaultDraft };
      for (const k of Object.keys(defaultDraft)) {
        if (k in cached) merged[k] = cached[k];
      }
      return merged;
    } catch (_) {
      return { ...defaultDraft };
    }
  });

  // Persist-Logik: schreibt bei jeder draft-Aenderung. Filterung wie oben.
  // useEffect ist asynchron - kein UI-Block bei groesseren Drafts.
  useEffect(() => {
    try {
      const persistable = {};
      for (const [k, v] of Object.entries(draft)) {
        // Hard-Skip: nicht-serialisierbare Typen
        if (typeof v === "function") continue;
        if (typeof File !== "undefined" && v instanceof File) continue;
        if (typeof Blob !== "undefined" && v instanceof Blob) continue;
        // Soft-Skip: identisch zum Default (Option-3 fuer Prompt-Felder)
        if (v === defaultDraft[k]) continue;
        // Bei Arrays/Objects: einfacher JSON-String-Compare als Default-Check.
        // Akzeptabler Overhead fuer typische Draft-Felder.
        if (typeof v === "object" && v !== null && defaultDraft[k] !== undefined) {
          try {
            if (JSON.stringify(v) === JSON.stringify(defaultDraft[k])) continue;
          } catch (_) { /* zyklische Objekte etc. - sicherheitshalber persistieren */ }
        }
        persistable[k] = v;
      }
      // Wenn ALLES default ist: Eintrag komplett entfernen (kein leeres {} liegen lassen)
      if (Object.keys(persistable).length === 0) {
        localStorage.removeItem(localStorageKey);
      } else {
        localStorage.setItem(localStorageKey, JSON.stringify(persistable));
      }
    } catch (_) {
      // Quota voll oder Storage disabled - Fail-Silent. Der Draft lebt
      // weiter im React-State; nur F5-Persistenz geht verloren.
    }
  }, [draft, localStorageKey]);

  const updateDraft = useCallback((patch) => {
    setDraft(prev => ({ ...prev, ...patch }));
  }, []);

  const clearDraft = useCallback(() => {
    setDraft({ ...defaultDraft });
    try { localStorage.removeItem(localStorageKey); } catch (_) {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [localStorageKey]);

  return [draft, updateDraft, clearDraft];
}


// ── useResumeWorkflowJob (Sprint Draft-Persistence B1) ──────────────────────
// Beim Mount einer P-Komponente: sucht in den letzten Jobs des angegebenen
// Workflows nach einem noch laufenden Job (pending/running). Wenn einer
// gefunden wird, ruft die `attach`-Callback - die Komponente kann sich dann
// transparent wieder an den Job heften und Progress/Output anzeigen.
//
// Use Case: Therapeut startet in P3 einen Antrag, wechselt zu P1, kommt
// zurueck zu P3. Die Komponente wurde unmounted, der Job laeuft im Backend
// weiter. useResumeWorkflowJob findet ihn und re-attached automatisch.
//
// `enabled`-Parameter: false ausschalten falls der bestehende Resume-Banner-
// Mechanismus (resumeJob-Prop aus dem App-Root) bereits aktiv ist - sonst
// wuerden BEIDE attach() aufrufen.
//
// Signatur:
//   useResumeWorkflowJob(workflow, attach, enabled = true)
//
// Beispiel in einer P-Komponente:
//   function attach(jobId) { ... pollJob + setState ... }
//   useResumeWorkflowJob("anamnese", attach, !resumeJob);
function useResumeWorkflowJob(workflow, attach, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    apiFetch(`${getApiBase()}/jobs?workflow=${workflow}&limit=10`)
      .then(r => r.ok ? r.json() : [])
      .then(list => {
        if (cancelled) return;
        // Bewusst nur laufende Jobs - keine alten done/error/cancelled. Sonst
        // wuerde der User unerwartet einen alten Output sehen statt eines
        // leeren Formulars.
        const running = list.find(j => j.status === "pending" || j.status === "running");
        if (running) attach(running.job_id);
      })
      .catch(() => {});
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflow, enabled]);
}


function useJobResult() {
  // Original-Version (das was generate() liefert oder resume befuellt)
  const [origText,    setOrigText]    = useState("");
  const [origBefund,  setOrigBefund]  = useState("");
  const [origQC,      setOrigQC]      = useState(null);
  const [origJobId,   setOrigJobId]   = useState(null);

  // Repair-Version (nach erfolgreichem repair()-Call)
  const [repairText,    setRepairText]    = useState("");
  const [repairBefund,  setRepairBefund]  = useState("");
  const [repairQC,      setRepairQC]      = useState(null);
  const [repairJobId,   setRepairJobId]   = useState(null);

  // Tab-Aktivierung
  const [activeVersion, setActiveVersion] = useState("original");

  // Panel-Auswahl-State (Reset zwischen Versionen)
  const [acceptedCodes, setAcceptedCodes] = useState([]);
  const [userHint,      setUserHint]      = useState("");

  // Repair-Modal-State
  const [showRepairModal, setShowRepairModal] = useState(false);
  const [modalPrompt,     setModalPrompt]     = useState("");
  const [repairBusy,      setRepairBusy]      = useState(false);
  const [repairError,     setRepairError]     = useState(null);
  // v19.4 C-1: laufender Repair-Job (treibt das JobProgressBar nach Modal-Close).
  const [repairProgressJobId, setRepairProgressJobId] = useState(null);

  const hasRepair = !!repairJobId;

  const applyOriginal = useCallback((jobOrResult) => {
    if (!jobOrResult) {
      setOrigText(""); setOrigBefund(""); setOrigQC(null); setOrigJobId(null);
      return;
    }
    setOrigText(jobOrResult.text ?? jobOrResult.result_text ?? "");
    setOrigBefund(jobOrResult.befundText ?? jobOrResult.befund_text ?? "");
    setOrigQC(pickQualityCheck(jobOrResult));
    setOrigJobId(jobOrResult.jobId ?? jobOrResult.job_id ?? null);
    setActiveVersion("original");
    setAcceptedCodes([]);
    setUserHint("");
    // Bei neuer Generierung Repair-Version verwerfen
    setRepairText(""); setRepairBefund(""); setRepairQC(null); setRepairJobId(null);
    setShowRepairModal(false); setModalPrompt(""); setRepairError(null);
    setRepairProgressJobId(null);
  }, []);

  const applyRepair = useCallback((repairResult) => {
    if (!repairResult) return;
    setRepairText(repairResult.text ?? "");
    setRepairBefund(repairResult.befundText ?? "");
    setRepairQC(pickQualityCheck(repairResult));
    setRepairJobId(repairResult.jobId ?? null);
    setActiveVersion("repair");
    setShowRepairModal(false);
    setRepairError(null);
    setRepairProgressJobId(null);
    // Auswahl-State leeren - bei zweitem Repair startet er bei 0
    setAcceptedCodes([]);
    setUserHint("");
  }, []);

  const reset = useCallback(() => {
    setOrigText(""); setOrigBefund(""); setOrigQC(null); setOrigJobId(null);
    setRepairText(""); setRepairBefund(""); setRepairQC(null); setRepairJobId(null);
    setActiveVersion("original");
    setAcceptedCodes([]); setUserHint("");
    setShowRepairModal(false); setModalPrompt("");
    setRepairBusy(false); setRepairError(null);
    setRepairProgressJobId(null);
  }, []);

  const toggleCode = useCallback((code) => {
    setAcceptedCodes(prev =>
      prev.includes(code) ? prev.filter(c => c !== code) : [...prev, code]
    );
  }, []);

  // Aktive Version -> auszugebender Text + QC
  const isRepairActive = activeVersion === "repair" && hasRepair;
  const text         = isRepairActive ? repairText   : origText;
  const befundText   = isRepairActive ? repairBefund : origBefund;
  const qualityCheck = isRepairActive ? repairQC     : origQC;
  // Der Job-ID den Repair-Operationen targeten muessen: IMMER der Original.
  // Repair-on-Repair wuerde gegen den ersten Repair-Job laufen - laut Plan
  // Phase C: nur Original + letzte Repair-Version.
  const repairTargetJobId = origJobId;

  return [
    {
      // Aktiv
      text, befundText, qualityCheck,
      // Versionen
      origText, origBefund, origQC, origJobId,
      repairText, repairBefund, repairQC, repairJobId,
      hasRepair, activeVersion, repairTargetJobId,
      // UI-Auswahl
      acceptedCodes, userHint,
      // Modal
      showRepairModal, modalPrompt, repairBusy, repairError,
      repairProgressJobId,
    },
    {
      applyOriginal, applyRepair, reset,
      toggleCode, setUserHint,
      setActiveVersion,
      openModal: (prompt) => { setModalPrompt(prompt); setShowRepairModal(true); },
      closeModal: () => { setShowRepairModal(false); },
      setModalPrompt,
      setRepairBusy, setRepairError,
      setRepairProgressJobId,
    },
  ];
}

// ── Stilprofil-Verwaltung ─────────────────────────────────────────
// v13: Workflow-Liste wird primaer vom Backend geladen (/api/workflows),
// der hardcoded Block hier dient nur als Fallback fuer den Fall dass
// das Backend offline/inkompatibel ist. Single Source of Truth liegt
// in Backend-File app/core/workflows.py.
const DOKUMENTTYPEN_FALLBACK = [
  { value: "dokumentation",      label: "Gesprächsdokumentation" },
  { value: "anamnese",           label: "Anamnese" },
  { value: "verlaengerung",      label: "Verlängerungsantrag" },
  { value: "folgeverlaengerung", label: "Folgeverlängerung" },
  { value: "akutantrag",         label: "Akutantrag" },
  { value: "entlassbericht",     label: "Entlassbericht" },
];

// Strukturelle Workflows aus Backend-Manifest. Wird in P5 fuer den
// "hatAbschnitte"-Hinweis benutzt - aktuell zeigen wir den Hinweis fuer
// Verlaengerung und Entlassbericht (beide sind strukturell). Wenn das
// Backend ein Workflow als is_structural meldet, taucht es hier automatisch
// auf - keine separate JSX-Aenderung noetig.
const STRUCTURAL_WORKFLOWS_FALLBACK = new Set([
  "anamnese", "verlaengerung", "folgeverlaengerung", "akutantrag", "entlassbericht",
]);

// Hook: laedt das Workflow-Manifest vom Backend, faellt auf Hardcoded-Liste
// zurueck wenn der Endpoint nicht antwortet. Cached in useState fuer den
// Lifecycle der Component - bei Re-Mount wird neu geladen.
function useWorkflowManifest() {
  const [workflows, setWorkflows] = useState(DOKUMENTTYPEN_FALLBACK);
  const [structural, setStructural] = useState(STRUCTURAL_WORKFLOWS_FALLBACK);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${getApiBase()}/workflows`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        const wfs = data.workflows || [];
        if (wfs.length === 0) return;  // leerer Response - Fallback behalten
        setWorkflows(wfs.map(w => ({ value: w.key, label: w.label, ...w })));
        setStructural(new Set(wfs.filter(w => w.is_structural).map(w => w.key)));
        setLoaded(true);
      } catch (_e) {
        // Backend nicht erreichbar oder altes Backend ohne Endpoint:
        // Fallback aus Hardcoded-Liste bleibt aktiv. Kein Toast - das
        // ist ein Soft-Failure, der UI laeuft normal weiter.
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return { workflows, structural, loaded };
}

export { useDraftCache, useResumeWorkflowJob, useJobResult, useWorkflowManifest };
