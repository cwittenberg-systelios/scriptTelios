/**
 * scriptTelios Frontend – Tests v19.33 (Dialog-Latenz):
 *  - speech.js: nur lokale Stimmen, Stimmen-Status, Aufwecken der Audioausgabe
 *  - api.js: warmupInterviewServer stoesst /interview/warmup an
 *  - interview-chat.jsx: client_perf im naechsten Turn, Hinweis ohne lokale Stimme
 */
import { useState } from "react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";

import {
  isLocalVoice, pickGermanVoice, voiceStatus, wakeAudio, _setLastSpokeAt,
  getSpeechProvider, _setSpeechProvider, LEAD_GAP_MS, WAKE_SILENCE_MS,
} from "../src/speech.js";

function voice(name, lang = "de-DE", localService = true) { return { name, lang, localService }; }
function installVoices(list) {
  window.speechSynthesis = { getVoices: () => list, speak: jest.fn((u) => setTimeout(() => u.onend && u.onend(), 0)), cancel: jest.fn() };
  global.SpeechSynthesisUtterance = function (t) { this.text = t; };
}

afterEach(() => {
  delete window.speechSynthesis;
  delete global.SpeechSynthesisUtterance;
  delete window.AudioContext;
  _setSpeechProvider(null);
});

describe("speech.js – nur lokale Stimmen", () => {
  test("Cloud-Stimmen werden ausgeschlossen", () => {
    expect(isLocalVoice(voice("Google Deutsch", "de-DE", false))).toBe(false);
    expect(isLocalVoice(voice("Microsoft Katja Online (Natural) - German (Germany)", "de-DE", false))).toBe(false);
    expect(isLocalVoice(voice("Microsoft Hedda - German (Germany)"))).toBe(true);
    expect(isLocalVoice(voice("Anna"))).toBe(true);
  });

  test("pickGermanVoice nimmt die lokale, auch wenn eine Cloud-Stimme zuerst kommt", () => {
    installVoices([voice("Google Deutsch", "de-DE", false), voice("Microsoft Hedda - German (Germany)")]);
    expect(pickGermanVoice().name).toMatch(/Hedda/);
    expect(voiceStatus()).toBe("ok");
  });

  test("nur Cloud-Stimmen -> Status none, es wird nichts gesprochen", async () => {
    installVoices([voice("Google Deutsch", "de-DE", false), voice("Samantha", "en-US")]);
    expect(voiceStatus()).toBe("none");
    const p = getSpeechProvider();
    expect(await p.say(["Hallo."])).toBe(false);
    expect(window.speechSynthesis.speak).not.toHaveBeenCalled();
  });

  test("leere Stimmenliste -> loading", () => {
    installVoices([]);
    expect(voiceStatus()).toBe("loading");
  });

  test("mit lokaler Stimme wird gesprochen", async () => {
    installVoices([voice("Anna")]);
    _setLastSpokeAt(Date.now());
    const p = getSpeechProvider();
    expect(await p.say(["Hallo du."])).toBe(true);
    expect(window.speechSynthesis.speak).toHaveBeenCalledTimes(1);
  });
});

describe("speech.js – Audioausgabe aufwecken", () => {
  test("nach Pause: Stille ueber WebAudio", async () => {
    const started = jest.fn();
    window.AudioContext = function () {
      this.state = "running"; this.sampleRate = 8000; this.destination = {};
      this.createBuffer = () => ({});
      this.createBufferSource = () => ({ connect: () => {}, start: started });
    };
    _setLastSpokeAt(0);
    const t0 = Date.now();
    expect(await wakeAudio()).toBe(true);
    expect(started).toHaveBeenCalled();
    expect(Date.now() - t0).toBeGreaterThanOrEqual(WAKE_SILENCE_MS - 20);
  });

  test("direkt nach dem letzten Satz: nur kurze Pause", async () => {
    _setLastSpokeAt(Date.now());
    const t0 = Date.now();
    expect(await wakeAudio()).toBe(false);
    const dt = Date.now() - t0;
    expect(dt).toBeGreaterThanOrEqual(LEAD_GAP_MS - 20);
    expect(dt).toBeLessThan(WAKE_SILENCE_MS);
  });
});

describe("api.js – Interview-Warmup", () => {
  test("warmupInterviewServer ohne Proxy stoesst /interview/warmup an", async () => {
    const { warmupInterviewServer } = await import("../src/api.js");
    const fn = jest.fn(() => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) }));
    window.signedFetch = fn;
    try {
      const st = await warmupInterviewServer();
      expect(st.status).toBe("no_proxy");
      const call = fn.mock.calls.find(c => String(c[0]).endsWith("/interview/warmup"));
      expect(call).toBeTruthy();
      expect(call[1].method).toBe("POST");
    } finally { delete window.signedFetch; }
  });
});

// ── InterviewChat: client_perf + Stimmen-Hinweis ─────────────────────────────

jest.mock("../src/api.js", () => {
  const actual = jest.requireActual("../src/api.js");
  return {
    ...actual,
    fetchInterviewSets: jest.fn(),
    interviewTranscribe: jest.fn(),
    interviewChatStream: jest.fn(),
    warmupInterviewServer: jest.fn(actual.warmupInterviewServer),
  };
});

const MANIFEST = {
  default_set: "kunst", abschnitte: {},
  sets: [{ key: "kunst", label: "Kunst", beschreibung: "", fragen: [
    { key: "klient", text: "Um wen geht es?", ziel_abschnitt: "meta", pflicht: true, pflichtaspekte: [], hinweis: "" },
    { key: "selbstgefaehrdung", text: "Hinweise auf Selbstgefährdung?", ziel_abschnitt: "schluss", pflicht: true, pflichtaspekte: [], hinweis: "" },
  ] }],
};

describe("InterviewChat – Messung und Stimmen-Hinweis", () => {
  let api;
  let chat;
  beforeEach(async () => {
    api = await import("../src/api.js");
    chat = await import("../src/interview-chat.jsx");
    api.fetchInterviewSets.mockResolvedValue(MANIFEST);
    api.warmupInterviewServer.mockImplementation(() => Promise.resolve({ status: "ok" }));
    api.interviewChatStream.mockImplementation(async (payload, onEvent) => {
      onEvent({ type: "delta", text: "Frage?" });
      const m = { type: "meta", sage: "Frage?", thema: "klient", checkliste: {}, rueckfragen: {}, trigger_stufe: 0, klient: null, fertig: false };
      onEvent(m); onEvent({ type: "done" });
      return m;
    });
  });

  function Harness() {
    const [v, setV] = useState({ ...chat.CHAT_DEFAULT });
    return <chat.InterviewChat value={v} onChange={setV} toast={() => {}} model={null} />;
  }

  function provider(status) {
    _setSpeechProvider({
      name: "test", available: () => true, say: async () => true, cancel: () => {}, voiceStatus: () => status,
      sayStream: () => ({ push: () => {}, end: async () => true, done: Promise.resolve(true) }),
    });
  }

  test("zweiter Turn traegt die Zeiten des ersten als client_perf", async () => {
    provider("ok");
    render(<Harness />);
    fireEvent.click(await screen.findByTestId("chat-start-btn"));
    await screen.findByTestId("chat-antwort");
    expect(api.interviewChatStream.mock.calls[0][0].client_perf).toBeNull();
    fireEvent.change(screen.getByTestId("chat-antwort"), { target: { value: "Um Frau L." } });
    await act(async () => { fireEvent.click(screen.getByTestId("chat-senden")); });
    await waitFor(() => expect(api.interviewChatStream).toHaveBeenCalledTimes(2));
    const cp = api.interviewChatStream.mock.calls[1][0].client_perf;
    expect(typeof cp.prev_total_ms).toBe("number");
    expect(typeof cp.prev_ttft_ms).toBe("number");
    expect(screen.queryByTestId("chat-voice-hint")).toBeNull();
  });

  test("ohne lokale deutsche Stimme erscheint der Hinweis", async () => {
    provider("none");
    render(<Harness />);
    fireEvent.click(await screen.findByTestId("chat-start-btn"));
    expect(await screen.findByTestId("chat-voice-hint")).toBeTruthy();
  });
});
