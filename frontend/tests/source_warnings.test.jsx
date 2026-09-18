/**
 * scriptTelios Frontend – v19.25 Sprint Q: Quellen-Warnungen im JobProgressBar.
 *
 * SSE-Event {type:"source_warnings"} -> gelbe Box; Polling-Fallback liest
 * job.source_warnings; ohne Warnungen keine Box. EventSource wird gemockt.
 */
import { render, screen, act } from "@testing-library/react";

jest.mock("../src/api.js", () => ({
  apiFetch: jest.fn(),
  getApiBase: () => "http://test/api",
}));

import { apiFetch } from "../src/api.js";
import { JobProgressBar, SourceWarningsBox } from "../src/ui.jsx";

class FakeEventSource {
  constructor(url) { this.url = url; FakeEventSource.last = this; this.closed = false; }
  close() { this.closed = true; }
  emit(obj) { this.onmessage && this.onmessage({ data: JSON.stringify(obj) }); }
  fail() { this.onerror && this.onerror(); }
}

beforeEach(() => { global.EventSource = FakeEventSource; apiFetch.mockReset(); });

const WARN = [
  { code: "VERLAUF_UNPLAUSIBEL", severity: "warning", source: "Verlaufsdokumentation",
    message: "sieht nicht nach einer Verlaufsdokumentation aus" },
];

test("SourceWarningsBox: leer -> nichts, warning -> gelb mit Abbruch-Hinweis", () => {
  const { container, rerender } = render(<SourceWarningsBox warnings={[]} />);
  expect(container.querySelector(".source-warnings")).toBeNull();
  rerender(<SourceWarningsBox warnings={WARN} />);
  expect(screen.getByText(/Hinweis zu den Quellen/)).toBeTruthy();
  expect(screen.getByText(/Verlaufsdokumentation:/)).toBeTruthy();
  expect(screen.getByText(/Der Auftrag läuft weiter/)).toBeTruthy();
  rerender(<SourceWarningsBox warnings={[{ code: "STYLE_EXAMPLE_TOO_SHORT", severity: "info", message: "nur Überschriften" }]} />);
  expect(screen.queryByText(/Der Auftrag läuft weiter/)).toBeNull();
  expect(screen.getByText(/nur Überschriften/)).toBeTruthy();
});

test("JobProgressBar zeigt Warnungen aus dem SSE-Event", async () => {
  render(<JobProgressBar jobId="j1" onTerminal={() => {}} />);
  const es = FakeEventSource.last;
  expect(screen.queryByText(/Hinweis zu den Quellen/)).toBeNull();
  await act(async () => { es.emit({ type: "progress", progress: 10, phase: "Extraktion", detail: "" }); });
  await act(async () => { es.emit({ type: "source_warnings", warnings: WARN }); });
  expect(screen.getByText(/Hinweis zu den Quellen/)).toBeTruthy();
  await act(async () => { es.emit({ type: "progress", progress: 40, phase: "KI-Generierung", detail: "" }); });
  // Box bleibt nach weiteren Progress-Events stehen
  expect(screen.getByText(/sieht nicht nach einer Verlaufsdokumentation/)).toBeTruthy();
});

test("JobProgressBar Polling-Fallback liest job.source_warnings", async () => {
  jest.useFakeTimers();
  apiFetch.mockResolvedValue({ json: async () => ({ status: "running", progress: 20, progress_phase: "Extraktion", source_warnings: WARN }) });
  render(<JobProgressBar jobId="j2" onTerminal={() => {}} />);
  await act(async () => { FakeEventSource.last.fail(); });
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText(/Hinweis zu den Quellen/)).toBeTruthy();
  jest.useRealTimers();
});
