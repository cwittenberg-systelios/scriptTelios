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
  buildJobFormData,
  startJob,
  repairPreview,
  repairStart,
  fetchRepairResult,
} from "../src/api.js";

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

// ── buildJobFormData / startJob ──────────────────────────────────────────────
// v19.21 (S3): generate() entfernt (kein Aufrufer mehr). Die Feld-Zuordnung
// lebt jetzt in buildJobFormData() und wird hier direkt getestet; startJob()
// deckt POST + Fehlerpfad ab. localStorage-Persistenz des aktiven Jobs
// uebernehmen die Panels (saveActiveJob in P1-P6), nicht mehr die API-Schicht.

describe("buildJobFormData()", () => {
  test("schickt workflow, workflow_instructions und transcript als FormData-Felder", () => {
    const fd = buildJobFormData("dokumentation", "PROMPT", "Transkripttext");
    expect(fd.get("workflow")).toBe("dokumentation");
    expect(fd.get("workflow_instructions")).toBe("PROMPT");
    expect(fd.get("transcript")).toBe("Transkripttext");
  });

  test("schickt bullets als separates Feld – nicht in transcript eingebaut", () => {
    const fd = buildJobFormData("dokumentation", "P", "Haupttext", { bullets: "• Punkt 1" });
    expect(fd.get("bullets")).toBe("• Punkt 1");
    expect(fd.get("transcript")).toBe("Haupttext");
  });

  test("geschlecht wird nur bei w/m gesendet — leer bleibt draussen (v19.12)", () => {
    expect(buildJobFormData("dokumentation", "P", "T", { geschlecht: "w" }).get("geschlecht")).toBe("w");
    expect(buildJobFormData("dokumentation", "P", "T", { geschlecht: "m" }).get("geschlecht")).toBe("m");
    expect(buildJobFormData("dokumentation", "P", "T", { geschlecht: "" }).get("geschlecht")).toBeNull();
    expect(buildJobFormData("dokumentation", "P", "T", { geschlecht: "auto" }).get("geschlecht")).toBeNull();
  });

  test("P0-Recording: p0_recording_id + priority, transcript nur wenn vorhanden", () => {
    let fd = buildJobFormData("dokumentation", "P", "ignoriert", {
      audio: { __p0recording: true, id: 42, transcript: "Aus P0" },
    });
    expect(fd.get("p0_recording_id")).toBe("42");
    expect(fd.get("priority")).toBe("high");
    expect(fd.get("transcript")).toBe("Aus P0");
    expect(fd.get("audio")).toBeNull();

    fd = buildJobFormData("dokumentation", "P", "ignoriert", { audio: { __p0recording: true, id: 7 } });
    expect(fd.get("transcript")).toBeNull();
  });

  test("Audio-Upload geht ins audio-Feld, Transkript-Datei ins transcript_file-Feld", () => {
    const audio = new File(["x"], "a.mp3", { type: "audio/mpeg" });
    let fd = buildJobFormData("dokumentation", "P", "Text", { audio });
    expect(fd.get("audio")).toBe(audio);
    expect(fd.get("transcript")).toBe("Text");

    const txt = new File(["t"], "t.txt", { type: "text/plain" });
    fd = buildJobFormData("anamnese", "P", "", { txtFile: txt });
    expect(fd.get("transcript_file")).toBe(txt);
    expect(fd.get("transcript")).toBeNull();
    fd = buildJobFormData("anamnese", "P", "Zusatz", { txtFile: txt });
    expect(fd.get("transcript")).toBe("Zusatz");
  });

  test("Dokument-Felder werden 1:1 gemappt (inkl. prozessreflexion, ism_n_items)", () => {
    const f = (n) => new File(["d"], n);
    const files = {
      selbst: f("s.pdf"), vorbef: f("v.pdf"), verlauf: f("vd.pdf"),
      antragsvorlage: f("a.docx"), vorantrag: f("va.docx"),
      prozessreflexion: f("pr.pdf"), style: f("st.docx"),
      befundVorlage: "BEFUND", patientName: "Frau M.", diagnosen: "F32.1",
      styleText: "Stil", model: "gemma4:31b", ismNItems: 8,
    };
    const fd = buildJobFormData("entlassbericht", "P", "", files);
    expect(fd.get("selbstauskunft")).toBe(files.selbst);
    expect(fd.get("vorbefunde")).toBe(files.vorbef);
    expect(fd.get("verlaufsdoku")).toBe(files.verlauf);
    expect(fd.get("antragsvorlage")).toBe(files.antragsvorlage);
    expect(fd.get("vorantrag")).toBe(files.vorantrag);
    expect(fd.get("prozessreflexion")).toBe(files.prozessreflexion);
    expect(fd.get("style_file")).toBe(files.style);
    expect(fd.get("befund_vorlage")).toBe("BEFUND");
    expect(fd.get("patientenname")).toBe("Frau M.");
    expect(fd.get("diagnosen")).toBe("F32.1");
    expect(fd.get("style_text")).toBe("Stil");
    expect(fd.get("model")).toBe("gemma4:31b");
    expect(fd.get("ism_n_items")).toBe("8");
  });

  test("therapeut_id kommt aus window.SYSTELIOS_USER", () => {
    window.SYSTELIOS_USER = "dr.test";
    try {
      expect(buildJobFormData("dokumentation", "P", "T").get("therapeut_id")).toBe("dr.test");
    } finally {
      delete window.SYSTELIOS_USER;
    }
    expect(buildJobFormData("dokumentation", "P", "T").get("therapeut_id")).toBeNull();
  });
});

describe("startJob()", () => {
  test("POSTet FormData an /jobs/generate und liefert job_id", async () => {
    const fetchMock = mockSignedFetch(jsonResponse({ job_id: "gen-1" }));
    const jobId = await startJob("dokumentation", "PROMPT", "Text", { bullets: "b" });
    expect(jobId).toBe("gen-1");
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/jobs\/generate$/);
    expect(opts.method).toBe("POST");
    expect(opts.body.get("bullets")).toBe("b");
  });

  test("wirft Fehler mit Backend-detail wenn POST non-ok", async () => {
    mockSignedFetch(jsonResponse({ detail: "Service Unavailable" }, false, 503));
    await expect(startJob("dokumentation", "P", "T")).rejects.toThrow("Service Unavailable");
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
