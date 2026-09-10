// ────────────────────────────────────────────────────────────────────────────
// src/shared.jsx — extrahiert aus klinische-dokumentation.jsx (R4, 2026-07-01).
// Chunk-Inhalte byte-identisch verschoben; nur Import/Export-Header sind neu.
// ────────────────────────────────────────────────────────────────────────────
import { useState, useRef, useCallback, useEffect, useMemo } from "react";


// ── Helpers ──────────────────────────────────────────────────────
// Maximale Upload-Größe (Cloudflare Free Plan: 100MB, mit Puffer)
const MAX_UPLOAD_MB = 90;

// Format-Helper
function fmtSec(s) {
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

function fmtMB(bytes) {
  return (bytes / (1024 * 1024)).toFixed(1);
}

// ── Browser-IndexedDB ─────────────────────────────────────────────────────
// Eine DB ("scriptTelios") mit einem Object-Store:
//   offline_queue: P0-Aufnahmen die wegen Server-Ausfall lokal warten.
//                  Wird nach erfolgreichem Reload-Upload sofort wieder geleert.
// Frueher gab es einen zweiten Store "audio_draft" als Reload-Persistenz fuer
// einzelne Aufnahmen - der war nie vollstaendig (Save-Pfad ohne Load-Pfad)
// und wurde mit Sprint-B-Refactoring entfernt. Bestehende Browser haben
// den Store evtl. noch in v2 - das stoert nicht, er bleibt einfach ungenutzt.
const IDB_NAME          = "scriptTelios";
const OFFLINE_IDB_STORE = "offline_queue";

function idbOpen() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, 2);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(OFFLINE_IDB_STORE)) {
        db.createObjectStore(OFFLINE_IDB_STORE, { keyPath: "id" });
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror   = (e) => reject(e.target.error);
  });
}

async function offlineQueueAdd(file, label) {
  try {
    const db = await idbOpen();
    const id = (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36));
    const tx = db.transaction(OFFLINE_IDB_STORE, "readwrite");
    tx.objectStore(OFFLINE_IDB_STORE).put({ id, blob: file, name: file.name, type: file.type, label: label || "", savedAt: Date.now() });
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = rej; });
    db.close();
    return id;
  } catch (e) { console.warn("[offline-queue] Speichern fehlgeschlagen:", e); return null; }
}

async function offlineQueueList() {
  try {
    const db = await idbOpen();
    const tx = db.transaction(OFFLINE_IDB_STORE, "readonly");
    const items = await new Promise((res, rej) => {
      const req = tx.objectStore(OFFLINE_IDB_STORE).getAll();
      req.onsuccess = (e) => res(e.target.result);
      req.onerror   = (e) => rej(e.target.error);
    });
    db.close();
    return items.sort((a, b) => a.savedAt - b.savedAt);
  } catch (e) { console.warn("[offline-queue] Laden fehlgeschlagen:", e); return []; }
}

async function offlineQueueRemove(id) {
  try {
    const db = await idbOpen();
    const tx = db.transaction(OFFLINE_IDB_STORE, "readwrite");
    tx.objectStore(OFFLINE_IDB_STORE).delete(id);
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = rej; });
    db.close();
  } catch (e) { console.warn("[offline-queue] Löschen fehlgeschlagen:", e); }
}

/**
 * AudioRecorder - Browser-seitige Sprachaufnahme mit Opus 24kbps.
 * Kompakt (45 Min ~ 8 MB), unter dem 90 MB Upload-Limit selbst bei 5h-Sitzungen.
 */

// ── QualityCheckPanel (v19 Phase 1) ──────────────────────────────────
// Read-only-Anzeige der QC-Issues. Wird direkt nach <Output> gerendert.
// Sprint 4 (Phase C): wird zur interaktiven Auswahl-Komponente erweitert,
// die Issue-Selection + User-Hint + Repair-Trigger umfasst.
//
// Erwartetes `data`-Shape (entspricht backend serialize_issues):
//   { version, workflow, summary: {critical, warning, info, total},
//     issues: [{code, severity, message, repair_hint, code_detail}, ...] }
// `null` oder leere `issues` -> die Komponente rendert NICHTS.
// ── QualityCheck-Helper (v19 Phase 1 + C) ─────────────────────────────
// pickQualityCheck(obj):
//   Liest das QC-Feld einheitlich heraus - egal ob obj aus pollJob() kommt
//   (Backend-snake_case: quality_check) oder aus generate()/repair()
//   (camelCase: qualityCheck). Liefert null bei Falsy-Eingaben.
//
// useJobResult():
//   Hook fuer Pages. Konsolidiert ALLES was zum Generierungs-Output gehoert:
//   - Original-Text + QC-Bundle (gesetzt von generate() oder resume)
//   - Repair-Text + Repair-QC-Bundle (gesetzt nach repair-Submit)
//   - activeVersion ("original" | "repair") - welcher Tab ist sichtbar
//   - Sub-State fuer das QualityCheckPanel: Auswahl-Checkboxen + Hint
//   - Sub-State fuer das RepairPreviewModal
//
//   Geliefert wird ein 2-Tupel [snapshot, ops]:
//     snapshot:
//       text                aktuell aktiver Text (Original oder Repair)
//       befundText          aktiver Befund-Text (nur Anamnese, sonst "")
//       qualityCheck        aktives QC-Bundle (Original-QC oder Repair-QC)
//       hasRepair           true wenn schon eine Repair-Version vorliegt
//       activeVersion       "original" | "repair"
//       acceptedCodes       Set-aehnliches Array (lokaler Auswahl-State)
//       userHint            Textarea-Wert
//       showRepairModal     bool
//       modalPrompt         der vom Backend gelieferte final_prompt
//       repairBusy          bool, blockiert Doppel-Klicks
//       repairError         string|null
//     ops:
//       applyOriginal(jobOrResult)  - generate()/resume -> Original-Version
//       applyRepair(repairResult)   - nach erfolgreichem repair() -> Repair-Version
//       reset()                     - alle Versionen + UI-State weg
//       toggleCode(code)            - Issue-Code an/abwaehlen
//       setHint(s)                  - User-Hint Textarea-Update
//       setActiveVersion(v)         - Tab-Wechsel original<->repair
//       openModal(prompt)           - RepairPreviewModal anzeigen
//       closeModal()                - Modal weg, Auswahl behalten
//       setRepairBusy(b)            - Loading-State
//       setRepairError(e)           - Fehler-Anzeige

function pickQualityCheck(obj) {
  if (!obj) return null;
  return obj.quality_check ?? obj.qualityCheck ?? null;
}

// ── Job-Persistenz ───────────────────────────────────────────────
// Speichert laufende Jobs in localStorage damit Seitenreloads den Job
// nicht verlieren. Wird beim App-Start automatisch wiederaufgenommen.

const JOB_STORAGE_KEY = "systelios_active_job";

function saveActiveJob(jobId, page) {
  try {
    localStorage.setItem(JOB_STORAGE_KEY, JSON.stringify({ jobId, page, startedAt: Date.now() }));
  } catch (_) {}
}

function loadActiveJob() {
  try {
    const raw = localStorage.getItem(JOB_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (_) { return null; }
}

function clearActiveJob() {
  try { localStorage.removeItem(JOB_STORAGE_KEY); } catch (_) {}
}

// Wandelt technische Fehlermeldungen in verständliche Texte um.
function friendlyError(e) {
  const msg = e?.message || String(e);
  if (msg === "Failed to fetch" || msg.includes("NetworkError") || msg.includes("fetch"))
    return "Server nicht erreichbar. Bitte warte einen Moment und versuche es erneut.";
  if (msg.includes("502") || msg.includes("Bad Gateway"))
    return "Server antwortet nicht (502). Bitte warte einen Moment und versuche es erneut.";
  if (msg.includes("503") || msg.includes("Service Unavailable"))
    return "Server überlastet (503). Bitte versuche es in Kürze erneut.";
  // v19.14b: Der Uhr-Drift-Fall trägt eine bereits nutzerfertige Erklärung aus
  // dem Backend (auth.py check_auth -> "clock_skew"). Diese Meldung nennt Betrag
  // und Richtung der Abweichung und sagt dem Nutzer, was zu tun ist - deshalb
  // durchreichen statt durch das generische "Zugriff verweigert" zu ersetzen.
  if (msg.includes("Uhr dieses Geräts"))
    return msg;
  if (msg.includes("401") || msg.includes("403"))
    return "Zugriff verweigert. Bitte Seite neu laden; falls es weiterhin auftritt, ist ggf. die Systemuhr dieses Geräts falsch gestellt.";
  if (msg.includes("404"))
    return "Endpunkt nicht gefunden (404).";
  if (msg.includes("timeout") || msg.includes("Timeout"))
    return "Zeitüberschreitung – der Server hat zu lange nicht geantwortet.";
  return msg;
}

// ── Pages ────────────────────────────────────────────────────────

// ── P0: Aufnahmen ─────────────────────────────────────────────────
function getEmptyWarning(text) {
  if (!text || text.trim().length < 20) {
    return "⚠️ Kein Ergebnis generiert – die Eingabe war möglicherweise zu kurz oder die Generierung wurde abgebrochen. Bitte Eingabe prüfen und erneut versuchen.";
  }
  return null;
}

// Inline-Label-Editor: Klick auf Text → editierbar, Enter/Blur → speichern
// Modul-level Cache: überlebt P0-Unmount beim Tab-Wechsel.
// Wird beim Mount als Initialwert genutzt → Liste sofort sichtbar, kein Flackern.
const _recordingsCache = { data: [] };
const _pendingLabels   = {};  // { [id]: label } — überlebt P0-Unmount

export { MAX_UPLOAD_MB, fmtSec, fmtMB, offlineQueueAdd, offlineQueueList, offlineQueueRemove, pickQualityCheck, JOB_STORAGE_KEY, saveActiveJob, loadActiveJob, clearActiveJob, friendlyError, getEmptyWarning, _recordingsCache, _pendingLabels };
