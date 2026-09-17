/**
 * scriptTelios Frontend – Tests fuer src/interview.jsx (v19.23).
 *
 * Dialog-Ablauf Start -> Frage -> Antwort -> Rueckfrage -> Weiter -> fertig,
 * Pflichtfrage ohne Antwort, Rueckfrage-Check-Fehler blockiert nicht,
 * Protokoll-Aufbau fuer /jobs/generate. api.js gemockt; kein Mikrofon,
 * kein speechSynthesis in jsdom (speak() faellt still durch).
 */
import { useState } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

jest.mock("../src/api.js", () => ({
  fetchInterviewSets: jest.fn(),
  interviewTranscribe: jest.fn(),
  interviewTurn: jest.fn(),
}));

import { fetchInterviewSets, interviewTurn } from "../src/api.js";
import { InterviewDialog, INTERVIEW_DEFAULT, buildInterviewProtokoll, interviewHasContent, speak } from "../src/interview.jsx";

const MANIFEST = {
  default_set: "gespraech",
  abschnitte: { auftragsklaerung: "Auftragsklärung", inhalte: "Inhalte", schluss: "Schluss" },
  sets: [
    { key: "gespraech", label: "Gespräch", beschreibung: "", fragen: [
      { key: "anliegen", text: "Worum ging es?", ziel_abschnitt: "auftragsklaerung", pflicht: false, pflichtaspekte: ["Anliegen"], hinweis: "" },
      { key: "selbstgefaehrdung", text: "Gab es Hinweise auf Selbstgefährdung?", ziel_abschnitt: "schluss", pflicht: true, pflichtaspekte: ["Aussage"], hinweis: "Pflicht" },
    ] },
    { key: "kunst", label: "Kunst", beschreibung: "", fragen: [
      { key: "methode", text: "Welche Methode?", ziel_abschnitt: "inhalte", pflicht: false, pflichtaspekte: [], hinweis: "" },
      { key: "selbstgefaehrdung", text: "Gab es Hinweise auf Selbstgefährdung?", ziel_abschnitt: "schluss", pflicht: true, pflichtaspekte: ["Aussage"], hinweis: "" },
    ] },
  ],
};

function Harness({ onState, toast = () => {} }) {
  const [v, setV] = useState({ ...INTERVIEW_DEFAULT });
  const onChange = (nv) => { setV(nv); onState && onState(nv); };
  return <InterviewDialog value={v} onChange={onChange} toast={toast} model={null} />;
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  fetchInterviewSets.mockResolvedValue(MANIFEST);
});

async function startDialog(toast) {
  const states = [];
  render(<Harness onState={(s) => states.push(s)} toast={toast} />);
  await screen.findByTestId("interview-start");
  fireEvent.click(screen.getByText("Interview starten"));
  await screen.findByTestId("interview-dialog");
  return states;
}

function answerAndNext(text) {
  fireEvent.change(screen.getByTestId("interview-antwort"), { target: { value: text } });
  fireEvent.click(screen.getByTestId("interview-weiter"));
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

  test("Ablauf: Antwort -> Rueckfrage -> Antwort -> Pflichtfrage -> fertig; Protokoll vollstaendig", async () => {
    interviewTurn
      .mockResolvedValueOnce({ rueckfrage: "Und was war das Ziel?", fehlende_aspekte: ["Ziel"], quelle: "llm" })
      .mockResolvedValueOnce({ rueckfrage: null, fehlende_aspekte: [], quelle: "llm" });
    const states = await startDialog();
    expect(screen.getByTestId("interview-frage").textContent).toContain("Worum ging es?");

    answerAndNext("Umgang mit Scham.");
    await screen.findByText(/Und was war das Ziel\?/);
    expect(interviewTurn).toHaveBeenCalledWith(expect.objectContaining({
      set: "gespraech", frage_key: "anliegen", antwort: "Umgang mit Scham.",
      pflichtaspekte: ["Anliegen"], rueckfrage_bereits: false, bisherige: [],
    }));

    answerAndNext("Mehr Selbstwert.");
    await waitFor(() => expect(screen.getByTestId("interview-frage").textContent).toContain("Selbstgefährdung"));
    // Rueckfrage-Antwort fuehrt zu KEINEM zweiten turn-Call (max. eine Rueckfrage)
    expect(interviewTurn).toHaveBeenCalledTimes(1);

    answerAndNext("Nein, keine Hinweise.");
    await screen.findByTestId("interview-fertig");
    expect(interviewTurn).toHaveBeenCalledTimes(2);
    expect(interviewTurn.mock.calls[1][0].bisherige).toEqual([{ frage: "Worum ging es?", antwort: "Umgang mit Scham." }]);

    const last = states[states.length - 1];
    const p = buildInterviewProtokoll(last);
    expect(p).toEqual({
      set: "gespraech", set_label: "Gespräch",
      eintraege: [
        { key: "anliegen", frage: "Worum ging es?", antwort: "Umgang mit Scham.", rueckfrage: "Und was war das Ziel?", rueckfrage_antwort: "Mehr Selbstwert.", ziel_abschnitt: "auftragsklaerung" },
        { key: "selbstgefaehrdung", frage: "Gab es Hinweise auf Selbstgefährdung?", antwort: "Nein, keine Hinweise.", rueckfrage: "", rueckfrage_antwort: "", ziel_abschnitt: "schluss" },
      ],
    });
    expect(interviewHasContent(last)).toBe(true);
  });

  test("Pflichtfrage ohne Antwort: Toast, kein Weiter, kein turn-Call", async () => {
    interviewTurn.mockResolvedValue({ rueckfrage: null, fehlende_aspekte: [], quelle: "keine" });
    const toast = jest.fn();
    await startDialog(toast);
    answerAndNext("Thema X.");
    await waitFor(() => expect(screen.getByTestId("interview-frage").textContent).toContain("Selbstgefährdung"));
    expect(screen.queryByText("Überspringen")).toBeNull();   // Pflichtfrage nicht ueberspringbar
    fireEvent.click(screen.getByTestId("interview-weiter"));
    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/Pflichtfrage/));
    expect(interviewTurn).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("interview-dialog")).toBeTruthy();
  });

  test("Rueckfrage-Check-Fehler blockiert den Dialog nicht", async () => {
    interviewTurn.mockRejectedValueOnce(new Error("Ollama down"))
      .mockResolvedValueOnce({ rueckfrage: null, fehlende_aspekte: [], quelle: "llm" });
    const toast = jest.fn();
    await startDialog(toast);
    answerAndNext("Thema X.");
    await waitFor(() => expect(screen.getByTestId("interview-frage").textContent).toContain("Selbstgefährdung"));
    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/weiter ohne Rückfrage/));
  });

  test("Ueberspringen einer Nicht-Pflichtfrage rendert '(nicht erhoben)' im Protokoll", async () => {
    interviewTurn.mockResolvedValue({ rueckfrage: null, fehlende_aspekte: [], quelle: "keine" });
    const states = await startDialog();
    fireEvent.click(screen.getByText("Überspringen"));
    await waitFor(() => expect(screen.getByTestId("interview-frage").textContent).toContain("Selbstgefährdung"));
    expect(interviewTurn).not.toHaveBeenCalled();
    answerAndNext("Keine.");
    await screen.findByTestId("interview-fertig");
    const p = buildInterviewProtokoll(states[states.length - 1]);
    expect(p.eintraege[0].antwort).toBe("");
    expect(screen.getByText("nicht erhoben")).toBeTruthy();
  });

  test("buildInterviewProtokoll ist null vor Abschluss; speak() faellt ohne TTS still durch", () => {
    expect(buildInterviewProtokoll({ ...INTERVIEW_DEFAULT })).toBeNull();
    expect(buildInterviewProtokoll(null)).toBeNull();
    expect(speak("Hallo")).toBe(false);
  });
});
