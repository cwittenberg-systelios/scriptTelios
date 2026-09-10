/**
 * scriptTelios Frontend – React-Tests fuer src/workflow-run.jsx (v19.21).
 *
 * Das Panel-Skelett (useWorkflowRun, WorkflowActionBar, KlientControls) war
 * bis S5 sechsmal inline in den Panels und nie getestet. Hier: Start -> Poll
 * -> Ergebnis, onResult/onError, Abbrechen, Resume-Banner, Doppel-Attach-
 * Schutz, Reset.
 *
 * api.js wird gemockt (startJob/pollJob/apiFetch); hooks.jsx nur fuer
 * useResumeWorkflowJob (Auto-Resume) - useJobResult laeuft echt.
 */
import { act, render, renderHook, screen, waitFor, fireEvent } from "@testing-library/react";

jest.mock("../src/api.js", () => ({
  apiFetch: jest.fn(() => Promise.resolve({ ok: true })),
  getApiBase: () => "http://api",
  pollJob: jest.fn(),
  startJob: jest.fn(),
}));
jest.mock("../src/hooks.jsx", () => {
  const actual = jest.requireActual("../src/hooks.jsx");
  return { ...actual, useResumeWorkflowJob: jest.fn() };
});

import { apiFetch, pollJob, startJob } from "../src/api.js";
import { useResumeWorkflowJob } from "../src/hooks.jsx";
import { useWorkflowRun, WorkflowActionBar, KlientControls } from "../src/workflow-run.jsx";

const JOB = { job_id: "j1", status: "done", result_text: "Fertiger Text, lang genug um nicht als leerer Output zu gelten. ".repeat(3), has_transcript: true };

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

describe("useWorkflowRun()", () => {
  test("start(): POST -> attach -> pollJob -> Ergebnis in out/lastJobId, onResult", async () => {
    startJob.mockResolvedValue("j1");
    pollJob.mockResolvedValue(JOB);
    const onResult = jest.fn();
    const { result } = renderHook(() => useWorkflowRun({ workflow: "anamnese", page: "p2", onResult }));

    await act(async () => { await result.current.start("PROMPT", "Text", { bullets: "b" }); });
    await waitFor(() => expect(result.current.busy).toBe(false));

    expect(startJob).toHaveBeenCalledWith("anamnese", "PROMPT", "Text", { bullets: "b" });
    expect(pollJob).toHaveBeenCalledWith("j1", expect.any(Number));
    expect(result.current.out).toBe(JOB.result_text);
    expect(result.current.displayText).toBe(JOB.result_text);
    expect(result.current.lastJobId).toBe("j1");
    expect(result.current.currentJobId).toBeNull();
    expect(onResult).toHaveBeenCalledWith(JOB);
    expect(result.current.outWarn).toBeNull();
  });

  test("Fehler beim Start: ohne onError landet die Meldung in out, mit onError im Callback", async () => {
    startJob.mockRejectedValue(new Error("Backend nicht erreichbar"));
    const { result } = renderHook(() => useWorkflowRun({ workflow: "verlaengerung", page: "p3" }));
    await act(async () => { await result.current.start("P", "", {}); });
    expect(result.current.out).toMatch(/^Fehler: /);
    expect(result.current.out).toMatch(/Backend nicht erreichbar/);
    expect(result.current.busy).toBe(false);

    const onError = jest.fn();
    const { result: r2 } = renderHook(() => useWorkflowRun({ workflow: "ism_fragebogen", page: "p6", onError }));
    await act(async () => { await r2.current.start("P", "", {}); });
    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/^Fehler: /), expect.any(Error));
    expect(r2.current.out).toBe("");
  });

  test("pollJob liefert null (abgebrochen): kein Ergebnis, busy zurueck auf false", async () => {
    startJob.mockResolvedValue("j2");
    pollJob.mockResolvedValue(null);
    const onResult = jest.fn();
    const { result } = renderHook(() => useWorkflowRun({ workflow: "anamnese", page: "p2", onResult }));
    await act(async () => { await result.current.start("P", "", {}); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(onResult).not.toHaveBeenCalled();
    expect(result.current.out).toBe("");
  });

  test("cancel(): DELETE auf den laufenden Job, Zustand zurueckgesetzt", async () => {
    startJob.mockResolvedValue("j3");
    pollJob.mockReturnValue(new Promise(() => {}));   // haengt (laufender Job)
    const { result } = renderHook(() => useWorkflowRun({ workflow: "anamnese", page: "p2" }));
    await act(async () => { await result.current.start("P", "", {}); });
    expect(result.current.busy).toBe(true);
    expect(result.current.currentJobId).toBe("j3");

    act(() => result.current.cancel());
    expect(apiFetch).toHaveBeenCalledWith("http://api/jobs/j3", { method: "DELETE" });
    expect(result.current.busy).toBe(false);
    expect(result.current.currentJobId).toBeNull();
  });

  test("Resume-Banner: resumeJob der eigenen Seite wird angehaengt, fremde Seite ignoriert", async () => {
    pollJob.mockResolvedValue(JOB);
    const onResumed = jest.fn();
    const { result, rerender } = renderHook(
      (props) => useWorkflowRun({ workflow: "anamnese", page: "p2", ...props }),
      { initialProps: { resumeJob: { page: "p3", jobId: "x" }, onResumed } },
    );
    expect(pollJob).not.toHaveBeenCalled();
    expect(useResumeWorkflowJob).toHaveBeenLastCalledWith("anamnese", expect.any(Function), false);

    rerender({ resumeJob: { page: "p2", jobId: "j9" }, onResumed });
    await waitFor(() => expect(result.current.lastJobId).toBe("j9"));
    expect(pollJob).toHaveBeenCalledTimes(1);
    expect(onResumed).toHaveBeenCalled();
  });

  test("Doppel-Attach-Schutz: dieselbe Job-ID wird nur einmal gepollt", async () => {
    pollJob.mockResolvedValue(JOB);
    renderHook(() => useWorkflowRun({ workflow: "anamnese", page: "p2" }));
    const attach = useResumeWorkflowJob.mock.calls[0][1];
    await act(async () => { attach("j5"); attach("j5"); });
    expect(pollJob).toHaveBeenCalledTimes(1);
  });

  test("reset(): Output, lastJobId und Job-Versionen leeren", async () => {
    startJob.mockResolvedValue("j1");
    pollJob.mockResolvedValue(JOB);
    const { result } = renderHook(() => useWorkflowRun({ workflow: "anamnese", page: "p2" }));
    await act(async () => { await result.current.start("P", "", {}); });
    await waitFor(() => expect(result.current.out).toBe(JOB.result_text));
    act(() => result.current.reset());
    expect(result.current.out).toBe("");
    expect(result.current.lastJobId).toBeNull();
    expect(result.current.job.hasRepair).toBe(false);
  });
});

describe("<WorkflowActionBar>", () => {
  test("zeigt Start-Button (disabled + title) bzw. Abbrechen waehrend busy", () => {
    const onRun = jest.fn(), onCancel = jest.fn();
    const { rerender } = render(
      <WorkflowActionBar busy={false} onRun={onRun} onCancel={onCancel} runLabel="Los" disabled title="Fehlt" />,
    );
    const btn = screen.getByRole("button", { name: "Los" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "Fehlt");

    rerender(<WorkflowActionBar busy={false} onRun={onRun} onCancel={onCancel} runLabel="Los" />);
    fireEvent.click(screen.getByRole("button", { name: "Los" }));
    expect(onRun).toHaveBeenCalledTimes(1);

    rerender(<WorkflowActionBar busy onRun={onRun} onCancel={onCancel} runLabel="Los" />);
    fireEvent.click(screen.getByRole("button", { name: /Abbrechen/ }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Los" })).toBeNull();
  });

  test("children werden links gerendert", () => {
    render(<WorkflowActionBar busy={false} onRun={() => {}} onCancel={() => {}} runLabel="Los"><span>LINKS</span></WorkflowActionBar>);
    expect(screen.getByText("LINKS")).toBeInTheDocument();
  });
});

describe("<KlientControls>", () => {
  test("Geschlecht-Toggle und Kuerzel-Eingabe rufen die Setter", () => {
    const onG = jest.fn(), onK = jest.fn();
    render(<KlientControls geschlecht="w" onGeschlecht={onG} kuerzel="K" onKuerzel={onK} />);
    fireEvent.click(screen.getByRole("button", { name: /männlich/ }));
    expect(onG).toHaveBeenCalledWith("m");
    fireEvent.change(screen.getByPlaceholderText("K."), { target: { value: "Mü" } });
    expect(onK).toHaveBeenCalledWith("Mü");
  });
});
