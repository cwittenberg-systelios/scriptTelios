/**
 * scriptTelios Frontend – Unit-Tests für src/shared.js (v19.12)
 *
 * Job-Persistenz (localStorage) gegen das LIVE-Modul. Bis v19.11 liefen diese
 * Tests gegen das Test-Duplikat utils/api.js, das von der App nie importiert
 * wurde — die Job-Storage-Helfer leben tatsächlich in src/shared.js.
 *
 * Ausführen: npm test (im frontend/-Verzeichnis)
 */

import {
  JOB_STORAGE_KEY,
  saveActiveJob,
  loadActiveJob,
  clearActiveJob,
  friendlyError,
  buildPatientName,
} from "../src/shared.js";

describe("Job-Persistenz (localStorage)", () => {
  beforeEach(() => localStorage.clear());

  test("saveActiveJob speichert jobId und page", () => {
    saveActiveJob("job-123", "p3");
    const raw = JSON.parse(localStorage.getItem(JOB_STORAGE_KEY));
    expect(raw.jobId).toBe("job-123");
    expect(raw.page).toBe("p3");
    expect(typeof raw.startedAt).toBe("number");
  });

  test("loadActiveJob gibt null zurück wenn kein Job gespeichert", () => {
    expect(loadActiveJob()).toBeNull();
  });

  test("loadActiveJob gibt gespeicherten Job zurück", () => {
    saveActiveJob("job-456", "p1");
    const job = loadActiveJob();
    expect(job.jobId).toBe("job-456");
    expect(job.page).toBe("p1");
  });

  test("clearActiveJob entfernt gespeicherten Job", () => {
    saveActiveJob("job-789", "p2");
    clearActiveJob();
    expect(loadActiveJob()).toBeNull();
    expect(localStorage.getItem(JOB_STORAGE_KEY)).toBeNull();
  });

  test("loadActiveJob gibt null zurück bei korruptem JSON", () => {
    localStorage.setItem(JOB_STORAGE_KEY, "{kaputt");
    expect(loadActiveJob()).toBeNull();
  });
});

describe("friendlyError()", () => {
  test("Netzwerkfehler wird verständlich übersetzt", () => {
    const msg = friendlyError(new Error("Failed to fetch"));
    expect(msg).toMatch(/Server nicht erreichbar/);
  });

  test("unbekannte Fehler behalten ihre Message", () => {
    expect(friendlyError(new Error("VRAM erschöpft"))).toMatch(/VRAM erschöpft/);
  });
});


// v19.21 (S5): Kuerzel/Anrede-Ableitung, vorher in P1/P2/P6 dreimal inline.
describe("buildPatientName()", () => {
  test("leer -> null", () => {
    expect(buildPatientName("", "w")).toBeNull();
    expect(buildPatientName("   ", "m")).toBeNull();
    expect(buildPatientName(undefined, "")).toBeNull();
  });
  test("haengt Punkt an und setzt Anrede nach Geschlecht", () => {
    expect(buildPatientName("K", "w")).toBe("Frau K.");
    expect(buildPatientName("K.", "m")).toBe("Herr K.");
    expect(buildPatientName(" Mü ", "")).toBe("Mü.");
    expect(buildPatientName("K", "auto")).toBe("K.");   // kein Geschlecht -> nur Kuerzel
  });
});
