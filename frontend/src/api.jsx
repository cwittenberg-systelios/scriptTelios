// ────────────────────────────────────────────────────────────────────────────
// src/api.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { clearActiveJob, saveActiveJob } from "./shared.jsx";


// sysTelios CI – angepasst an Confluence-Intranet-Screenshot:
// Sidebar: Dunkelgrau/Anthrazit (#2c2c2c) / Highlight: Dunkelrot #8b1a1a / Neutral Grau-Töne / System-Schrift
// Auth-Wrapper: nutzt window.signedFetch (Confluence-Macro) für HMAC-Auth.
// Fallback auf apiFetch() wenn nicht im Confluence-Kontext (z.B. lokale Entwicklung).
const apiFetch = (url, opts) => {
  if (typeof window !== "undefined" && window.signedFetch) {
    return window.signedFetch(url, opts);
  }
  return fetch(url, opts);
};

// v19.3: authentifizierter Download-Helper.
// Native <a href download> sendet KEINE Custom-Header (HMAC-Auth), daher
// laesst der Browser-Download das Backend mit unauthentifizierter Request
// auflaufen → 401/403. Fix: fetch() mit HMAC-Headern, dann Blob in einen
// dynamischen <a>-Tag schubsen und virtuell klicken.
//
// fallbackName: wird genutzt wenn der Server kein Content-Disposition
//               header sendet. Mit Endung (z.B. "audio.webm", "transkript.txt").
async function downloadViaApi(url, fallbackName) {
  let r;
  try {
    r = await apiFetch(url);
  } catch (e) {
    alert("Download fehlgeschlagen: Netzwerkfehler");
    return;
  }
  if (!r.ok) {
    let detail = "";
    try {
      const errJson = await r.json();
      detail = errJson.detail || JSON.stringify(errJson);
    } catch (_) { detail = r.statusText; }
    alert(`Download fehlgeschlagen (${r.status}): ${detail}`);
    return;
  }
  // Versuche Filename aus Content-Disposition zu lesen, sonst fallback
  let filename = fallbackName;
  const cd = r.headers.get("content-disposition");
  if (cd) {
    const m = cd.match(/filename\*?=(?:UTF-8'')?["']?([^"';]+)["']?/i);
    if (m) filename = decodeURIComponent(m[1]);
  }
  const blob = await r.blob();
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Browser braucht den ObjectURL noch kurz fuer den Download-Start
  setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
}

// Polling fuer eine bekannte job_id – wiederverwendbar fuer Resume.
// Stoppt automatisch wenn der Server status="cancelled" zurueckgibt.
async function pollJob(jobId, maxWaitSeconds = 1200, signal) {
  const interval = 3;
  for (let i = 0; i < maxWaitSeconds / interval; i++) {
    if (signal && signal.aborted) return null;
    await new Promise(res => setTimeout(res, interval * 1000));
    if (signal && signal.aborted) return null;
    try {
      const poll = await apiFetch(`${getApiBase()}/jobs/${jobId}`);
      if (!poll.ok) continue;
      const job = await poll.json();
      if (job.status === "done")      return job;
      if (job.status === "error")     throw new Error(job.error_msg || "Job fehlgeschlagen");
      if (job.status === "cancelled") return null;
    } catch (e) {
      if (signal && signal.aborted) return null;
      throw e;
    }
  }
  throw new Error("Timeout: Job dauert zu lange");
}

async function generate(workflow, prompt, userContent, files = {}, page = null) {
  const therapeutId = getConfluenceUser();
  const fd = new FormData();
  fd.append("workflow",   workflow);
  // v18: Feld heisst jetzt 'workflow_instructions' (frueher 'prompt').
  // Backend akzeptiert beide Namen, wir senden den neuen Namen als
  // Single-Source-of-Truth.
  fd.append("workflow_instructions", prompt);
  if (files.befundVorlage) fd.append("befund_vorlage", files.befundVorlage);
  if (therapeutId)       fd.append("therapeut_id",    therapeutId);
  if (files.patientName) fd.append("patientenname",   files.patientName);
  // v19.8: strukturiertes Geschlecht - unabhaengig vom Kuerzel-Gate. Nur
  // "w"/"m" senden; "auto" = Backend-Default (ableiten wie bisher).
  if (files.geschlecht === "w" || files.geschlecht === "m") fd.append("geschlecht", files.geschlecht);

  // 1. P0-Recording → p0_recording_id immer senden; transcript nur wenn vorhanden
  // 2. Audio-Datei (Upload) → audio-Feld
  // 3. Transkript-Datei (.txt/.docx) → transcript_file-Feld
  // 4. Text direkt → transcript-Feld
  if (files.audio && files.audio.__p0recording) {
    fd.append("p0_recording_id", String(files.audio.id));
    fd.append("priority", "high");
    if (files.audio.transcript) fd.append("transcript", files.audio.transcript);
  } else if (files.audio) {
    fd.append("transcript", userContent);
    fd.append("audio", files.audio);
  } else if (files.txtFile) {
    fd.append("transcript_file", files.txtFile);
    if (userContent) fd.append("transcript", userContent);
  } else {
    fd.append("transcript", userContent);
  }

  if (files.selbst)      fd.append("selbstauskunft",   files.selbst);
  if (files.vorbef)      fd.append("vorbefunde",       files.vorbef);
  if (files.verlauf)     fd.append("verlaufsdoku",     files.verlauf);
  if (files.antragsvorlage) fd.append("antragsvorlage", files.antragsvorlage);
  if (files.vorantrag)   fd.append("vorantrag",        files.vorantrag);
  if (files.style)       fd.append("style_file",       files.style);
  if (files.diagnosen)   fd.append("diagnosen",        files.diagnosen);
  if (files.bullets)     fd.append("bullets",          files.bullets);
  if (files.styleText)   fd.append("style_text",       files.styleText);
  if (files.model)       fd.append("model",             files.model);

  // Job starten
  const r = await apiFetch(`${getApiBase()}/jobs/generate`, { method: "POST", body: fd });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || r.statusText);

  const jobId = d.job_id;
  saveActiveJob(jobId, page);
  if (files.onJobId) files.onJobId(jobId);

  try {
    const job = await pollJob(jobId, 1200, files.signal);
    clearActiveJob();
    if (!job) return null;  // abgebrochen
    return {
      text:        job.result_text   || "",
      befundText:  job.befund_text   || "",
      akutText:    job.akut_text     || "",
      jobId,
      hasTranscript: job.has_transcript || false,
      // v19 Phase 1: QC-Bundle vom Backend. Null bei Pre-v19-Jobs oder QC-Fail.
      qualityCheck: job.quality_check || null,
    };
  } catch (e) {
    clearActiveJob();
    throw e;
  }
}

// Sprint B (Multi-Job-Liste P1): non-blocking Variante von generate().
// Sendet nur den POST und liefert die job_id sofort zurueck, OHNE auf
// Abschluss zu warten. Polling/SSE fuer Status uebernimmt die aufrufende
// Komponente (P1 ueber selectedJobId-useEffect). Bewusst nicht ueber
// generate() refaktoriert, weil P2/P3/P4 weiterhin den blockierenden
// Flow nutzen und buildJobFormData()-Extraktion ein eigener Sprint ist.
async function startJob(workflow, prompt, userContent, files = {}) {
  const therapeutId = getConfluenceUser();
  const fd = new FormData();
  fd.append("workflow",   workflow);
  fd.append("workflow_instructions", prompt);
  if (files.befundVorlage) fd.append("befund_vorlage", files.befundVorlage);
  if (therapeutId)       fd.append("therapeut_id",    therapeutId);
  if (files.patientName) fd.append("patientenname",   files.patientName);
  // v19.8: strukturiertes Geschlecht - unabhaengig vom Kuerzel-Gate. Nur
  // "w"/"m" senden; "auto" = Backend-Default (ableiten wie bisher).
  if (files.geschlecht === "w" || files.geschlecht === "m") fd.append("geschlecht", files.geschlecht);

  if (files.audio && files.audio.__p0recording) {
    fd.append("p0_recording_id", String(files.audio.id));
    fd.append("priority", "high");
    if (files.audio.transcript) fd.append("transcript", files.audio.transcript);
  } else if (files.audio) {
    fd.append("transcript", userContent);
    fd.append("audio", files.audio);
  } else if (files.txtFile) {
    fd.append("transcript_file", files.txtFile);
    if (userContent) fd.append("transcript", userContent);
  } else {
    fd.append("transcript", userContent);
  }

  if (files.selbst)         fd.append("selbstauskunft",  files.selbst);
  if (files.vorbef)         fd.append("vorbefunde",      files.vorbef);
  if (files.verlauf)        fd.append("verlaufsdoku",    files.verlauf);
  if (files.antragsvorlage) fd.append("antragsvorlage",  files.antragsvorlage);
  if (files.vorantrag)      fd.append("vorantrag",       files.vorantrag);
  if (files.style)          fd.append("style_file",      files.style);
  if (files.diagnosen)      fd.append("diagnosen",       files.diagnosen);
  if (files.bullets)        fd.append("bullets",         files.bullets);
  if (files.styleText)      fd.append("style_text",      files.styleText);
  if (files.model)          fd.append("model",            files.model);

  const r = await apiFetch(`${getApiBase()}/jobs/generate`, { method: "POST", body: fd });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || r.statusText);
  return d.job_id;
}


// Laedt das Transkript eines Jobs vom Backend und speichert es als .txt
async function downloadTranscript(jobId, filename = "transkript.txt") {
  const r = await apiFetch(`${getApiBase()}/jobs/${jobId}/transcript`);
  if (!r.ok) throw new Error("Transkript nicht verfügbar");
  const data = await r.json();
  const blob = new Blob([data.transcript], { type: "text/plain;charset=utf-8" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// v19 Phase C: Repair-API-Aufrufe (Production-Pfad mit apiFetch).
// Test-Pendants in utils/api.js (mit _fetch-Parameter).

async function repairPreview(jobId, acceptedCodes, userHint) {
  const r = await apiFetch(`${getApiBase()}/jobs/${encodeURIComponent(jobId)}/repair/preview`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({
      accepted_issue_codes: acceptedCodes || [],
      user_hint:            userHint || "",
    }),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = d.detail;
    if (detail && typeof detail === "object") throw new Error(detail.msg || JSON.stringify(detail));
    throw new Error(detail || r.statusText);
  }
  return d;  // { final_prompt, accepted_issues, user_hint_sanitized }
}

// v19.4 C-1: Repair NICHT mehr blockierend pollen. repairStart() triggert nur
// den Job und gibt sofort die repair_job_id zurueck — das Modal kann sich
// schliessen und ein JobProgressBar uebernimmt die Fortschrittsanzeige (analog
// zur normalen Generierung). fetchRepairResult() holt nach Terminal das
// fertige Repair-Job-Objekt.
async function repairStart(jobId, acceptedCodes, userHint, customFinalPrompt = null) {
  const body = {
    accepted_issue_codes: acceptedCodes || [],
    user_hint:            userHint || "",
  };
  if (customFinalPrompt) body.custom_final_prompt = customFinalPrompt;
  const r = await apiFetch(`${getApiBase()}/jobs/${encodeURIComponent(jobId)}/repair`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = d.detail;
    if (detail && typeof detail === "object") throw new Error(detail.msg || JSON.stringify(detail));
    throw new Error(detail || r.statusText);
  }
  return { repairJobId: d.repair_job_id, parentJobId: d.parent_job_id };
}

// Holt das fertige Repair-Job-Objekt (einmaliger GET, kein Polling) und mappt
// es auf den Shape den applyRepair() erwartet.
async function fetchRepairResult(repairJobId, parentJobId = null) {
  const r = await apiFetch(`${getApiBase()}/jobs/${encodeURIComponent(repairJobId)}`);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  if (j.status === "error") throw new Error(j.error_msg || "Überarbeitung fehlgeschlagen");
  if (j.status === "cancelled") return null;
  return {
    text:         j.result_text   || "",
    befundText:   j.befund_text   || "",
    akutText:     j.akut_text     || "",
    jobId:        repairJobId,
    parentJobId:  parentJobId,
    qualityCheck: j.quality_check || null,
  };
}

// ── Confluence-Konfiguration ─────────────────────────────────────
// API_BASE: dynamisch aus localStorage/window lesen damit URL-Änderungen
// sofort ohne Seitenneuladung greifen
function getApiBase() {
  // window.SYSTELIOS_API_BASE hat Vorrang (vom Confluence Macro gesetzt, immer verfügbar)
  const fromWindow = (typeof window !== "undefined" && window.SYSTELIOS_API_BASE) || "";
  // localStorage als Fallback – kann in Confluence-iframes blockiert sein
  let stored = "";
  try { stored = localStorage.getItem("systelios_backend_url") || ""; } catch (_) {}
  const raw = fromWindow || stored;
  if (!raw) return "http://localhost:8000/api";
  return raw.replace(/\/$/, "").replace(/\/api$/, "") + "/api";
}

// Therapeuten-Name aus Confluence AJS (gesetzt im Macro vor dem Bundle)
function getConfluenceUser() {
  if (typeof window !== "undefined" && window.SYSTELIOS_USER) {
    return window.SYSTELIOS_USER;
  }
  return "";
}

export { apiFetch, downloadViaApi, pollJob, generate, startJob, downloadTranscript, repairPreview, repairStart, fetchRepairResult, getApiBase, getConfluenceUser };
