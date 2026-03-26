/**
 * scriptTelios Frontend – Unit-Tests für api.js
 *
 * Testet die reinen Logik-Funktionen ohne React-Overhead.
 * Ausführen: npm test (im frontend/-Verzeichnis)
 */

import { jest } from "@jest/globals";
import {
  getApiBase,
  getConfluenceUser,
  saveActiveJob,
  loadActiveJob,
  clearActiveJob,
  pollJob,
  generate,
  buildGeschlechtHinweis,
  JOB_STORAGE_KEY,
} from "../utils/api.js";

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Baut einen fetch-Mock der eine Sequenz von Responses liefert */
function mockFetchSequence(...responses) {
  let i = 0;
  return jest.fn(() => {
    const res = responses[i] ?? responses.at(-1);
    i++;
    return Promise.resolve(res);
  });
}

function jsonResponse(body, ok = true, status = 200) {
  return { ok, status, json: () => Promise.resolve(body) };
}

// ── getApiBase ────────────────────────────────────────────────────────────────

describe("getApiBase()", () => {
  beforeEach(() => localStorage.clear());

  test("gibt Standard-URL zurück wenn nichts konfiguriert", () => {
    expect(getApiBase()).toBe("http://localhost:8000/api");
  });

  test("liest gespeicherte Backend-URL aus localStorage", () => {
    localStorage.setItem("systelios_backend_url", "https://xyz.trycloudflare.com");
    expect(getApiBase()).toBe("https://xyz.trycloudflare.com/api");
  });

  test("entfernt doppeltes /api am Ende", () => {
    localStorage.setItem("systelios_backend_url", "https://xyz.trycloudflare.com/api");
    expect(getApiBase()).toBe("https://xyz.trycloudflare.com/api");
  });

  test("entfernt trailing slash", () => {
    localStorage.setItem("systelios_backend_url", "https://xyz.trycloudflare.com/");
    expect(getApiBase()).toBe("https://xyz.trycloudflare.com/api");
  });
});

// ── getConfluenceUser ─────────────────────────────────────────────────────────

describe("getConfluenceUser()", () => {
  afterEach(() => { delete window.SYSTELIOS_USER; });

  test("gibt leeren String zurück wenn nicht gesetzt", () => {
    expect(getConfluenceUser()).toBe("");
  });

  test("gibt window.SYSTELIOS_USER zurück wenn gesetzt", () => {
    window.SYSTELIOS_USER = "dr.mueller";
    expect(getConfluenceUser()).toBe("dr.mueller");
  });
});

// ── Job-Persistenz ────────────────────────────────────────────────────────────

describe("Job-Persistenz (localStorage)", () => {
  beforeEach(() => localStorage.clear());

  test("saveActiveJob speichert jobId und page", () => {
    saveActiveJob("job-123", "p1");
    const raw = JSON.parse(localStorage.getItem(JOB_STORAGE_KEY));
    expect(raw.jobId).toBe("job-123");
    expect(raw.page).toBe("p1");
    expect(raw.startedAt).toBeGreaterThan(0);
  });

  test("loadActiveJob gibt null zurück wenn kein Job gespeichert", () => {
    expect(loadActiveJob()).toBeNull();
  });

  test("loadActiveJob gibt gespeicherten Job zurück", () => {
    saveActiveJob("job-456", "p2");
    const job = loadActiveJob();
    expect(job.jobId).toBe("job-456");
    expect(job.page).toBe("p2");
  });

  test("clearActiveJob entfernt gespeicherten Job", () => {
    saveActiveJob("job-789", "p1");
    clearActiveJob();
    expect(loadActiveJob()).toBeNull();
  });

  test("loadActiveJob gibt null zurück bei korruptem JSON", () => {
    localStorage.setItem(JOB_STORAGE_KEY, "kein-json{{{");
    expect(loadActiveJob()).toBeNull();
  });
});

// ── buildGeschlechtHinweis ────────────────────────────────────────────────────

describe("buildGeschlechtHinweis()", () => {
  test("weiblich enthält 'Klientin' und weibliche Pronomen", () => {
    const h = buildGeschlechtHinweis("w");
    expect(h).toContain("weiblich");
    expect(h).toContain("Klientin");
    expect(h).toContain("sie");
  });

  test("männlich enthält 'Klient' und männliche Pronomen", () => {
    const h = buildGeschlechtHinweis("m");
    expect(h).toContain("männlich");
    expect(h).toContain("Klient");
    expect(h).toContain("er");
  });

  test("auto enthält Anweisung zur Ableitung aus Transkript", () => {
    const h = buildGeschlechtHinweis("auto");
    expect(h).toContain("Leite das Geschlecht");
    expect(h).toContain("Transkript");
  });

  test("unbekannter Wert gibt leeren String zurück", () => {
    expect(buildGeschlechtHinweis("unbekannt")).toBe("");
  });

  test("alle drei Optionen beginnen mit Newlines (kein unbeabsichtigtes Zusammenkleben)", () => {
    for (const g of ["w", "m", "auto"]) {
      expect(buildGeschlechtHinweis(g)).toMatch(/^\n\n/);
    }
  });

  test("kuerzel wird als Namenskürzel in den Hinweis eingebaut", () => {
    const h = buildGeschlechtHinweis("w", "K");
    expect(h).toContain("K.");
    expect(h).toContain("Frau K.");
    expect(h).toContain("Namenskürzel");
  });

  test("kuerzel mit Punkt am Ende wird nicht doppelt punktiert", () => {
    const h = buildGeschlechtHinweis("m", "M.");
    expect(h).toContain("Herr M.");
    expect(h).not.toContain("M..");
  });

  test("leeres kuerzel erzeugt keinen Namenshinweis", () => {
    const h = buildGeschlechtHinweis("w", "");
    expect(h).not.toContain("Namenskürzel");
    expect(h).not.toContain("Frau");
  });

  test("kuerzel funktioniert auch mit auto-Modus", () => {
    const h = buildGeschlechtHinweis("auto", "S");
    expect(h).toContain("S.");
    expect(h).toContain("Klient S.");
  });
});

// ── pollJob ───────────────────────────────────────────────────────────────────

describe("pollJob()", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    localStorage.clear();
  });
  afterEach(() => jest.useRealTimers());

  test("gibt Job-Objekt zurück wenn status=done", async () => {
    const mockFetch = jest.fn().mockResolvedValue(
      jsonResponse({ status: "done", result_text: "Notiz fertig.", has_transcript: true })
    );
    const p = pollJob("job-1", 10, mockFetch);
    await jest.runAllTimersAsync();
    const job = await p;
    expect(job.result_text).toBe("Notiz fertig.");
    expect(job.has_transcript).toBe(true);
  });

  test("gibt null zurück bei status=cancelled", async () => {
    const mockFetch = jest.fn().mockResolvedValue(
      jsonResponse({ status: "cancelled" })
    );
    const p = pollJob("job-cancelled", 10, mockFetch);
    await jest.runAllTimersAsync();
    const result = await p;
    expect(result).toBeNull();
  });

  test("wirft Fehler bei status=error", async () => {
    const mockFetch = jest.fn().mockResolvedValue(
      jsonResponse({ status: "error", error_msg: "VRAM erschöpft" })
    );
    const p = pollJob("job-2", 10, mockFetch);
    await jest.runAllTimersAsync();
    await expect(p).rejects.toThrow("VRAM erschöpft");
  });

  test("pollt weiter solange status=running", async () => {
    const mockFetch = mockFetchSequence(
      jsonResponse({ status: "running" }),
      jsonResponse({ status: "running" }),
      jsonResponse({ status: "done", result_text: "Fertig nach 3 Polls." })
    );
    const p = pollJob("job-3", 20, mockFetch);
    await jest.runAllTimersAsync();
    const job = await p;
    expect(mockFetch).toHaveBeenCalledTimes(3);
    expect(job.result_text).toBe("Fertig nach 3 Polls.");
  });

  test("wirft Timeout-Fehler wenn maxWaitSeconds überschritten", async () => {
    const mockFetch = jest.fn().mockResolvedValue(
      jsonResponse({ status: "running" })
    );
    const p = pollJob("job-4", 4, mockFetch); // 4s max, interval=2s → 2 Versuche
    await jest.runAllTimersAsync();
    await expect(p).rejects.toThrow("Timeout");
  });

  test("ignoriert fehlerhafte Poll-Responses (nicht-ok) und versucht weiter", async () => {
    const mockFetch = mockFetchSequence(
      { ok: false, status: 503, json: () => Promise.resolve({}) },
      jsonResponse({ status: "done", result_text: "Trotz Fehler fertig." })
    );
    const p = pollJob("job-5", 10, mockFetch);
    await jest.runAllTimersAsync();
    const job = await p;
    expect(job.result_text).toBe("Trotz Fehler fertig.");
  });
});

// ── generate() ───────────────────────────────────────────────────────────────

describe("generate()", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    localStorage.clear();
  });
  afterEach(() => jest.useRealTimers());

  test("schickt workflow, prompt und transcript als FormData-Felder", async () => {
    const mockFetch = mockFetchSequence(
      jsonResponse({ job_id: "job-gen-1" }),           // POST /jobs/generate
      jsonResponse({ status: "done", result_text: "OK", has_transcript: false })
    );
    const p = generate("dokumentation", "Erstelle Notiz.", "Transkript-Inhalt", {}, null, mockFetch);
    await jest.runAllTimersAsync();
    await p;

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain("/jobs/generate");
    expect(opts.method).toBe("POST");
    const fd = opts.body;
    expect(fd.get("workflow")).toBe("dokumentation");
    expect(fd.get("prompt")).toBe("Erstelle Notiz.");
    expect(fd.get("transcript")).toBe("Transkript-Inhalt");
  });

  test("schickt bullets als separates Feld – nicht in transcript eingebaut", async () => {
    const mockFetch = mockFetchSequence(
      jsonResponse({ job_id: "job-gen-2" }),
      jsonResponse({ status: "done", result_text: "OK", has_transcript: false })
    );
    const p = generate(
      "dokumentation", "Prompt", "Transkript-Text",
      { bullets: "- IFS\n- innerer Löwe" }, null, mockFetch
    );
    await jest.runAllTimersAsync();
    await p;

    const fd = mockFetch.mock.calls[0][1].body;
    // Bullets als eigenes Feld vorhanden
    expect(fd.get("bullets")).toBe("- IFS\n- innerer Löwe");
    // Transkript enthält NICHT die Stichpunkte (der alte Bug)
    expect(fd.get("transcript")).not.toContain("STICHPUNKTE");
    expect(fd.get("transcript")).not.toContain("innerer Löwe");
  });

  test("gibt { text, jobId, hasTranscript } zurück", async () => {
    const mockFetch = mockFetchSequence(
      jsonResponse({ job_id: "job-gen-3" }),
      jsonResponse({ status: "done", result_text: "Notiztext", has_transcript: true })
    );
    const p = generate("dokumentation", "p", "t", {}, null, mockFetch);
    await jest.runAllTimersAsync();
    const result = await p;
    expect(result.text).toBe("Notiztext");
    expect(result.jobId).toBe("job-gen-3");
    expect(result.hasTranscript).toBe(true);
  });

  test("speichert Job-ID in localStorage während Polling läuft", async () => {
    let resolveJob;
    const jobDone = new Promise(res => { resolveJob = res; });

    const mockFetch = jest.fn()
      .mockResolvedValueOnce(jsonResponse({ job_id: "job-persist-1" }))
      .mockImplementationOnce(async () => {
        await jobDone;
        return jsonResponse({ status: "done", result_text: "Fertig." });
      });

    const p = generate("dokumentation", "p", "t", {}, "p1", mockFetch);

    // Kurz nach dem Start: Job muss in localStorage sein
    await Promise.resolve();
    await Promise.resolve();
    const saved = loadActiveJob();
    expect(saved?.jobId).toBe("job-persist-1");
    expect(saved?.page).toBe("p1");

    resolveJob();
    await jest.runAllTimersAsync();
    await p;
  });

  test("löscht Job-ID aus localStorage nach erfolgreichem Abschluss", async () => {
    const mockFetch = mockFetchSequence(
      jsonResponse({ job_id: "job-clear-1" }),
      jsonResponse({ status: "done", result_text: "Fertig." })
    );
    const p = generate("dokumentation", "p", "t", {}, "p1", mockFetch);
    await jest.runAllTimersAsync();
    await p;
    expect(loadActiveJob()).toBeNull();
  });

  test("löscht Job-ID auch bei Fehler (kein verwaister localStorage-Eintrag)", async () => {
    const mockFetch = mockFetchSequence(
      jsonResponse({ job_id: "job-err-1" }),
      jsonResponse({ status: "error", error_msg: "LLM-Fehler" })
    );
    const p = generate("dokumentation", "p", "t", {}, "p1", mockFetch);
    await jest.runAllTimersAsync();
    await expect(p).rejects.toThrow("LLM-Fehler");
    expect(loadActiveJob()).toBeNull();
  });

  test("wirft Fehler wenn Backend nicht erreichbar (non-ok POST)", async () => {
    const mockFetch = jest.fn().mockResolvedValue(
      jsonResponse({ detail: "Service Unavailable" }, false, 503)
    );
    const p = generate("dokumentation", "p", "t", {}, null, mockFetch);
    await jest.runAllTimersAsync();
    await expect(p).rejects.toThrow("Service Unavailable");
  });
});
