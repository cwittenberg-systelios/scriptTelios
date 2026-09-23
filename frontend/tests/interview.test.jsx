/**
 * scriptTelios Frontend – Tests fuer src/interview.jsx (v19.23 + v19.24).
 *
 * Dialog-Ablauf inkl. Klient-Frage, Rueckfrage, Suizidalitaets-Trigger-Kette,
 * Abschluss-Check mit "So lassen", Sprachsequenzen (gemockter Provider),
 * Feedback-Button, Protokoll-Aufbau. api.js gemockt; kein Mikrofon.
 */
import { useState } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

jest.mock("../src/api.js", () => ({
  fetchInterviewSets: jest.fn(),
  interviewTranscribe: jest.fn(),
  interviewTurn: jest.fn(),
  interviewAbschluss: jest.fn(),
  warmupInterviewServer: jest.fn(() => Promise.resolve({ status: "ok" })),
  apiFetch: jest.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) })),
  getApiBase: () => "http://api",
}));

import { fetchInterviewSets, interviewTurn, interviewAbschluss, warmupInterviewServer } from "../src/api.js";
import { _setSpeechProvider } from "../src/speech.js";
import { InterviewDialog, INTERVIEW_DEFAULT, buildInterviewProtokoll, interviewHasContent } from "../src/interview.jsx";

const MANIFEST = {
  default_set: "gespraech",
  abschnitte: { meta: "Organisatorisch", auftragsklaerung: "Auftragsklärung", inhalte: "Inhalte", schluss: "Schluss" },
  sets: [
    { key: "gespraech", label: "Gespräch", beschreibung: "", fragen: [
      { key: "klient", text: "Um wen geht es?", ziel_abschnitt: "meta", pflicht: true, pflichtaspekte: [], hinweis: "" },
      { key: "anliegen", text: "Worum ging es?", ziel_abschnitt: "auftragsklaerung", pflicht: false, pflichtaspekte: ["Anliegen"], hinweis: "" },
      { key: "selbstgefaehrdung", text: "Gab es Hinweise auf Selbstgefährdung?", ziel_abschnitt: "schluss", pflicht: true, pflichtaspekte: ["Aussage"], hinweis: "Pflicht" },
    ] },
    { key: "kunst", label: "Kunst", beschreibung: "", fragen: [
      { key: "klient", text: "Um wen geht es?", ziel_abschnitt: "meta", pflicht: true, pflichtaspekte: [], hinweis: "" },
      { key: "methode", text: "Welche Methode?", ziel_abschnitt: "inhalte", pflicht: false, pflichtaspekte: [], hinweis: "" },
      { key: "selbstgefaehrdung", text: "Gab es Hinweise auf Selbstgefährdung?", ziel_abschnitt: "schluss", pflicht: true, pflichtaspekte: ["Aussage"], hinweis: "" },
    ] },
  ],
};

const NONE = { rueckfrage: null, fehlende_aspekte: [], quelle: "keine", ueberleitung: "Danke.", quittung: null, rueckfrage_typ: null, trigger_stufe: 0, klient: null };
const KLIENT = { ...NONE, quelle: "klient", klient: { anrede: "Herr", initial: "M.", gender: "m" } };

let spoken;
function Harness({ onState, onKlient, toast = () => {} }) {
  const [v, setV] = useState({ ...INTERVIEW_DEFAULT });
  const onChange = (nv) => { setV(nv); onState && onState(nv); };
  return <InterviewDialog value={v} onChange={onChange} toast={toast} model={null} onKlient={onKlient} />;
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  fetchInterviewSets.mockResolvedValue(MANIFEST);
  interviewAbschluss.mockResolvedValue({ punkte: [], model_used: "m" });
  spoken = [];
  _setSpeechProvider({ name: "test", available: () => true, say: async (parts) => { spoken.push(parts.filter(Boolean)); return true; }, cancel: () => {} });
});

async function startDialog(opts = {}) {
  const states = [];
  render(<Harness onState={(s) => states.push(s)} {...opts} />);
  await screen.findByTestId("interview-start");
  fireEvent.click(screen.getByText("Interview starten"));
  await screen.findByTestId("interview-dialog");
  return states;
}

function answerAndNext(text) {
  fireEvent.change(screen.getByTestId("interview-antwort"), { target: { value: text } });
  fireEvent.click(screen.getByTestId("interview-weiter"));
}

async function expectFrage(text) {
  await waitFor(() => expect(screen.getByTestId("interview-frage").textContent).toContain(text));
}

describe("InterviewDialog", () => {
  test("Start zeigt Fragen des Default-Sets und merkt gewaehltes Set", async () => {
    render(<Harness />);
    await screen.findByTestId("interview-start");
    expect(screen.getByText("Worum ging es?")).toBeTruthy();
    fireEvent.change(screen.getByTestId("interview-set"), { target: { value: "kunst" } });
    await screen.findByText("Welche Methode?");
    expect(localStorage.getItem("st_interview_set")).toBe("kunst");
  });

  test("Klient-Frage fuellt Kuerzel, Rueckfrage mit Quittung, Abschluss ohne Punkte -> fertig; Protokoll vollstaendig", async () => {
    const onKlient = jest.fn();
    interviewTurn
      .mockResolvedValueOnce(KLIENT)
      .mockResolvedValueOnce({ ...NONE, rueckfrage: "Und was war das Ziel?", fehlende_aspekte: ["Ziel"], quelle: "llm", rueckfrage_typ: "aspekt", quittung: "Verstanden.", ueberleitung: null })
      .mockResolvedValueOnce(NONE);
    const states = await startDialog({ onKlient });
    expect(spoken[0]).toEqual(["Um wen geht es?"]);

    answerAndNext("Herr Müller");
    await expectFrage("Worum ging es?");
    expect(onKlient).toHaveBeenCalledWith({ anrede: "Herr", initial: "M.", gender: "m" });
    expect(screen.getByTestId("interview-klient").textContent).toBe("Herr M.");
    expect(spoken[spoken.length - 1]).toEqual(["Danke.", "Worum ging es?"]);

    answerAndNext("Umgang mit Scham.");
    await expectFrage("Und was war das Ziel?");
    expect(spoken[spoken.length - 1]).toEqual(["Verstanden.", "Und was war das Ziel?"]);
    expect(interviewTurn).toHaveBeenLastCalledWith(expect.objectContaining({
      set: "gespraech", frage_key: "anliegen", antwort: "Umgang mit Scham.", anrede: "Herr M.",
      trigger_stufe: 0, rueckfrage_bereits: false, bisherige: [], frage_index: 1,
    }));

    answerAndNext("Mehr Selbstwert.");
    await expectFrage("Selbstgefährdung");
    expect(interviewTurn).toHaveBeenCalledTimes(2);   // Aspekt-Rueckfrage: kein zweiter Check

    answerAndNext("Nein, keine Hinweise.");
    await screen.findByTestId("interview-fertig");
    expect(interviewAbschluss).toHaveBeenCalledTimes(1);
    expect(interviewAbschluss.mock.calls[0][0].protokoll.eintraege[2].antwort).toBe("Nein, keine Hinweise.");

    const p = buildInterviewProtokoll(states[states.length - 1]);
    expect(p.set).toBe("gespraech");
    expect(p.session_id).toMatch(/^s/);
    expect(p.eintraege[0]).toEqual({ key: "klient", frage: "Um wen geht es?", antwort: "Herr Müller", nachfragen: [], ziel_abschnitt: "meta" });
    expect(p.eintraege[1].nachfragen).toEqual([{ typ: "aspekt", frage: "Und was war das Ziel?", antwort: "Mehr Selbstwert." }]);
    expect(p.abschluss).toEqual([]);
    expect(interviewHasContent(states[states.length - 1])).toBe(true);
    expect(screen.getByText(/Feedback zur Ausgabe/)).toBeTruthy();
  });

  test("Suizidalitaets-Trigger-Kette 0 -> 1 -> 2 -> weiter, Anrede in den Calls", async () => {
    interviewTurn
      .mockResolvedValueOnce(KLIENT)
      .mockResolvedValueOnce({ ...NONE, rueckfrage: "Gab es konkrete Pläne, und ist Herr M. absprachefähig?", quelle: "trigger", rueckfrage_typ: "trigger:suizidalitaet", trigger_stufe: 1, quittung: "Ja, verstanden.", ueberleitung: null })
      .mockResolvedValueOnce({ ...NONE, rueckfrage: "Was wurde vereinbart – Kooperationsbedingung?", quelle: "trigger", rueckfrage_typ: "trigger:suizidalitaet", trigger_stufe: 2, quittung: "Verstanden.", ueberleitung: null })
      .mockResolvedValueOnce({ ...NONE, quelle: "keine" })
      .mockResolvedValueOnce(NONE);
    const states = await startDialog();
    answerAndNext("Herr Müller");
    await expectFrage("Worum ging es?");
    answerAndNext("Lebensmüde Gedanken geäußert.");
    await expectFrage("konkrete Pläne");
    expect(screen.getByText(/Nachfrage Suizidalität/)).toBeTruthy();
    answerAndNext("Keine Pläne, aber unsicher absprachefähig.");
    await expectFrage("Kooperationsbedingung");
    expect(interviewTurn).toHaveBeenLastCalledWith(expect.objectContaining({ trigger_stufe: 1, rueckfrage_bereits: true, antwort: "Keine Pläne, aber unsicher absprachefähig." }));
    answerAndNext("Kooperationsbedingung und Nachtdienst.");
    await expectFrage("Selbstgefährdung");
    expect(interviewTurn).toHaveBeenLastCalledWith(expect.objectContaining({ trigger_stufe: 2 }));
    answerAndNext("Siehe oben, distanziert.");
    await screen.findByTestId("interview-fertig");
    const p = buildInterviewProtokoll(states[states.length - 1]);
    expect(p.eintraege[1].nachfragen.map(n => n.typ)).toEqual(["trigger:suizidalitaet", "trigger:suizidalitaet"]);
    expect(p.eintraege[1].nachfragen[1].antwort).toBe("Kooperationsbedingung und Nachtdienst.");
  });

  test("Abschluss-Check: Punkt beantworten, Punkt so lassen, Sprachsequenz", async () => {
    interviewTurn.mockResolvedValueOnce(KLIENT).mockResolvedValue(NONE);
    interviewAbschluss.mockResolvedValueOnce({ punkte: [
      { typ: "widerspruch", bezug: ["1", "2"], frage: "Wurde etwas vereinbart oder nicht?" },
      { typ: "luecke", bezug: ["1"], frage: "Welche Übung genau?" },
    ], model_used: "m" });
    const states = await startDialog();
    answerAndNext("Frau K.");
    await expectFrage("Worum ging es?");
    answerAndNext("Thema X.");
    await expectFrage("Selbstgefährdung");
    answerAndNext("Keine.");
    await screen.findByTestId("interview-abschluss");
    expect(spoken[spoken.length - 1]).toEqual(["Danke.", "Ich habe noch 2 Fragen zum Ganzen.", "Wurde etwas vereinbart oder nicht?"]);
    expect(screen.getByText(/Widerspruch/)).toBeTruthy();
    answerAndNext("Doch, eine Atemübung.");
    await expectFrage("Welche Übung genau?");
    fireEvent.click(screen.getByTestId("interview-belassen"));
    await screen.findByTestId("interview-fertig");
    expect(screen.getByText(/1 Punkt bewusst offen gelassen/)).toBeTruthy();
    const p = buildInterviewProtokoll(states[states.length - 1]);
    expect(p.abschluss).toEqual([
      { typ: "widerspruch", bezug: ["1", "2"], frage: "Wurde etwas vereinbart oder nicht?", antwort: "Doch, eine Atemübung.", belassen: false },
      { typ: "luecke", bezug: ["1"], frage: "Welche Übung genau?", antwort: "", belassen: true },
    ]);
  });

  test("Abschluss-Check-Fehler blockiert nicht; Pflichtfrage ohne Antwort haelt an", async () => {
    interviewTurn.mockResolvedValueOnce(KLIENT).mockResolvedValue(NONE);
    interviewAbschluss.mockRejectedValueOnce(new Error("down"));
    const toast = jest.fn();
    await startDialog({ toast });
    answerAndNext("Frau K.");
    await expectFrage("Worum ging es?");
    answerAndNext("Thema.");
    await expectFrage("Selbstgefährdung");
    expect(screen.queryByText("Überspringen")).toBeNull();
    fireEvent.click(screen.getByTestId("interview-weiter"));
    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/Pflichtfrage/));
    answerAndNext("Keine.");
    await screen.findByTestId("interview-fertig");
    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/Abschluss-Prüfung nicht möglich/));
  });

  test("Klient nicht erkannt: eine Rueckfrage, dann weiter ohne Klient (D4=B)", async () => {
    interviewTurn
      .mockResolvedValueOnce({ ...NONE, rueckfrage: "Ich habe kein Kürzel erkannt …", quelle: "klient", rueckfrage_typ: "klient", quittung: "Okay.", ueberleitung: null })
      .mockResolvedValueOnce({ ...NONE, quelle: "klient" })
      .mockResolvedValue(NONE);
    const onKlient = jest.fn();
    await startDialog({ onKlient });
    answerAndNext("keine Ahnung");
    await expectFrage("kein Kürzel erkannt");
    answerAndNext("weiss nicht");
    await expectFrage("Worum ging es?");
    expect(onKlient).not.toHaveBeenCalled();
    expect(screen.queryByTestId("interview-klient")).toBeNull();
  });

  test("Rueckfrage-Check-Fehler blockiert den Dialog nicht", async () => {
    interviewTurn.mockResolvedValueOnce(KLIENT).mockRejectedValueOnce(new Error("Ollama down")).mockResolvedValue(NONE);
    const toast = jest.fn();
    await startDialog({ toast });
    answerAndNext("Frau K.");
    await expectFrage("Worum ging es?");
    answerAndNext("Thema X.");
    await expectFrage("Selbstgefährdung");
    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/weiter ohne Rückfrage/));
  });

  test("Server aus: Bundle-Manifest, Warmup, Statuszeile; st-health-ok laedt neu", async () => {
    fetchInterviewSets.mockResolvedValueOnce({ ...MANIFEST, source: "bundle" }).mockResolvedValueOnce({ ...MANIFEST, source: "server" });
    warmupInterviewServer.mockResolvedValueOnce({ status: "starting" });
    render(<Harness />);
    await screen.findByTestId("interview-start");
    expect(warmupInterviewServer).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Worum ging es?")).toBeTruthy();                // sofort nutzbar
    await screen.findByText(/Server startet/);
    window.dispatchEvent(new Event("st-health-ok"));
    await waitFor(() => expect(fetchInterviewSets).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByTestId("interview-server")).toBeNull());
  });

  test("buildInterviewProtokoll ist null vor Abschluss", () => {
    expect(buildInterviewProtokoll({ ...INTERVIEW_DEFAULT })).toBeNull();
    expect(buildInterviewProtokoll(null)).toBeNull();
  });
});
