/**
 * scriptTelios Frontend – Tests v19.35.1 (Bedienung Dialog-Modus nach erstem
 * Praxistest): ein Stopp-Button, automatisches Senden nach der Aufnahme,
 * "Interview abbrechen", "Abschließen" erst nach der ersten Antwort.
 */
import { useState } from "react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";

jest.mock("../src/api.js", () => ({
  fetchInterviewSets: jest.fn(),
  interviewTranscribe: jest.fn(),
  interviewChatStream: jest.fn(),
  interviewLease: jest.fn(() => Promise.resolve(null)),
  fetchTtsEngines: jest.fn(() => Promise.resolve([{ key: "browser", label: "Browser", available: true }])),
  warmupInterviewServer: jest.fn(() => Promise.resolve({ status: "ok" })),
}));

import { fetchInterviewSets, interviewChatStream, interviewLease, interviewTranscribe } from "../src/api.js";
import { _setSpeechProvider } from "../src/speech.js";
import { InterviewChat, CHAT_DEFAULT } from "../src/interview-chat.jsx";

const MANIFEST = {
  default_set: "kunst", abschnitte: {},
  sets: [{ key: "kunst", label: "Kunst", beschreibung: "", fragen: [
    { key: "klient", text: "Um wen geht es?", ziel_abschnitt: "meta", pflicht: true, pflichtaspekte: [], hinweis: "" },
    { key: "selbstgefaehrdung", text: "Hinweise auf Selbstgefährdung?", ziel_abschnitt: "schluss", pflicht: true, pflichtaspekte: [], hinweis: "" },
  ] }],
};

function meta(extra = {}) {
  return { type: "meta", sage: "Um wen geht es?", thema: "klient", checkliste: {}, rueckfragen: {}, trigger_stufe: 0, klient: null, fertig: false, ...extra };
}
function streamOnce(m = meta()) {
  interviewChatStream.mockImplementationOnce(async (p, onEvent) => { onEvent({ type: "delta", text: m.sage }); onEvent(m); onEvent({ type: "done" }); return m; });
}

// Mikrofon-Attrappe
class FakeRecorder {
  static isTypeSupported() { return true; }
  constructor() { this.state = "inactive"; }
  start() { this.state = "recording"; }
  stop() { this.state = "inactive"; this.ondataavailable({ data: new Blob(["x".repeat(500)]) }); this.onstop(); }
}

function Harness({ toast = () => {} }) {
  const [v, setV] = useState({ ...CHAT_DEFAULT });
  return <InterviewChat value={v} onChange={setV} toast={toast} model={null} />;
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  fetchInterviewSets.mockResolvedValue(MANIFEST);
  global.MediaRecorder = FakeRecorder;
  Object.defineProperty(global.navigator, "mediaDevices", { configurable: true,
    value: { getUserMedia: jest.fn(async () => ({ getTracks: () => [{ stop: () => {} }] })) } });
  _setSpeechProvider({
    name: "test", available: () => true, say: async () => true, cancel: () => {}, voiceStatus: () => "ok",
    sayStream: () => ({ push: () => {}, end: async () => true, done: Promise.resolve(true) }),
  });
});

async function starten(props) {
  streamOnce();
  render(<Harness {...props} />);
  fireEvent.click(await screen.findByTestId("chat-start-btn"));
  await screen.findByTestId("chat-antwort");
}

test("waehrend der Aufnahme: nur Absenden/Verwerfen (+ Interview abbrechen), Wellen-Bubble im Chat", async () => {
  await starten();
  await act(async () => { fireEvent.click(screen.getByTestId("chat-rec-start")); });
  expect(screen.getByTestId("chat-rec-absenden")).toBeTruthy();
  expect(screen.getByTestId("chat-rec-verwerfen")).toBeTruthy();
  expect(screen.queryByTestId("chat-senden")).toBeNull();
  expect(screen.queryByTestId("chat-abschliessen")).toBeNull();
  expect(screen.queryByTestId("chat-rec-start")).toBeNull();
  expect(screen.getByTestId("chat-rec-bubble").dataset.state).toBe("recording");
  expect(screen.getByTestId("chat-antwort").disabled).toBe(true);
});

test("nach dem Stopp wird automatisch gesendet (getippter Text vorangestellt)", async () => {
  await starten();
  interviewTranscribe.mockResolvedValueOnce({ transcript: "Um Frau L." });
  streamOnce(meta({ sage: "Worum ging es?", thema: "anliegen" }));
  fireEvent.change(screen.getByTestId("chat-antwort"), { target: { value: "Also:" } });
  await act(async () => { fireEvent.click(screen.getByTestId("chat-rec-start")); });
  await act(async () => { fireEvent.click(screen.getByTestId("chat-rec-absenden")); });
  await waitFor(() => expect(interviewChatStream).toHaveBeenCalledTimes(2));
  const hist = interviewChatStream.mock.calls[1][0].historie;
  expect(hist[hist.length - 1]).toEqual({ rolle: "behandler", text: "Also: Um Frau L." });
  expect(screen.getByTestId("chat-antwort").value).toBe("");
});

test("leere Transkription: Hinweis, kein Senden", async () => {
  const toast = jest.fn();
  await starten({ toast });
  interviewTranscribe.mockResolvedValueOnce({ transcript: "" });
  await act(async () => { fireEvent.click(screen.getByTestId("chat-rec-start")); });
  await act(async () => { fireEvent.click(screen.getByTestId("chat-rec-absenden")); });
  await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringMatching(/Nichts verstanden/)));
  expect(interviewChatStream).toHaveBeenCalledTimes(1);
});

test("Abschließen erst nach der ersten Antwort", async () => {
  await starten();
  expect(screen.getByTestId("chat-abschliessen").disabled).toBe(true);
  streamOnce(meta({ sage: "Worum ging es?" }));
  fireEvent.change(screen.getByTestId("chat-antwort"), { target: { value: "Um Frau L." } });
  await act(async () => { fireEvent.click(screen.getByTestId("chat-senden")); });
  await waitFor(() => expect(screen.getByTestId("chat-abschliessen").disabled).toBe(false));
});

test("Interview abbrechen: Rueckfrage, alles verworfen, zurueck zum Start, Reservierung frei", async () => {
  await starten();
  const sid = interviewChatStream.mock.calls[0][0].session_id;
  streamOnce(meta({ sage: "Worum ging es?" }));
  fireEvent.change(screen.getByTestId("chat-antwort"), { target: { value: "Um Frau L." } });
  await act(async () => { fireEvent.click(screen.getByTestId("chat-senden")); });
  window.confirm = jest.fn(() => false);
  fireEvent.click(screen.getByTestId("chat-abbrechen"));
  expect(screen.getByTestId("chat-antwort")).toBeTruthy();          // abgelehnt -> bleibt
  window.confirm = jest.fn(() => true);
  await act(async () => { fireEvent.click(screen.getByTestId("chat-abbrechen")); });
  expect(await screen.findByTestId("chat-start-btn")).toBeTruthy();
  expect(interviewLease).toHaveBeenCalledWith(sid, "release");
});

test("Interview abbrechen waehrend einer Aufnahme: keine Transkription", async () => {
  await starten();
  await act(async () => { fireEvent.click(screen.getByTestId("chat-rec-start")); });
  await act(async () => { fireEvent.click(screen.getByTestId("chat-abbrechen")); });
  expect(await screen.findByTestId("chat-start-btn")).toBeTruthy();
  expect(interviewTranscribe).not.toHaveBeenCalled();
});

// ── v19.37: Absenden/Verwerfen, Wellen-Bubble ────────────────────────────────

test("Verwerfen: keine Transkription, Bubble weg, zurueck zu Aufnehmen", async () => {
  await starten();
  await act(async () => { fireEvent.click(screen.getByTestId("chat-rec-start")); });
  await act(async () => { fireEvent.click(screen.getByTestId("chat-rec-verwerfen")); });
  expect(interviewTranscribe).not.toHaveBeenCalled();
  expect(screen.queryByTestId("chat-rec-bubble")).toBeNull();
  expect(screen.getByTestId("chat-rec-start")).toBeTruthy();
  expect(interviewChatStream).toHaveBeenCalledTimes(1);
});

test("Absenden: Bubble bleibt als 'Transkribiere' stehen und wird durch das Transkript ersetzt", async () => {
  await starten();
  let fertig;
  interviewTranscribe.mockImplementationOnce(() => new Promise(r => { fertig = r; }));
  streamOnce(meta({ sage: "Worum ging es?", thema: "anliegen" }));
  await act(async () => { fireEvent.click(screen.getByTestId("chat-rec-start")); });
  await act(async () => { fireEvent.click(screen.getByTestId("chat-rec-absenden")); });
  expect(screen.getByTestId("chat-rec-bubble").dataset.state).toBe("transcribing");
  expect(screen.getByTestId("chat-rec-bubble").textContent).toMatch(/Transkribiere/);
  await act(async () => { fertig({ transcript: "Um Frau L." }); });
  await waitFor(() => expect(screen.queryByTestId("chat-rec-bubble")).toBeNull());
  expect(screen.getByText("Um Frau L.")).toBeTruthy();
  expect(interviewChatStream).toHaveBeenCalledTimes(2);
});

test("Pegel: Welle folgt der Lautstaerke und friert beim Absenden ein", async () => {
  let frameCb = null;
  global.requestAnimationFrame = (cb) => { frameCb = cb; return 1; };
  global.cancelAnimationFrame = () => { frameCb = null; };
  let amp = 0;
  window.AudioContext = function () {
    this.createAnalyser = () => ({ fftSize: 512, getByteTimeDomainData: (b) => { for (let i = 0; i < b.length; i++) b[i] = 128 + (i % 2 ? amp : -amp); } });
    this.createMediaStreamSource = () => ({ connect: () => {} });
    this.close = () => {};
  };
  try {
    await starten();
    await act(async () => { fireEvent.click(screen.getByTestId("chat-rec-start")); });
    const heights = () => [...screen.getByTestId("chat-rec-bubble").querySelectorAll("rect")].map(r => Number(r.getAttribute("height")));
    amp = 40;                                   // laut
    await act(async () => { frameCb(1000); });
    const laut = heights();
    expect(laut[laut.length - 1]).toBeGreaterThan(20);
    let fertig;
    interviewTranscribe.mockImplementationOnce(() => new Promise(r => { fertig = r; }));
    await act(async () => { fireEvent.click(screen.getByTestId("chat-rec-absenden")); });
    expect(frameCb).toBeNull();                 // Messung gestoppt -> Welle steht
    expect(heights()).toEqual(laut);
    await act(async () => { fertig({ transcript: "" }); });
  } finally {
    delete window.AudioContext; delete global.requestAnimationFrame; delete global.cancelAnimationFrame;
  }
});

test("v19.37.1: Interview abbrechen steht abgesetzt unter dem Chat, nicht in der Button-Zeile", async () => {
  await starten();
  const abbr = screen.getByTestId("chat-abbrechen");
  const senden = screen.getByTestId("chat-senden");
  expect(senden.parentElement.contains(abbr)).toBe(false);
  // im DOM nach Chat und Checkliste
  const liste = screen.getByTestId("chat-checkliste");
  expect(liste.compareDocumentPosition(abbr) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  // auch waehrend der Aufnahme an derselben Stelle
  await act(async () => { fireEvent.click(screen.getByTestId("chat-rec-start")); });
  expect(screen.getByTestId("chat-rec-absenden").parentElement.contains(screen.getByTestId("chat-abbrechen"))).toBe(false);
});
