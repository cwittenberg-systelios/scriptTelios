/**
 * scriptTelios Frontend – Tests v19.42: Dialog-Modus aufgeraeumt, Text
 * synchron zum Vorlesen.
 *  - speech.js: onSentence/onSkip, Dauer, speaks, cancel beendet Wiedergabe
 *  - Chat: Text erst beim Vorlesen, rollend, Klick zeigt alles, Sicherung 8 s,
 *    ohne Vorlesen sofort; Fragenliste als aufklappbare Zeile
 */
import { useState } from "react";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";

jest.mock("../src/api.js", () => {
  const actual = jest.requireActual("../src/api.js");
  return {
    ...actual,
    apiFetch: jest.fn(() => Promise.resolve({ ok: true, json: async () => ({ jobs: [] }) })),
    getApiBase: () => "http://api",
    startJob: jest.fn(),
    fetchInterviewSets: jest.fn(),
    interviewTranscribe: jest.fn(),
    interviewChatStream: jest.fn(),
    interviewLease: jest.fn(() => Promise.resolve(null)),
    fetchTtsEngines: jest.fn(() => Promise.resolve({ engines: [], default: "browser" })),
    warmupInterviewServer: jest.fn(() => Promise.resolve({ status: "ok" })),
    interviewTts: jest.fn(),
  };
});

import { fetchInterviewSets, interviewChatStream } from "../src/api.js";
import { _setSpeechProvider, createServerProvider, _setLastSpokeAt } from "../src/speech.js";
import { InterviewChat, CHAT_DEFAULT } from "../src/interview-chat.jsx";

const MANIFEST = {
  default_set: "kunst", abschnitte: {},
  sets: [{ key: "kunst", label: "Kunst", beschreibung: "", fragen: [
    { key: "klient", text: "Um wen geht es?", ziel_abschnitt: "meta", pflicht: true, pflichtaspekte: [], hinweis: "" },
    { key: "anliegen", text: "Worum ging es im Gespräch?", ziel_abschnitt: "a", pflicht: false, pflichtaspekte: [], hinweis: "" },
  ] }],
};

// ── speech.js ────────────────────────────────────────────────────────────────

describe("Server-Provider: Satz-Ereignisse", () => {
  let audios;
  beforeEach(() => {
    audios = [];
    _setLastSpokeAt(Date.now());
    global.URL.createObjectURL = jest.fn(() => "blob:x");
    global.URL.revokeObjectURL = jest.fn();
    global.Audio = function () {
      this.duration = 2.5;
      this.pause = jest.fn();
      this.play = () => Promise.resolve();
      audios.push(this);
    };
  });

  test("onSentence beim Start der Wiedergabe mit Audiodauer, speaks=true", async () => {
    const events = [];
    const sp = createServerProvider({ fetchAudio: async () => ({}), engine: () => "chatterbox:gunther" });
    const s = sp.sayStream({ onSentence: (e) => events.push(e) });
    expect(s.speaks).toBe(true);
    s.push("Um wen geht es? Bitte ");
    await act(async () => { await new Promise(r => setTimeout(r, 400)); });
    expect(events).toEqual([{ text: "Um wen geht es?", durationMs: 2500 }]);
    s.push("das Kürzel.");
    const done = s.end();
    audios[0].onended();
    await act(async () => { await new Promise(r => setTimeout(r, 20)); });
    expect(events[1]).toEqual({ text: "Bitte das Kürzel.", durationMs: 2500 });
    audios[1].onended();
    await expect(done).resolves.toBe(true);
  });

  test("Fehler ohne Ersatzstimme -> onSkip; cancel beendet laufende Wiedergabe", async () => {
    const skips = [];
    const sp = createServerProvider({ fetchAudio: () => Promise.reject(new Error("weg")), engine: () => "x" });
    const s = sp.sayStream({ onSkip: (e) => skips.push(e.text) });
    s.push("Hallo. ");
    await act(async () => { await new Promise(r => setTimeout(r, 400)); });
    expect(skips).toEqual(["Hallo."]);

    const sp2 = createServerProvider({ fetchAudio: async () => ({}), engine: () => "x" });
    const s2 = sp2.sayStream();
    s2.push("Eins. ");
    await act(async () => { await new Promise(r => setTimeout(r, 400)); });
    const done = s2.end();
    sp2.cancel();                                    // pause() feuert kein onended
    await expect(done).resolves.toBe(false);
  });
});

// ── Chat ─────────────────────────────────────────────────────────────────────

function Harness() {
  const [v, setV] = useState({ ...CHAT_DEFAULT });
  return <InterviewChat value={v} onChange={setV} toast={() => {}} model={null} />;
}

// Stimme, die der Test steuert: sentence(i) startet Satz i, finish() beendet
function fakeVoice({ speaks = true } = {}) {
  const ctl = { opts: null, cancel: jest.fn(), resolve: null, pushed: [] };
  _setSpeechProvider({
    name: "fake", available: () => true, say: async () => true, voiceStatus: () => "ok",
    cancel: () => { ctl.cancel(); ctl.resolve && ctl.resolve(false); },
    sayStream: (opts) => {
      ctl.opts = opts;
      const done = new Promise(r => { ctl.resolve = r; });
      return { push: (d) => ctl.pushed.push(d), end: () => done, done, speaks };
    },
  });
  return ctl;
}

function streamOnce(sage) {
  interviewChatStream.mockImplementationOnce(async (p, onEvent) => {
    onEvent({ type: "delta", text: sage });
    const m = { type: "meta", sage, thema: "klient", checkliste: {}, rueckfragen: {}, trigger_stufe: 0, klient: null, fertig: false };
    onEvent(m); onEvent({ type: "done" });
    return m;
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
  fetchInterviewSets.mockResolvedValue(MANIFEST);
});
afterEach(() => { jest.useRealTimers(); });

async function starten(sage) {
  streamOnce(sage);
  render(<Harness />);
  fireEvent.click(await screen.findByTestId("chat-start-btn"));
  await screen.findByTestId("chat-dialog");
}

test("Text erscheint erst beim Vorlesen, Satz fuer Satz und rollend", async () => {
  const ctl = fakeVoice();
  await starten("Hallo! Um wen geht es?");
  expect(screen.getByTestId("chat-log").textContent).not.toMatch(/Um wen/);   // noch nicht vorgelesen
  expect(screen.getByTestId("chat-thinking")).toBeTruthy();
  jest.useFakeTimers();
  act(() => { ctl.opts.onSentence({ text: "Hallo!", durationMs: 1000 }); });
  expect(screen.getByTestId("chat-speaking")).toBeTruthy();
  const live = () => screen.getByTestId("chat-live").textContent.replace("🔊", "");
  act(() => { jest.advanceTimersByTime(200); });
  const teil = live();
  expect(teil.length).toBeGreaterThan(0);
  act(() => { jest.advanceTimersByTime(1000); });
  expect(live()).toContain("Hallo!");
  act(() => { ctl.opts.onSentence({ text: "Um wen geht es?", durationMs: 1500 }); });
  act(() => { jest.advanceTimersByTime(2000); });
  expect(live()).toBe("Hallo! Um wen geht es?");
  await act(async () => { ctl.resolve(true); });                       // fertig vorgelesen
  expect(screen.queryByTestId("chat-speaking")).toBeNull();
  expect(screen.getByText("Hallo! Um wen geht es?")).toBeTruthy();
});

test("Klick auf die Sprechblase stoppt und zeigt den ganzen Text", async () => {
  const ctl = fakeVoice();
  await starten("Hallo! Um wen geht es?");
  act(() => { ctl.opts.onSentence({ text: "Hallo!", durationMs: 5000 }); });
  await act(async () => { fireEvent.click(screen.getByTestId("chat-live")); });
  expect(ctl.cancel).toHaveBeenCalled();
  expect(screen.getByText("Hallo! Um wen geht es?")).toBeTruthy();
});

test("Sicherung: 8 s ohne Ton -> ganzer Text", async () => {
  fakeVoice();
  jest.useFakeTimers({ doNotFake: ["queueMicrotask"] });
  streamOnce("Hallo! Um wen geht es?");
  render(<Harness />);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  await act(async () => { fireEvent.click(screen.getByTestId("chat-start-btn")); });
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByTestId("chat-log").textContent).not.toMatch(/Um wen/);
  await act(async () => { jest.advanceTimersByTime(8600); });
  expect(screen.getByText("Hallo! Um wen geht es?")).toBeTruthy();
});

test("Stimme spricht nicht (speaks=false) -> Text sofort", async () => {
  fakeVoice({ speaks: false });
  await starten("Hallo! Um wen geht es?");
  await waitFor(() => expect(screen.getByText("Hallo! Um wen geht es?")).toBeTruthy());
});

test("Vorlesen aus -> Text sofort, kein sayStream", async () => {
  const ctl = fakeVoice();
  localStorage.setItem("st_interview_vorlesen", "0");
  await starten("Hallo! Um wen geht es?");
  await waitFor(() => expect(screen.getByText("Hallo! Um wen geht es?")).toBeTruthy());
  expect(ctl.opts).toBeNull();
});

test("Fragenliste: eine Zeile mit Stand und Thema, Liste erst nach Klick", async () => {
  fakeVoice({ speaks: false });
  await starten("Um wen geht es?");
  const toggle = screen.getByTestId("chat-fragen-toggle");
  expect(toggle.textContent).toMatch(/Fragen 0\/2/);
  await waitFor(() => expect(screen.getByTestId("chat-fragen-toggle").textContent).toMatch(/jetzt: Um wen geht es\?/));
  expect(screen.queryByTestId("chat-fragen-liste")).toBeNull();
  fireEvent.click(toggle);
  expect(screen.getByTestId("chat-fragen-liste").textContent).toMatch(/Worum ging es im Gespräch\?/);
  expect(screen.queryByTestId("chat-set")).toBeNull();                 // Verfahren nur vor dem Start
});

