/**
 * scriptTelios Frontend – Tests P7 SNS-Verlaufsauswertung (v19.41).
 *
 * Start (FormData-Felder), Ergebnisdarstellung (Bericht/Grafiken/Kennwerte),
 * DOCX-Export aus dem editierten Text, Live-QC bei Blur.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

jest.mock("../src/api.js", () => {
  const actual = jest.requireActual("../src/api.js");
  return {
    ...actual,
    apiFetch: jest.fn(() => Promise.resolve({ ok: true, json: async () => ({}) })),
    getApiBase: () => "http://api",
    pollJob: jest.fn(),
    startJob: jest.fn(),
  };
});
jest.mock("../src/hooks.jsx", () => {
  const actual = jest.requireActual("../src/hooks.jsx");
  return { ...actual, useResumeWorkflowJob: jest.fn() };
});
jest.mock("../src/ui.jsx", () => {
  const actual = jest.requireActual("../src/ui.jsx");
  return {
    ...actual,
    JobModelPicker: () => null,
    FeedbackButton: () => null,
    JobProgressBar: () => <div>Läuft</div>,
  };
});

import { apiFetch, buildJobFormData, pollJob, startJob } from "../src/api.js";
import { P7 } from "../src/panels/P7.jsx";

const FAKTEN = {
  zeitraum: { start: "2026-03-02", ende: "2026-04-10", messtage_hsf: 39 },
  ordnungsuebergang: "2026-03-19",
  phasen: [{ id: 1, label: "P1", start: "2026-03-02", ende: "2026-03-18", typ: "ankommen", komposit_mittel: 40.1, dk_mittel: 0.1, name: null }],
  uebergaenge: [{ datum: "2026-03-19", typ: "ordnungsuebergang", shift: 47.0, vorlaeufer: { datum: "2026-03-18", dk: 0.128, kritisch: true } }],
  ism: { quelle: "xml", faktoren: [{ name: "I Zielerleben", besetzt: true, items: ["I · x"], sprung_uebergang: 41.4, krisen_minimum: 12.0, endniveau: 88.0 }] },
  hsf: { faktoren: [{ name: "III", kurz: "Symptome", anfang: 56.3, ende: 19.0, delta: -37.3, tau: -0.53 }] },
  einbrueche: [{ datum: "2026-03-14", tiefe: 13.4, dauer: 1 }],
  polung_check: [{ item: "III · x", r: -0.6, fraglich: true }],
};
const RESULT = {
  version: 1, kuerzel: "Frau K.", anrede: "Klientin",
  text: "1. Zusammenfassung\nAlles gut.\n\n2. Fragebögen und Faktorstruktur\nText.",
  fakten: FAKTEN, phasen: [{ ...FAKTEN.phasen[0], name: "Ankommen" }],
  flags: ["SUIZIDALITAET_IN_QUELLE", "KONSTANTE_ITEMS"], hinweise: [],
  grafiken: { hsf_faktoren: { png_b64: "iVBORw0KGgo=", titel: "HSF", nr: 1 }, krd: { png_b64: "iVBORw0KGgo=", titel: "KRD", nr: 2 } },
};
const JOB = { job_id: "j7", status: "done", result_text: JSON.stringify(RESULT),
              quality_check: { version: 1, workflow: "sns_verlauf", issues: [], summary: { critical: 0, warning: 0, info: 0, total: 0, checks_run: 16 } } };

function file(name, type = "text/csv") { return new File(["sep=;"], name, { type }); }

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

test("buildJobFormData: SNS-Felder werden gemappt", () => {
  const fd = buildJobFormData("sns_verlauf", "P", "", {
    snsHsf: file("hsf.csv"), snsInd: file("ind.csv"), snsXml: file("i.xml", "text/xml"), snsDoc: file("f.doc"),
    snsVorname: "Anna", patientName: "Frau K.", geschlecht: "w",
  });
  expect(fd.get("workflow")).toBe("sns_verlauf");
  expect(fd.get("sns_hsf_csv").name).toBe("hsf.csv");
  expect(fd.get("sns_ind_csv").name).toBe("ind.csv");
  expect(fd.get("sns_ind_xml").name).toBe("i.xml");
  expect(fd.get("sns_faktor_doc").name).toBe("f.doc");
  expect(fd.get("sns_vorname")).toBe("Anna");
  expect(fd.get("patientenname")).toBe("Frau K.");
  expect(fd.get("geschlecht")).toBe("w");
});

test("P7: Start erst mit HSF + Kürzel; startJob bekommt die Dateien", async () => {
  startJob.mockResolvedValue("j7");
  pollJob.mockResolvedValue(JOB);
  const { container } = render(<P7 toast={() => {}} />);
  const runBtn = screen.getByRole("button", { name: /Verlaufsauswertung erstellen/ });
  expect(runBtn.disabled).toBe(true);

  const inputs = container.querySelectorAll("input[type=file]");
  await act(async () => { fireEvent.change(inputs[0], { target: { files: [file("hsf.csv")] } }); });
  expect(runBtn.disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: /weiblich/ }));
  fireEvent.change(screen.getByPlaceholderText("K."), { target: { value: "K" } });
  expect(screen.getByRole("button", { name: /Verlaufsauswertung erstellen/ }).disabled).toBe(false);

  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Verlaufsauswertung erstellen/ })); });
  expect(startJob).toHaveBeenCalledTimes(1);
  const [wf, , , files] = startJob.mock.calls[0];
  expect(wf).toBe("sns_verlauf");
  expect(files.snsHsf.name).toBe("hsf.csv");
  expect(files.patientName).toBe("Frau K.");
  expect(files.snsInd).toBeNull();

  await waitFor(() => expect(screen.getByText(/Verlaufsauswertung – Frau K\./)).toBeTruthy());
  expect(screen.getByText(/Suizidalität\/Selbstverletzung im Tagebuch/)).toBeTruthy();
  expect(container.querySelector("textarea").value).toMatch(/1\. Zusammenfassung/);
  expect(screen.getByText(/alle 16 Checks bestanden/)).toBeTruthy();
});

test("P7: Tabs Grafiken/Kennwerte, DOCX-Export mit editiertem Text, Live-QC bei Blur", async () => {
  startJob.mockResolvedValue("j7");
  pollJob.mockResolvedValue(JOB);
  apiFetch.mockImplementation((url) => {
    if (url.endsWith("/sns/check")) return Promise.resolve({ ok: true, json: async () => ({ ...JOB.quality_check, issues: [{ code: "SNS_ABSCHNITT_FEHLT", severity: "critical", message: "fehlt", code_detail: {} }], summary: { critical: 1, warning: 0, info: 0, total: 1, checks_run: 16 } }) });
    if (url.endsWith("/sns/docx")) return Promise.resolve({ ok: true, headers: { get: () => 'attachment; filename="Verlaufsauswertung_FrauK.docx"' }, blob: async () => new Blob(["x"]) });
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
  global.URL.createObjectURL = jest.fn(() => "blob:x");
  global.URL.revokeObjectURL = jest.fn();
  const toast = jest.fn();
  const { container } = render(<P7 toast={toast} />);
  const inputs = container.querySelectorAll("input[type=file]");
  await act(async () => { fireEvent.change(inputs[0], { target: { files: [file("hsf.csv")] } }); });
  fireEvent.click(screen.getByRole("button", { name: /männlich/ }));
  fireEvent.change(screen.getByPlaceholderText("K."), { target: { value: "M." } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Verlaufsauswertung erstellen/ })); });
  await waitFor(() => expect(container.querySelector("textarea")).toBeTruthy());

  // Text editieren + Blur -> /sns/check
  const ta = container.querySelector("textarea");
  fireEvent.change(ta, { target: { value: "1. Zusammenfassung\nEditiert." } });
  await act(async () => { fireEvent.blur(ta); });
  const checkCall = apiFetch.mock.calls.find(([u]) => u.endsWith("/sns/check"));
  expect(JSON.parse(checkCall[1].body).text).toBe("1. Zusammenfassung\nEditiert.");
  expect(JSON.parse(checkCall[1].body).result.grafiken).toBeUndefined();
  await waitFor(() => expect(screen.getByText(/fehlt/)).toBeTruthy());

  // DOCX aus dem editierten Text, mit Grafiken
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /DOCX herunterladen/ })); });
  const docxCall = apiFetch.mock.calls.find(([u]) => u.endsWith("/sns/docx"));
  const body = JSON.parse(docxCall[1].body);
  expect(body.text).toBe("1. Zusammenfassung\nEditiert.");
  expect(body.kuerzel).toBe("Frau K.");
  expect(Object.keys(body.result.grafiken)).toEqual(["hsf_faktoren", "krd"]);
  expect(toast).toHaveBeenCalledWith("DOCX heruntergeladen");

  // Tabs
  fireEvent.click(screen.getByText("Grafiken"));
  expect(container.querySelectorAll("img").length).toBe(2);
  fireEvent.click(screen.getByText("Kennwerte"));
  expect(screen.getByText(/Ordnungsübergang/)).toBeTruthy();
  expect(screen.getAllByText(/19\.03\.2026/).length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText(/Polung fraglich/)).toBeTruthy();
});
