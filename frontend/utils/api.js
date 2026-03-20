/**
 * scriptTelios – reine Logik-Funktionen (kein React, kein DOM außer localStorage/fetch)
 * Ausgelagert damit sie unit-testbar sind.
 */

export const JOB_STORAGE_KEY = "systelios_active_job";

// ── URL-Helfer ────────────────────────────────────────────────────────────────

export function getApiBase() {
  const stored = (typeof localStorage !== "undefined" && localStorage.getItem("systelios_backend_url")) || "";
  const fromWindow = (typeof window !== "undefined" && window.SYSTELIOS_API_BASE) || "";
  const raw = stored || fromWindow;
  if (!raw) return "http://localhost:8000/api";
  return raw.replace(/\/$/, "").replace(/\/api$/, "") + "/api";
}

export function getConfluenceUser() {
  if (typeof window !== "undefined" && window.SYSTELIOS_USER) {
    return window.SYSTELIOS_USER;
  }
  return "";
}

// ── Job-Persistenz (localStorage) ────────────────────────────────────────────

export function saveActiveJob(jobId, page) {
  try {
    localStorage.setItem(JOB_STORAGE_KEY, JSON.stringify({ jobId, page, startedAt: Date.now() }));
  } catch (_) {}
}

export function loadActiveJob() {
  try {
    const raw = localStorage.getItem(JOB_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (_) { return null; }
}

export function clearActiveJob() {
  try { localStorage.removeItem(JOB_STORAGE_KEY); } catch (_) {}
}

// ── Polling ───────────────────────────────────────────────────────────────────

export async function pollJob(jobId, maxWaitSeconds = 1200, _fetch = fetch) {
  const interval = 2;
  for (let i = 0; i < maxWaitSeconds / interval; i++) {
    await new Promise(res => setTimeout(res, interval * 1000));
    const poll = await _fetch(`${getApiBase()}/jobs/${jobId}`);
    if (!poll.ok) continue;
    const job = await poll.json();
    if (job.status === "done")  return job;
    if (job.status === "error") throw new Error(job.error_msg || "Job fehlgeschlagen");
  }
  throw new Error("Timeout: Job dauert zu lange");
}

// ── Generierung ───────────────────────────────────────────────────────────────

export async function generate(workflow, prompt, userContent, files = {}, page = null, _fetch = fetch) {
  const therapeutId = getConfluenceUser();
  const fd = new FormData();
  fd.append("workflow",   workflow);
  fd.append("prompt",     prompt);
  fd.append("transcript", userContent);
  if (therapeutId)      fd.append("therapeut_id",  therapeutId);
  if (files.audio)      fd.append("audio",          files.audio);
  if (files.selbst)     fd.append("selbstauskunft", files.selbst);
  if (files.vorbef)     fd.append("vorbefunde",     files.vorbef);
  if (files.style)      fd.append("style_file",     files.style);
  if (files.diagnosen)  fd.append("diagnosen",      files.diagnosen);
  if (files.bullets)    fd.append("bullets",        files.bullets);

  const r = await _fetch(`${getApiBase()}/jobs/generate`, { method: "POST", body: fd });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || r.statusText);

  const jobId = d.job_id;
  saveActiveJob(jobId, page);

  try {
    const job = await pollJob(jobId, 1200, _fetch);
    clearActiveJob();
    return { text: job.result_text || "", jobId, hasTranscript: job.has_transcript || false };
  } catch (e) {
    clearActiveJob();
    throw e;
  }
}

// ── Transkript-Download ───────────────────────────────────────────────────────

export async function downloadTranscript(jobId, filename = "transkript.txt", _fetch = fetch) {
  const r = await _fetch(`${getApiBase()}/jobs/${jobId}/transcript`);
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

// ── Geschlecht-Hinweis ────────────────────────────────────────────────────────

export function buildGeschlechtHinweis(geschlecht, kuerzel = "") {
  const k = kuerzel.trim().replace(/\.?$/, ".");
  const nameHinweis = kuerzel.trim()
    ? ` Verwende als Namenskürzel durchgehend "${k}" (z.B. "Frau ${k}", "Herr ${k}", "Klient ${k}").`
    : "";
  return {
    "w":    `\n\nKLIENT-GESCHLECHT: weiblich – verwende durchgehend weibliche Formen (die Klientin, sie, ihr).${nameHinweis}`,
    "m":    `\n\nKLIENT-GESCHLECHT: männlich – verwende durchgehend männliche Formen (der Klient, er, ihm).${nameHinweis}`,
    "auto": `\n\nKLIENT-GESCHLECHT: Leite das Geschlecht aus dem Transkript ab (Namen, Pronomen, Anreden). Falls nicht erkennbar, verwende neutrale Formen.${nameHinweis}`,
  }[geschlecht] ?? "";
}
