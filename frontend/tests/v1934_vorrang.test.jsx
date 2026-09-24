/**
 * scriptTelios Frontend – Tests v19.34 (Vorrang laufender Interviews):
 * Freigabe der Reservierung (fertig, Unmount), Hinweis bei laufendem Auftrag,
 * Session-ID am Diktat.
 */
import { useState } from "react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";

jest.mock("../src/api.js", () => ({
  fetchInterviewSets: jest.fn(),
  interviewTranscribe: jest.fn(),
  interviewChatStream: jest.fn(),
  interviewLease: jest.fn(() => Promise.resolve(null)),
  warmupInterviewServer: jest.fn(() => Promise.resolve({ status: "ok" })),
  apiFetch: jest.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) })),
  getApiBase: () => "http://api",
}));

import { fetchInterviewSets, interviewChatStream, interviewLease } from "../src/api.js";
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
  return { type: "meta", sage: "Frage?", thema: "klient", checkliste: {}, rueckfragen: {}, trigger_stufe: 0, klient: null, fertig: false, ...extra };
}

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

async function starten() {
  interviewChatStream.mockImplementationOnce(async (p, onEvent) => {
    const m = meta(); onEvent({ type: "delta", text: "Frage?" }); onEvent(m); onEvent({ type: "done" }); return m;
  });
  const utils = render(<Harness />);
  fireEvent.click(await screen.findByTestId("chat-start-btn"));
  await screen.findByTestId("chat-antwort");
  return utils;
}

const releases = () => interviewLease.mock.calls.filter(c => c[1] === "release");

describe("Reservierung", () => {
  test("Unmount waehrend des Gespraechs gibt frei", async () => {
    const { unmount } = await starten();
    const sid = interviewChatStream.mock.calls[0][0].session_id;
    expect(sid).toBeTruthy();
    expect(releases()).toHaveLength(0);
    unmount();
    expect(releases()).toEqual([[sid, "release"]]);
  });

  test("fertig gibt frei", async () => {
    await starten();
    const sid = interviewChatStream.mock.calls[0][0].session_id;
    interviewChatStream.mockImplementationOnce(async (p, onEvent) => {
      const m = meta({ sage: "Danke, das habe ich.", fertig: true });
      onEvent({ type: "delta", text: m.sage }); onEvent(m); onEvent({ type: "done" }); return m;
    });
    fireEvent.change(screen.getByTestId("chat-antwort"), { target: { value: "Um Frau L., keine Hinweise." } });
    await act(async () => { fireEvent.click(screen.getByTestId("chat-senden")); });
    await waitFor(() => expect(releases()).toEqual([[sid, "release"]]));
  });

  test("laufender Auftrag: Hinweis bis zum ersten Wort", async () => {
    await starten();
    let weiter;
    interviewChatStream.mockImplementationOnce(async (p, onEvent) => {
      onEvent({ type: "status", jobs_running: 1 });
      await new Promise(r => { weiter = r; });
      const m = meta({ sage: "Und dann?" });
      onEvent({ type: "delta", text: m.sage }); onEvent(m); onEvent({ type: "done" }); return m;
    });
    fireEvent.change(screen.getByTestId("chat-antwort"), { target: { value: "Um Frau L." } });
    await act(async () => { fireEvent.click(screen.getByTestId("chat-senden")); });
    expect(await screen.findByTestId("chat-jobs-busy")).toBeTruthy();
    await act(async () => { weiter(); });
    await waitFor(() => expect(screen.queryByTestId("chat-jobs-busy")).toBeNull());
  });
});
