/**
 * scriptTelios Frontend – Tests v19.35 (Server-Vorlesen zum Testen):
 *  - speech.js Server-Provider: Vorab-Abruf, Reihenfolge, Abbruch, Fallback
 *  - Umschalter browser/server
 *  - TtsSelect: Liste, ausgegraute Engines, Rueckfall auf Browser, Hinweis
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

jest.mock("../src/api.js", () => ({
  fetchTtsEngines: jest.fn(),
  interviewTts: jest.fn(),
}));

import { fetchTtsEngines } from "../src/api.js";
import {
  createServerProvider, createSwitchingProvider, setTtsEngine, getTtsEngine,
  _setLastSpokeAt, TTS_FALLBACK_EVENT, LS_TTS_ENGINE,
} from "../src/speech.js";
import { TtsSelect, _resetTtsEngines } from "../src/tts-select.jsx";

// <audio>-Attrappe: spielt sofort "zu Ende", merkt sich die Reihenfolge
let played;
beforeEach(() => {
  played = [];
  _setLastSpokeAt(Date.now());
  global.URL.createObjectURL = jest.fn((b) => `blob:${b.text}`);
  global.URL.revokeObjectURL = jest.fn();
  global.Audio = function (url) {
    this.url = url;
    this.pause = jest.fn();
    this.play = () => { played.push(url.replace("blob:", "")); setTimeout(() => this.onended && this.onended(), 0); return Promise.resolve(); };
  };
  localStorage.clear();
  setTtsEngine("browser");
  _resetTtsEngines();
});

function deferred() { let resolve; const p = new Promise(r => { resolve = r; }); return { p, resolve }; }

describe("Server-Provider", () => {
  test("holt jeden Satz sofort ab und spielt in Reihenfolge", async () => {
    const pending = {};
    const fetchAudio = jest.fn((text) => { pending[text] = deferred(); return pending[text].p; });
    const sp = createServerProvider({ fetchAudio, engine: () => "piper" });
    const s = sp.sayStream();
    s.push("Wie ging es ihr? Und ");
    s.push("danach? Gut.");
    const done = s.end();
    expect(fetchAudio.mock.calls.map(c => c[0])).toEqual(["Wie ging es ihr?", "Und danach?", "Gut."]);
    expect(fetchAudio.mock.calls[0][1]).toBe("piper");
    // zweiter Satz kommt zuerst zurueck - gespielt wird trotzdem in Reihenfolge
    pending["Und danach?"].resolve({ text: "Und danach?" });
    pending["Gut."].resolve({ text: "Gut." });
    pending["Wie ging es ihr?"].resolve({ text: "Wie ging es ihr?" });
    await act(async () => { await done; });
    expect(played).toEqual(["Wie ging es ihr?", "Und danach?", "Gut."]);
  });

  test("cancel bricht laufende Abrufe ab", async () => {
    const signals = [];
    const fetchAudio = jest.fn((text, eng, { signal }) => { signals.push(signal); return new Promise(() => {}); });
    const sp = createServerProvider({ fetchAudio, engine: () => "piper" });
    const s = sp.sayStream();
    s.push("Eins. Zwei. ");
    sp.cancel();
    expect(signals.length).toBe(2);
    expect(signals.every(sig => sig.aborted)).toBe(true);
  });

  test("Fehler: Browser-Stimme uebernimmt, Hinweis-Event", async () => {
    const fallback = { say: jest.fn(async () => true), cancel: jest.fn() };
    const fetchAudio = jest.fn(() => Promise.reject(new Error("Vorlese-Dienst nicht erreichbar.")));
    const events = [];
    const onFb = (e) => events.push(e.detail.message);
    window.addEventListener(TTS_FALLBACK_EVENT, onFb);
    try {
      const sp = createServerProvider({ fetchAudio, fallback, engine: () => "piper" });
      await act(async () => { await sp.say(["Danke."]); });
      expect(fallback.say).toHaveBeenCalledWith(["Danke."]);
      expect(events[0]).toMatch(/nicht erreichbar/);
    } finally { window.removeEventListener(TTS_FALLBACK_EVENT, onFb); }
  });
});

describe("Umschalter", () => {
  test("waehlt je nach Engine browser oder server", () => {
    const mk = (name) => ({ name, available: () => true, say: jest.fn(), sayStream: jest.fn(() => name), cancel: jest.fn(), voiceStatus: () => "none" });
    const b = mk("browser"); const s = mk("server");
    const sw = createSwitchingProvider(b, s);
    expect(sw.sayStream()).toBe("browser");
    expect(sw.voiceStatus()).toBe("none");
    setTtsEngine("piper");
    expect(getTtsEngine()).toBe("piper");
    expect(localStorage.getItem(LS_TTS_ENGINE)).toBe("piper");
    expect(sw.sayStream()).toBe("server");
    expect(sw.voiceStatus()).toBe("ok");
    sw.cancel();
    expect(b.cancel).toHaveBeenCalled(); expect(s.cancel).toHaveBeenCalled();
  });
});

describe("TtsSelect", () => {
  const ENGINES = [
    { key: "browser", label: "Browser", available: true, reason: "" },
    { key: "piper", label: "Piper (Thorsten)", available: true, reason: "" },
    { key: "chatterbox", label: "Chatterbox (CPU, Test)", available: false, reason: "abgeschaltet" },
  ];

  test("zeigt Engines, sperrt nicht verfuegbare, speichert Wahl", async () => {
    fetchTtsEngines.mockResolvedValue(ENGINES);
    const onChange = jest.fn();
    render(<TtsSelect onChange={onChange} />);
    const sel = await screen.findByTestId("tts-engine");
    const opts = [...sel.querySelectorAll("option")];
    expect(opts.map(o => o.value)).toEqual(["browser", "piper", "chatterbox"]);
    expect(opts[2].disabled).toBe(true);
    fireEvent.change(sel, { target: { value: "piper" } });
    expect(getTtsEngine()).toBe("piper");
    expect(onChange).toHaveBeenCalledWith("piper");
  });

  test("gespeicherte, nicht mehr verfuegbare Wahl faellt auf Browser zurueck", async () => {
    setTtsEngine("chatterbox");
    fetchTtsEngines.mockResolvedValue(ENGINES);
    render(<TtsSelect />);
    const sel = await screen.findByTestId("tts-engine");
    expect(sel.value).toBe("browser");
    expect(getTtsEngine()).toBe("browser");
  });

  test("nur Browser: keine Auswahl; Fallback-Hinweis wird angezeigt", async () => {
    fetchTtsEngines.mockResolvedValue([{ key: "browser", label: "Browser", available: true }]);
    const { container } = render(<TtsSelect />);
    await act(async () => { await Promise.resolve(); });
    expect(container.innerHTML).toBe("");
    _resetTtsEngines();
    fetchTtsEngines.mockResolvedValue(ENGINES);
    render(<TtsSelect />);
    await screen.findByTestId("tts-engine");
    act(() => { window.dispatchEvent(new CustomEvent(TTS_FALLBACK_EVENT, { detail: { message: "Server-Stimme weg" } })); });
    expect(screen.getByTestId("tts-fallback").textContent).toBe("Server-Stimme weg");
  });
});
