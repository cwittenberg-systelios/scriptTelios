/**
 * scriptTelios Frontend – Tests fuer den Dialog-Modus (v19.31):
 * src/interview-chat.jsx (Turn-Ablauf mit gemocktem Stream, Checkliste,
 * fertig-Verweigerung, Klient-Uebernahme, Feedback) und
 * src/speech.js sayStream / splitSentences.
 */
import { useState } from "react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";

jest.mock("../src/api.js", () => ({
  fetchInterviewSets: jest.fn(),
  interviewTranscribe: jest.fn(),
  interviewChatStream: jest.fn(),
  warmupInterviewServer: jest.fn(() => Promise.resolve({ status: "ok" })),
  interviewLease: jest.fn(() => Promise.resolve(null)),
  apiFetch: jest.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) })),
  getApiBase: () => "http://api",
}));

import { fetchInterviewSets, interviewChatStream } from "../src/api.js";
import { _setSpeechProvider, splitSentences } from "../src/speech.js";
import { InterviewChat, CHAT_DEFAULT, buildInterviewGespraech, chatHasContent } from "../src/interview-chat.jsx";

const MANIFEST = {
  default_set: "kunst", abschnitte: {},
  sets: [{ key: "kunst", label: "Kunst", beschreibung: "", fragen: [
    { key: "klient", text: "Um wen geht es?", ziel_abschnitt: "meta", pflicht: true, pflichtaspekte: [], hinweis: "" },
    { key: "anliegen", text: "Was war das Anliegen?", ziel_abschnitt: "auftragsklaerung", pflicht: false, pflichtaspekte: [], hinweis: "" },
    { key: "selbstgefaehrdung", text: "Hinweise auf Selbstgefährdung?", ziel_abschnitt: "schluss", pflicht: true, pflichtaspekte: [], hinweis: "" },
  ] }],
};

// Stream-Mock: streamt `sage` in zwei Deltas und liefert meta.
function mockTurn(meta) {
  interviewChatStream.mockImplementationOnce(async (payload, onEvent) => {
    const sage = meta.sage;
    onEvent({ type: "delta", text: sage.slice(0, 5) });
    onEvent({ type: "delta", text: sage.slice(5) });
    const m = { type: "meta", checkliste: {}, rueckfragen: {}, trigger_stufe: 0, klient: null, fertig: false, fertig_verweigert: false, regie: null, ...meta };
    onEvent(m); onEvent({ type: "done" });
    return m;
  });
}

let spokenStream;
function Harness({ onState, onKlient, toast = () => {} }) {
  const [v, setV] = useState({ ...CHAT_DEFAULT });
  return <InterviewChat value={v} onChange={(nv) => { setV(nv); onState && onState(nv); }} toast={toast} model={null} onKlient={onKlient} />;
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  fetchInterviewSets.mockResolvedValue(MANIFEST);
  spokenStream = [];
  _setSpeechProvider({
    name: "test", available: () => true, say: async () => true, cancel: () => {},
    sayStream: () => { const parts = []; spokenStream.push(parts); return { push: (d) => parts.push(d), end: async () => true, done: Promise.resolve(true) }; },
  });
});

async function begin(opts = {}) {
  const states = [];
  mockTurn({ sage: "Hallo! Um wen geht es heute?", thema: "klient" });
  render(<Harness onState={(s) => states.push(s)} {...opts} />);
  await screen.findByTestId("chat-start");
  fireEvent.click(screen.getByTestId("chat-start-btn"));
  await screen.findByTestId("chat-dialog");
  await waitFor(() => expect(screen.getByText("Hallo! Um wen geht es heute?")).toBeTruthy());
  return states;
}

async function answer(text) {
  fireEvent.change(screen.getByTestId("chat-antwort"), { target: { value: text } });
  await act(async () => { fireEvent.click(screen.getByTestId("chat-senden")); });
}

describe("InterviewChat", () => {
  test("Start: erster Turn mit leerer Historie, Antwort gestreamt und vorgelesen", async () => {
    await begin();
    expect(interviewChatStream.mock.calls[0][0]).toEqual(expect.objectContaining({ set: "kunst", historie: [], checkliste: {} }));
    expect(spokenStream[0].join("")).toBe("Hallo! Um wen geht es heute?");
    expect(screen.getByTestId("chat-checkliste").textContent).toContain("0/3");
  });

  test("Antwort -> Turn mit Historie; Klient und Checkliste uebernommen; Enter sendet", async () => {
    const onKlient = jest.fn();
    const states = await begin({ onKlient });
    mockTurn({ sage: "Danke. Worum ging es für Herrn Müller?", thema: "anliegen",
      checkliste: { klient: "abgedeckt" }, klient: { anrede: "Herr", initial: "M.", gender: "m" } });
    fireEvent.change(screen.getByTestId("chat-antwort"), { target: { value: "Um Herrn Müller." } });
    await act(async () => { fireEvent.keyDown(screen.getByTestId("chat-antwort"), { key: "Enter" }); });
    await waitFor(() => expect(screen.getByText(/Worum ging es für Herrn Müller/)).toBeTruthy());
    const payload = interviewChatStream.mock.calls[1][0];
    expect(payload.historie).toEqual([
      { rolle: "system", text: "Hallo! Um wen geht es heute?", thema: "klient" },
      { rolle: "behandler", text: "Um Herrn Müller." },
    ]);
    expect(onKlient).toHaveBeenCalledWith({ anrede: "Herr", initial: "M.", gender: "m" });
    expect(screen.getByTestId("chat-klient").textContent).toBe("Herr M.");
    expect(screen.getByTestId("chat-checkliste").textContent).toContain("1/3");
    const last = states[states.length - 1];
    expect(last.klient.initial).toBe("M.");
    expect(buildInterviewGespraech(last)).toBeNull();   // noch nicht fertig
  });

  test("fertig_verweigert -> Toast, weiter im Gespraech; fertig -> Abschluss + Feedback + Gespraech-Objekt", async () => {
    const toast = jest.fn();
    const states = await begin({ toast });
    mockTurn({ sage: "Danke, das war's.", thema: "", fertig: false, fertig_verweigert: true });
    await answer("Bitte abschließen.");
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringMatching(/Pflichtpunkte fehlen/)));
    expect(screen.getByTestId("chat-antwort")).toBeTruthy();
    mockTurn({ sage: "Danke dir, das habe ich alles.", thema: "", fertig: true,
      checkliste: { klient: "abgedeckt", anliegen: "abgedeckt", selbstgefaehrdung: "abgedeckt" }, klient: { anrede: "Frau", initial: "K.", gender: "w" } });
    await answer("Um Frau K., keine Hinweise auf Selbstgefährdung.");
    await waitFor(() => expect(screen.getByText(/Gespräch abgeschlossen/)).toBeTruthy());
    expect(screen.getByText(/Feedback zur Ausgabe/)).toBeTruthy();
    const g = buildInterviewGespraech(states[states.length - 1]);
    expect(g.set).toBe("kunst");
    expect(g.historie.map(t => t.rolle)).toEqual(["system", "behandler", "system", "behandler", "system"]);
    expect(g.klient.initial).toBe("K.");
    expect(chatHasContent(states[states.length - 1])).toBe(true);
    fireEvent.click(screen.getByText("Noch etwas ergänzen"));
    await screen.findByTestId("chat-antwort");
  });

  test("Stream-Fehler: Toast, Behandler-Antwort bleibt in der Historie", async () => {
    const toast = jest.fn();
    const states = await begin({ toast });
    interviewChatStream.mockRejectedValueOnce(new Error("Ollama down"));
    await answer("Meine Antwort.");
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringMatching(/nochmal/)));
    const last = states[states.length - 1];
    expect(last.historie[last.historie.length - 1]).toEqual({ rolle: "behandler", text: "Meine Antwort." });
  });

  test("buildInterviewGespraech ist null ohne Historie", () => {
    expect(buildInterviewGespraech({ ...CHAT_DEFAULT })).toBeNull();
  });
});

describe("splitSentences", () => {
  test("trennt an Satzenden, haelt Abkuerzungen und Kuerzel zusammen", () => {
    const r = splitSentences("Verstanden. Wie ging es Frau K. danach? Und z.B. heute? Rest");
    expect(r.sentences).toEqual(["Verstanden.", "Wie ging es Frau K. danach?", "Und z.B. heute?"]);
    expect(r.rest).toBe(" Rest");
  });
  test("unvollstaendiger Satz bleibt im Rest", () => {
    expect(splitSentences("Hallo du")).toEqual({ sentences: [], rest: "Hallo du" });
  });
});
