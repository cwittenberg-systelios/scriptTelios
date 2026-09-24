/**
 * scriptTelios Frontend – Tests v19.40.1: deutliche Warteanzeige bis zum
 * ersten Wort (springende Punkte, Text, Sekundenzaehler, Start-Hinweis).
 */
import { useState } from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";

jest.mock("../src/api.js", () => ({
  fetchInterviewSets: jest.fn(),
  interviewTranscribe: jest.fn(),
  interviewChatStream: jest.fn(),
  interviewLease: jest.fn(() => Promise.resolve(null)),
  fetchTtsEngines: jest.fn(() => Promise.resolve({ engines: [], default: "browser" })),
  warmupInterviewServer: jest.fn(() => Promise.resolve({ status: "ok" })),
}));

import { fetchInterviewSets, interviewChatStream } from "../src/api.js";
import { _setSpeechProvider } from "../src/speech.js";
import { InterviewChat, CHAT_DEFAULT } from "../src/interview-chat.jsx";

const MANIFEST = {
  default_set: "kunst", abschnitte: {},
  sets: [{ key: "kunst", label: "Kunst", beschreibung: "", fragen: [
    { key: "klient", text: "Um wen geht es?", ziel_abschnitt: "meta", pflicht: true, pflichtaspekte: [], hinweis: "" },
  ] }],
};

function Harness() {
  const [v, setV] = useState({ ...CHAT_DEFAULT });
  return <InterviewChat value={v} onChange={setV} toast={() => {}} model={null} />;
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  fetchInterviewSets.mockResolvedValue(MANIFEST);
  _setSpeechProvider({
    name: "test", available: () => true, say: async () => true, cancel: () => {}, voiceStatus: () => "ok",
    sayStream: () => ({ push: () => {}, end: async () => true, done: Promise.resolve(true) }),
  });
});
afterEach(() => { jest.useRealTimers(); });

test("Start: animierte Punkte, 'Interview wird vorbereitet', Zaehler und Hinweis; weg beim ersten Wort", async () => {
  let emit;
  interviewChatStream.mockImplementationOnce((p, onEvent) => { emit = onEvent; return new Promise(() => {}); });
  render(<Harness />);
  const btn = await screen.findByTestId("chat-start-btn");
  jest.useFakeTimers();
  await act(async () => { fireEvent.click(btn); });
  const bubble = screen.getByTestId("chat-thinking");
  expect(bubble.querySelectorAll(".chat-dots span").length).toBe(3);
  expect(bubble.textContent).toMatch(/Interview wird vorbereitet/);
  expect(bubble.textContent).not.toMatch(/\d+ s/);
  await act(async () => { jest.advanceTimersByTime(9000); });
  expect(screen.getByTestId("chat-thinking").textContent).toMatch(/9 s/);
  expect(screen.getByTestId("chat-thinking-hint")).toBeTruthy();
  await act(async () => { emit({ type: "delta", text: "Um wen" }); });
  expect(screen.queryByTestId("chat-thinking")).toBeNull();
  expect(screen.getByTestId("chat-live").textContent).toBe("Um wen");
});

test("spaetere Runde: 'Denkt nach', kein Start-Hinweis", async () => {
  const m = { type: "meta", sage: "Um wen geht es?", thema: "klient", checkliste: {}, rueckfragen: {}, trigger_stufe: 0, klient: null, fertig: false };
  interviewChatStream.mockImplementationOnce(async (p, onEvent) => { onEvent({ type: "delta", text: m.sage }); onEvent(m); onEvent({ type: "done" }); return m; });
  interviewChatStream.mockImplementationOnce(() => new Promise(() => {}));
  render(<Harness />);
  fireEvent.click(await screen.findByTestId("chat-start-btn"));
  await screen.findByTestId("chat-antwort");
  jest.useFakeTimers();
  fireEvent.change(screen.getByTestId("chat-antwort"), { target: { value: "Um Frau L." } });
  await act(async () => { fireEvent.click(screen.getByTestId("chat-senden")); });
  expect(screen.getByTestId("chat-thinking").textContent).toMatch(/Denkt nach/);
  await act(async () => { jest.advanceTimersByTime(10000); });
  expect(screen.queryByTestId("chat-thinking-hint")).toBeNull();
});
