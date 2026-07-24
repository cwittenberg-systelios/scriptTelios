/**
 * scriptTelios Frontend – Unit-Tests für src/api.js (v19.12)
 *
 * Läuft gegen das LIVE-Modul (bis v19.11: Test-Duplikat utils/api.js, das
 * von der App nie importiert wurde und architektonisch abgedriftet war —
 * u.a. testete es ein blockierendes repair(), das v19.4 C-1 durch
 * repairStart() + fetchRepairResult() ersetzt hat).
 *
 * Mock-Strategie: apiFetch() delegiert an window.signedFetch (Confluence-
 * HMAC) mit Fallback auf fetch. Die Tests mocken window.signedFetch —
 * die Produktionssignaturen brauchen keinen _fetch-Injektionsparameter.
 *
 * Ausführen: npm test (im frontend/-Verzeichnis)
 */

import { jest } from "@jest/globals";
import {
  getApiBase,
  getConfluenceUser,
  pollJob,
  generate,
  repairPreview,
  repairStart,
  fetchRepairResult,
} from "../src/api.js";
import { JOB_STORAGE_KEY } from "../src/shared.js";

// ── Helpers ──────────────────────────────────────────────────────────────────

function jsonResponse(body, ok = true, status = 200) {
  return { ok, status, statusText: ok ? "OK" : "Error", json: () => Promise.resolve(body) };
}

/** window.signedFetch-Mock mit Response-Sequenz (letzte wiederholt sich). */
function mockSignedFetch(...responses) {
  let i = 0;
  const fn = jest.fn(() => Promise.resolve(responses[Math.min(i++, responses.length - 1)]));
  window.signedFetch = fn;
  return fn;
}

afterEach(() => {
  delete window.signedFetch;
  delete window.SYSTELIOS_API_BASE;
  delete window.SYSTELIOS_USER;
  localStorage.clear();
  jest.useRealTimers();
});

// ── getApiBase ───────────────────────────────────────────────────────────────

describe("getApiBase()", () => {
  test("gibt Standard-URL zurück wenn nichts konfiguriert", () => {
    expect(getApiBase()).toBe("http://localhost:8000/api");
  });

  test("window.SYSTELIOS_API_BASE hat Vorrang vor localStorage", () => {
    window.SYSTELIOS_API_BASE = "https://macro.example.com";
    localStorage.setItem("systelios_backend_url", "https://stored.example.com");
    expect(getApiBase()).toBe("https://macro.example.com/api");
  });

  test("liest gespeicherte Backend-URL aus localStorage", () => {
    localStorage.setItem("systelios_backend_url", "https://pod.example.com");
    expect(getApiBase()).toBe("https://pod.example.com/api");
  });

  test("entfernt doppeltes /api am Ende", () => {
    localStorage.setItem("systelios_backend_url", "https://pod.example.com/api");
    expect(getApiBase()).toBe("https://pod.example.com/api");
  });

  test("entfernt trailing slash", () => {
    localStorage.setItem("systelios_backend_url", "https://pod.example.com/");
    expect(getApiBase()).toBe("https://pod.example.com/api");
  });
});

// ── getConfluenceUser ────────────────────────────────────────────────────────

describe("getConfluenceUser()", () => {
  test("gibt leeren String zurück wenn nicht gesetzt", () => {
    expect(getConfluenceUser()).toBe("");
  });

  test("gibt window.SYSTELIOS_USER zurück wenn gesetzt", () => {
    window.SYSTELIOS_USER = "c.wittenberg";
    expect(getConfluenceUser()).toBe("c.wittenberg");
  });
});

// ── pollJob ──────────────────────────────────────────────────────────────────

describe("pollJob()", () => {
  beforeEach(() => jest.useFakeTimers());

  test("gibt Job-Objekt zurück wenn status=done", async () => {
    mockSignedFetch(jsonResponse({ status: "done", result_text: "Notiz fertig.", has_transcript: true }));
    const p = pollJob("job-1", 10);
    await jest.runAllTimersAsync();
    const job = await p;
    expect(job.result_text).toBe("Notiz fertig.");
    expect(job.has_transcript).toBe(true);
  });

  test("gibt null zurück bei status=cancelled", async () => {
    mockSignedFetch(jsonResponse({ status: "cancelled" }));
    const p = pollJob("job-cancelled", 10);
    await jest.runAllTimersAsync();
    expect(await p).toBeNull();
  });

  test("wirft Fehler bei status=error", async () => {
    mockSignedFetch(jsonResponse({ status: "error", error_msg: "VRAM erschöpft" }));
    const p = pollJob("job-err", 10);
    // rejects-Expectation VOR dem Timer-Lauf anhängen — sonst meldet Jest
    // unter --experimental-vm-modules eine unhandled rejection.
    const expectation = expect(p).rejects.toThrow("VRAM erschöpft");
    await jest.runAllTimersAsync();
    await expectation;
  });

  test("pollt weiter solange status=running", async () => {
    const fetchMock = mockSignedFetch(
      jsonResponse({ status: "running" }),
      jsonResponse({ status: "running" }),
      jsonResponse({ status: "done", result_text: "fertig" }),
    );
    const p = pollJob("job-slow", 60);
    await jest.runAllTimersAsync();
    const job = await p;
    expect(job.result_text).toBe("fertig");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  test("wirft Timeout-Fehler wenn maxWaitSeconds überschritten", async () => {
    mockSignedFetch(jsonResponse({ status: "running" }));
    const p = pollJob("job-hang", 3);  // 1 Iteration bei interval=3
    const expectation = expect(p).rejects.toThrow("Timeout");
    await jest.runAllTimersAsync();
    await expectation;
  });

  test("ignoriert fehlerhafte Poll-Responses (nicht-ok) und versucht weiter", async () => {
    mockSignedFetch(
      jsonResponse({}, false, 502),
      jsonResponse({ status: "done", result_text: "doch noch" }),
    );
    const p = pollJob("job-flaky", 60);
    await jest.runAllTimersAsync();
    expect((await p).result_text).toBe("doch noch");
  });

  test("AbortSignal beendet Polling mit null", async () => {
    mockSignedFetch(jsonResponse({ status: "running" }));
    const ctl = new AbortController();
    const p = pollJob("job-abort", 60, ctl.signal);
    ctl.abort();
    await jest.runAllTimersAsync();
    expect(await p).toBeNull();
  });
});

// ── generate ─────────────────────────────────────────────────────────────────

describe("generate()", () => {
  beforeEach(() => jest.useFakeTimers());

  /** POST + sofortiges done-Polling. */
  function mockGenerateFlow(jobBody = { status: "done", result_text: "Text." }) {
    return mockSignedFetch(
      jsonResponse({ job_id: "gen-1" }),
      jsonResponse(jobBody),
    );
  }

  function sentFormData(fetchMock, callIdx = 0) {
    return fetchMock.mock.calls[callIdx][1].body;
  }

  test("schickt workflow, workflow_instructions und transcript als FormData-Felder", async () => {
    const fetchMock = mockGenerateFlow();
    const p = generate("dokumentation", "PROMPT", "Transkripttext");
    await jest.runAllTimersAsync();
    await p;
    const fd = sentFormData(fetchMock);
    expect(fd.get("workflow")).toBe("dokumentation");
    expect(fd.get("workflow_instructions")).toBe("PROMPT");
    expect(fd.get("transcript")).toBe("Transkripttext");
  });

  test("schickt bullets als separates Feld – nicht in transcript eingebaut", async () => {
    const fetchMock = mockGenerateFlow();
    const p = generate("dokumentation", "P", "Haupttext", { bullets: "• Punkt 1" });
    await jest.runAllTimersAsync();
    await p;
    const fd = sentFormData(fetchMock);
    expect(fd.get("bullets")).toBe("• Punkt 1");
    expect(fd.get("transcript")).toBe("Haupttext");
  });

  test("geschlecht wird nur bei w/m gesendet — leer bleibt draussen (v19.12)", async () => {
    let fetchMock = mockGenerateFlow();
    let p = generate("dokumentation", "P", "T", { geschlecht: "w" });
    await jest.runAllTimersAsync();
    await p;
    expect(sentFormData(fetchMock).get("geschlecht")).toBe("w");

    fetchMock = mockGenerateFlow();
    p = generate("dokumentation", "P", "T", { geschlecht: "" });
    await jest.runAllTimersAsync();
    await p;
    expect(sentFormData(fetchMock).get("geschlecht")).toBeNull();
  });

  test("gibt { text, jobId, hasTranscript, qualityCheck } zurück", async () => {
    mockGenerateFlow({
      status: "done", result_text: "Ergebnis.", has_transcript: true,
      quality_check: { issues: [] },
    });
    const p = generate("dokumentation", "P", "T");
    await jest.runAllTimersAsync();
    const r = await p;
    expect(r.text).toBe("Ergebnis.");
    expect(r.jobId).toBe("gen-1");
    expect(r.hasTranscript).toBe(true);
    expect(r.qualityCheck).toEqual({ issues: [] });
  });

  test("liefert qualityCheck=null wenn Backend kein quality_check hat", async () => {
    mockGenerateFlow({ status: "done", result_text: "x" });
    const p = generate("dokumentation", "P", "T");
    await jest.runAllTimersAsync();
    expect((await p).qualityCheck).toBeNull();
  });

  test("speichert Job-ID in localStorage während Polling läuft", async () => {
    mockSignedFetch(
      jsonResponse({ job_id: "gen-persist" }),
      jsonResponse({ status: "running" }),
      jsonResponse({ status: "done", result_text: "x" }),
    );
    const p = generate("dokumentation", "P", "T", {}, "p1");
    // Nach dem POST, vor Abschluss: Job muss persistiert sein
    await jest.advanceTimersByTimeAsync(3000);
    const saved = JSON.parse(localStorage.getItem(JOB_STORAGE_KEY));
    expect(saved.jobId).toBe("gen-persist");
    expect(saved.page).toBe("p1");
    await jest.runAllTimersAsync();
    await p;
  });

  test("löscht Job-ID aus localStorage nach erfolgreichem Abschluss", async () => {
    mockGenerateFlow();
    const p = generate("dokumentation", "P", "T");
    await jest.runAllTimersAsync();
    await p;
    expect(localStorage.getItem(JOB_STORAGE_KEY)).toBeNull();
  });

  test("löscht Job-ID auch bei Fehler (kein verwaister localStorage-Eintrag)", async () => {
    mockSignedFetch(
      jsonResponse({ job_id: "gen-fail" }),
      jsonResponse({ status: "error", error_msg: "kaputt" }),
    );
    const p = generate("dokumentation", "P", "T");
    const expectation = expect(p).rejects.toThrow("kaputt");
    await jest.runAllTimersAsync();
    await expectation;
    expect(localStorage.getItem(JOB_STORAGE_KEY)).toBeNull();
  });

  test("wirft Fehler wenn Backend nicht erreichbar (non-ok POST)", async () => {
    mockSignedFetch(jsonResponse({ detail: "Service Unavailable" }, false, 503));
    const p = generate("dokumentation", "P", "T");
    const expectation = expect(p).rejects.toThrow("Service Unavailable");
    await jest.runAllTimersAsync();
    await expectation;
  });
});

// ── repairPreview (v19 Phase C) ──────────────────────────────────────────────

describe("repairPreview()", () => {
  test("POSTet codes + hint, gibt final_prompt zurueck", async () => {
    const fetchMock = mockSignedFetch(jsonResponse({ final_prompt: "REPARIERE X" }));
    const d = await repairPreview("job-1", ["NAME_LEAK"], "bitte anonymisieren");
    expect(d.final_prompt).toBe("REPARIERE X");
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/jobs\/job-1\/repair\/preview$/);
    expect(JSON.parse(opts.body)).toEqual({
      accepted_issue_codes: ["NAME_LEAK"],
      user_hint: "bitte anonymisieren",
    });
  });

  test("wirft Fehler bei 404", async () => {
    mockSignedFetch(jsonResponse({ detail: "Job nicht gefunden" }, false, 404));
    await expect(repairPreview("weg", [], "")).rejects.toThrow("Job nicht gefunden");
  });

  test("wirft Fehler bei 422 mit detail-Objekt", async () => {
    mockSignedFetch(jsonResponse(
      { detail: { msg: "unbekannte Codes", unknown_codes: ["XX"] } }, false, 422,
    ));
    await expect(repairPreview("job-1", ["XX"], "")).rejects.toThrow("unbekannte Codes");
  });

  test("URL-encodet die jobId", async () => {
    const fetchMock = mockSignedFetch(jsonResponse({ final_prompt: "p" }));
    await repairPreview("job/with slash", [], "");
    expect(fetchMock.mock.calls[0][0]).toContain("job%2Fwith%20slash");
  });

  test("Defaults: leere codes + leerer hint", async () => {
    const fetchMock = mockSignedFetch(jsonResponse({ final_prompt: "p" }));
    await repairPreview("job-1", null, null);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      accepted_issue_codes: [],
      user_hint: "",
    });
  });
});

// ── repairStart + fetchRepairResult (v19.4 C-1, nicht-blockierend) ───────────

describe("repairStart()", () => {
  test("triggert Repair-Job und liefert IDs sofort (kein Polling)", async () => {
    const fetchMock = mockSignedFetch(
      jsonResponse({ repair_job_id: "rep-1", parent_job_id: "job-1" }),
    );
    const r = await repairStart("job-1", ["NAME_LEAK"], "Hinweis");
    expect(r).toEqual({ repairJobId: "rep-1", parentJobId: "job-1" });
    expect(fetchMock).toHaveBeenCalledTimes(1);  // wirklich non-blocking
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      accepted_issue_codes: ["NAME_LEAK"],
      user_hint: "Hinweis",
    });
  });

  test("sendet custom_final_prompt wenn gesetzt", async () => {
    const fetchMock = mockSignedFetch(jsonResponse({ repair_job_id: "rep-2" }));
    await repairStart("job-1", [], "", "EIGENER PROMPT");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).custom_final_prompt)
      .toBe("EIGENER PROMPT");
  });

  test("OHNE custom_final_prompt wird das Feld weggelassen", async () => {
    const fetchMock = mockSignedFetch(jsonResponse({ repair_job_id: "rep-3" }));
    await repairStart("job-1", [], "");
    expect("custom_final_prompt" in JSON.parse(fetchMock.mock.calls[0][1].body))
      .toBe(false);
  });

  test("wirft Fehler mit detail-Message bei non-ok", async () => {
    mockSignedFetch(jsonResponse({ detail: "Parent-Job läuft noch" }, false, 409));
    await expect(repairStart("job-1", [], "")).rejects.toThrow("Parent-Job läuft noch");
  });
});

describe("fetchRepairResult()", () => {
  test("mappt fertigen Repair-Job auf applyRepair-Shape", async () => {
    mockSignedFetch(jsonResponse({
      status: "done", result_text: "Repariert.", befund_text: "Befund repariert.",
      quality_check: { issues: [] },
    }));
    const r = await fetchRepairResult("rep-1", "job-1");
    expect(r.text).toBe("Repariert.");
    expect(r.befundText).toBe("Befund repariert.");
    expect(r.jobId).toBe("rep-1");
    expect(r.parentJobId).toBe("job-1");
    expect(r.qualityCheck).toEqual({ issues: [] });
  });

  test("liefert null wenn Repair-Job 'cancelled' meldet", async () => {
    mockSignedFetch(jsonResponse({ status: "cancelled" }));
    expect(await fetchRepairResult("rep-1")).toBeNull();
  });

  test("propagiert Job-Error", async () => {
    mockSignedFetch(jsonResponse({ status: "error", error_msg: "Modell abgestürzt" }));
    await expect(fetchRepairResult("rep-1")).rejects.toThrow("Modell abgestürzt");
  });
});
