/**
 * scriptTelios Frontend – Tests v19.35 (Server-Vorlesen zum Testen):
 *  - speech.js Server-Provider: Vorab-Abruf, Reihenfolge, Abbruch, Fallback
 *  - Umschalter: Server-Stimme oder stumm (v19.39)
 *  - TtsSelect: nur Chatterbox-Stimmen, Default Gunther, Wahl im localStorage
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

jest.mock("../src/api.js", () => ({
  fetchTtsEngines: jest.fn(),
  interviewTts: jest.fn(),
}));

import { fetchTtsEngines } from "../src/api.js";
import {
  createServerProvider, createSwitchingProvider, setTtsEngine, getTtsEngine, setActiveTtsEngine, getStoredTtsEngine,
  _setLastSpokeAt, TTS_FALLBACK_EVENT, LS_TTS_ENGINE,
} from "../src/speech.js";
import { TtsSelect, chooseEngine, _resetTtsEngines } from "../src/tts-select.jsx";

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
  setTtsEngine("");
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

describe("Umschalter (v19.39: Server, Browser als letzte Option, sonst stumm)", () => {
  test("waehlt nach aktiver Stimme", () => {
    const mk = (name, vs = "none") => ({ name, available: () => true, say: jest.fn(), sayStream: jest.fn(() => name), cancel: jest.fn(), voiceStatus: () => vs });
    const b = mk("browser", "ok"); const s = mk("server");
    const sw = createSwitchingProvider(b, s);
    setActiveTtsEngine("");
    expect(sw.sayStream()).not.toBe("browser");
    expect(sw.sayStream()).not.toBe("server");
    expect(sw.voiceStatus()).toBe("none");
    setActiveTtsEngine("browser");
    expect(sw.sayStream()).toBe("browser");
    expect(sw.voiceStatus()).toBe("ok");
    setActiveTtsEngine("chatterbox:gunther");
    expect(sw.sayStream()).toBe("server");
    sw.cancel();
    expect(b.cancel).toHaveBeenCalled(); expect(s.cancel).toHaveBeenCalled();
  });

  test("setTtsEngine speichert, setActiveTtsEngine nicht", () => {
    setTtsEngine("chatterbox:carsten");
    expect(localStorage.getItem(LS_TTS_ENGINE)).toBe("chatterbox:carsten");
    setActiveTtsEngine("chatterbox:gunther");
    expect(getTtsEngine()).toBe("chatterbox:gunther");
    expect(getStoredTtsEngine()).toBe("chatterbox:carsten");
    expect(localStorage.getItem(LS_TTS_ENGINE)).toBe("chatterbox:carsten");
  });
});

describe("TtsSelect (v19.39)", () => {
  const V = (key, label, available = true) => ({ key, label, available, reason: available ? "" : "Referenz fehlt" });
  const DATA = { default: "chatterbox:gunther", reason: "", engines: [
    V("chatterbox:gunther", "Gunther Schmidt"), V("chatterbox:carsten", "Carsten"), V("chatterbox:charlotte", "Charlotte", false),
  ] };

  test("chooseEngine: Nutzerwahl > Default > erste verfuegbare", () => {
    expect(chooseEngine(DATA, "chatterbox:carsten")).toBe("chatterbox:carsten");
    expect(chooseEngine(DATA, "chatterbox:charlotte")).toBe("chatterbox:gunther");   // Wahl nicht verfuegbar
    expect(chooseEngine(DATA, "browser")).toBe("chatterbox:gunther");                 // Altlast
    expect(chooseEngine({ ...DATA, default: "chatterbox:weg" }, "")).toBe("chatterbox:gunther");
    expect(chooseEngine({ engines: [], default: "chatterbox:gunther" }, "")).toBe("");
  });

  test("ohne gespeicherte Wahl: Gunther aktiv, nichts gespeichert; Wahl wird gespeichert", async () => {
    fetchTtsEngines.mockResolvedValue(DATA);
    const onChange = jest.fn();
    render(<TtsSelect onChange={onChange} />);
    const sel = await screen.findByTestId("tts-engine");
    expect(sel.value).toBe("chatterbox:gunther");
    expect(getTtsEngine()).toBe("chatterbox:gunther");
    expect(localStorage.getItem(LS_TTS_ENGINE)).toBe("");
    expect([...sel.querySelectorAll("option")].map(o => o.value)).toEqual(["chatterbox:gunther", "chatterbox:carsten", "chatterbox:charlotte"]);
    expect(sel.querySelector('option[value="chatterbox:charlotte"]').disabled).toBe(true);
    fireEvent.change(sel, { target: { value: "chatterbox:carsten" } });
    expect(localStorage.getItem(LS_TTS_ENGINE)).toBe("chatterbox:carsten");
    expect(onChange).toHaveBeenCalledWith("chatterbox:carsten");
  });

  test("gespeicherte Wahl bleibt nach Neuladen", async () => {
    setTtsEngine("chatterbox:carsten");
    fetchTtsEngines.mockResolvedValue(DATA);
    render(<TtsSelect />);
    expect((await screen.findByTestId("tts-engine")).value).toBe("chatterbox:carsten");
  });

  test("gespeicherte Wahl gerade nicht verfuegbar: Default wirkt, Wahl bleibt gespeichert", async () => {
    setTtsEngine("chatterbox:charlotte");
    fetchTtsEngines.mockResolvedValue(DATA);
    render(<TtsSelect />);
    expect((await screen.findByTestId("tts-engine")).value).toBe("chatterbox:gunther");
    expect(localStorage.getItem(LS_TTS_ENGINE)).toBe("chatterbox:charlotte");
  });

  test("keine Stimme verfuegbar: Hinweis statt Auswahl, kein Vorlesen", async () => {
    fetchTtsEngines.mockResolvedValue({ engines: [], default: "chatterbox:gunther", reason: "Vorlese-Dienst nicht erreichbar" });
    render(<TtsSelect />);
    expect((await screen.findByTestId("tts-none")).textContent).toMatch(/nicht erreichbar/);
    expect(getTtsEngine()).toBe("");
  });

  test("Fehler beim Vorlesen: Hinweis", async () => {
    fetchTtsEngines.mockResolvedValue(DATA);
    render(<TtsSelect />);
    await screen.findByTestId("tts-engine");
    act(() => { window.dispatchEvent(new CustomEvent(TTS_FALLBACK_EVENT, { detail: { message: "Vorlesen gerade nicht möglich" } })); });
    expect(screen.getByTestId("tts-fallback").textContent).toBe("Vorlesen gerade nicht möglich");
  });
});

describe("TtsSelect v19.37.2 – unvollstaendige Liste wird nicht gemerkt", () => {
  const OFF = { default: "chatterbox:gunther", reason: "", engines: [
    { key: "chatterbox:gunther", label: "Gunther Schmidt", available: false, reason: "Referenz fehlt" },
    { key: "chatterbox:carsten", label: "Carsten", available: true, reason: "" },
  ] };
  const ON = { default: "chatterbox:gunther", reason: "", engines: [
    { key: "chatterbox:gunther", label: "Gunther Schmidt", available: true, reason: "" },
    { key: "chatterbox:carsten", label: "Carsten", available: true, reason: "" },
  ] };
  beforeEach(() => { fetchTtsEngines.mockReset(); _resetTtsEngines(); });

  test("beim Oeffnen der Auswahl wird neu gefragt, danach verfuegbar", async () => {
    fetchTtsEngines.mockResolvedValueOnce(OFF).mockResolvedValue(ON);
    render(<TtsSelect />);
    const sel = await screen.findByTestId("tts-engine");
    expect(sel.querySelector('option[value="chatterbox:gunther"]').disabled).toBe(true);
    await act(async () => { fireEvent.mouseDown(sel); });
    expect(sel.querySelector('option[value="chatterbox:gunther"]').disabled).toBe(false);
    expect(fetchTtsEngines).toHaveBeenCalledTimes(2);
  });

  test("nach 'Server laeuft' (st-health-ok) wird neu gefragt", async () => {
    fetchTtsEngines.mockResolvedValueOnce(OFF).mockResolvedValue(ON);
    render(<TtsSelect />);
    const sel = await screen.findByTestId("tts-engine");
    await act(async () => { window.dispatchEvent(new Event("st-health-ok")); });
    expect(sel.querySelector('option[value="chatterbox:gunther"]').disabled).toBe(false);
  });

  test("vollstaendige Liste wird gemerkt (kein erneuter Abruf beim naechsten Mount)", async () => {
    fetchTtsEngines.mockResolvedValue(ON);
    const { unmount } = render(<TtsSelect />);
    await screen.findByTestId("tts-engine");
    unmount();
    render(<TtsSelect />);
    await screen.findByTestId("tts-engine");
    expect(fetchTtsEngines).toHaveBeenCalledTimes(1);
  });
});

describe("TtsSelect – Browser als letzte Option (v19.39)", () => {
  const V = (key, label, available = true) => ({ key, label, available, reason: "" });
  beforeEach(() => {
    fetchTtsEngines.mockReset(); _resetTtsEngines();
    window.speechSynthesis = { getVoices: () => [{ name: "Anna", lang: "de-DE", localService: true }], speak: jest.fn(), cancel: jest.fn() };
    global.SpeechSynthesisUtterance = function (t) { this.text = t; };
  });
  afterEach(() => { delete window.speechSynthesis; delete global.SpeechSynthesisUtterance; });

  test("Browser steht als letzte Option in der Liste, Default bleibt Gunther", async () => {
    fetchTtsEngines.mockResolvedValue({ default: "chatterbox:gunther", reason: "", engines: [V("chatterbox:gunther", "Gunther Schmidt"), V("chatterbox:carsten", "Carsten")] });
    render(<TtsSelect />);
    const sel = await screen.findByTestId("tts-engine");
    expect([...sel.querySelectorAll("option")].map(o => o.value)).toEqual(["chatterbox:gunther", "chatterbox:carsten", "browser"]);
    expect(sel.value).toBe("chatterbox:gunther");
    fireEvent.change(sel, { target: { value: "browser" } });
    expect(getTtsEngine()).toBe("browser");
    expect(localStorage.getItem(LS_TTS_ENGINE)).toBe("browser");
  });

  test("ohne Server-Stimmen: Browser wirkt, Grund wird angezeigt, Wahl bleibt", async () => {
    setTtsEngine("chatterbox:carsten");
    fetchTtsEngines.mockResolvedValue({ engines: [], default: "chatterbox:gunther", reason: "Vorlese-Dienst nicht erreichbar" });
    render(<TtsSelect />);
    const sel = await screen.findByTestId("tts-engine");
    expect(sel.value).toBe("browser");
    expect(screen.getByTestId("tts-server-reason").textContent).toMatch(/nicht erreichbar/);
    expect(localStorage.getItem(LS_TTS_ENGINE)).toBe("chatterbox:carsten");
  });

  test("Server-Fehler beim Vorlesen: Browser-Stimme uebernimmt den Satz", async () => {
    const fallback = { say: jest.fn(async () => true), cancel: jest.fn() };
    const sp = createServerProvider({ fetchAudio: () => Promise.reject(new Error("weg")), fallback, engine: () => "chatterbox:gunther" });
    await act(async () => { await sp.say(["Danke."]); });
    expect(fallback.say).toHaveBeenCalledWith(["Danke."]);
  });
});
