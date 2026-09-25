/**
 * scriptTelios Frontend – Tests v19.42: P1 im Gespraechsmodus ohne
 * Stichpunkte/Stilvorlage/Prompt, Aktionsleiste erst nach dem Abschluss;
 * Fragenkarten unveraendert.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

jest.mock("../src/api.js", () => {
  const actual = jest.requireActual("../src/api.js");
  return {
    ...actual,
    apiFetch: jest.fn(() => Promise.resolve({ ok: true, json: async () => [] })),
    getApiBase: () => "http://api",
    startJob: jest.fn(),
  };
});
jest.mock("../src/interview-chat.jsx", () => {
  const actual = jest.requireActual("../src/interview-chat.jsx");
  return {
    ...actual,
    InterviewChat: ({ value, onChange }) => (
      <button type="button" data-testid="stub-fertig" onClick={() => onChange({ ...value, phase: "fertig" })}>fertig</button>
    ),
  };
});
jest.mock("../src/interview.jsx", () => {
  const actual = jest.requireActual("../src/interview.jsx");
  return { ...actual, InterviewDialog: () => <div data-testid="stub-karten" /> };
});
jest.mock("../src/ui.jsx", () => {
  const actual = jest.requireActual("../src/ui.jsx");
  return { ...actual, JobModelPicker: () => null, FeedbackButton: () => null };
});

import { P1 } from "../src/panels/P1.jsx";

beforeEach(() => { localStorage.clear(); });

async function oeffnen(modus) {
  localStorage.setItem("st_interview_modus", modus);
  render(<P1 toast={() => {}} />);
  await act(async () => { await Promise.resolve(); });
  await act(async () => { fireEvent.click(screen.getByTestId("p1-tile-interview")); });
}

test("Gespraech: keine Stichpunkte/Stil/Prompt, kein FORM-Text, Leiste erst nach Abschluss", async () => {
  await oeffnen("chat");
  expect(screen.queryByText("Stichpunkte")).toBeNull();
  expect(screen.queryByText(/Stilvorlage/)).toBeNull();
  expect(screen.queryByText(/Prompt\/Modell/)).toBeNull();
  expect(screen.queryByText(/Das System führt das Gespräch/)).toBeNull();
  expect(screen.queryByText("Form")).toBeNull();
  expect(screen.queryByTestId("p1-action-bar")).toBeNull();
  await act(async () => { fireEvent.click(screen.getByTestId("stub-fertig")); });
  expect(screen.getByTestId("p1-action-bar").textContent).toMatch(/Verlaufsnotiz generieren/);
});

test("Fragenkarten: unveraendert mit Stichpunkten und Aktionsleiste", async () => {
  await oeffnen("fragen");
  expect(screen.getByTestId("stub-karten")).toBeTruthy();
  expect(screen.getByText("Stichpunkte")).toBeTruthy();
  expect(screen.getByTestId("p1-action-bar")).toBeTruthy();
});
