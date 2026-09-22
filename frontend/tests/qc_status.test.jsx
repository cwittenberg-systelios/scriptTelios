/**
 * scriptTelios Frontend – v19.27 (D8): Status "alle n Checks bestanden" bei
 * leerer Issue-Liste und Liste fehlender Begriffe (code_detail.fehlend).
 */
import { render, screen } from "@testing-library/react";

jest.mock("../src/api.js", () => ({
  fetchRepairResult: jest.fn(), repairPreview: jest.fn(), repairStart: jest.fn(),
  apiFetch: jest.fn(), getApiBase: () => "http://test/api",
}));

import { QualityCheckPanel, QcFehlendDetail } from "../src/qa.jsx";

test("leere Issue-Liste zeigt Status mit Anzahl der Checks", () => {
  const { container } = render(<QualityCheckPanel
    data={{ summary: { info: 0, warning: 0, critical: 0, total: 0, checks_run: 34 }, issues: [] }}
    onToggle={() => {}} onHintChange={() => {}} />);
  expect(screen.getByText(/alle 34 Checks bestanden/)).toBeTruthy();
  expect(container.querySelector(".qc-panel.qc-ok")).toBeTruthy();
  // kein Repair-Formular ohne Beanstandungen
  expect(container.querySelector(".qc-repair-form")).toBeNull();
});

test("leere Issue-Liste ohne checks_run (Alt-Jobs)", () => {
  render(<QualityCheckPanel data={{ summary: { total: 0 }, issues: [] }} readOnly />);
  expect(screen.getByText(/keine Beanstandungen/)).toBeTruthy();
});

test("data null rendert nichts", () => {
  const { container } = render(<QualityCheckPanel data={null} />);
  expect(container.firstChild).toBeNull();
});

test("fehlende Begriffe werden unter dem Issue gelistet", () => {
  const data = {
    summary: { info: 0, warning: 1, critical: 1, total: 2, checks_run: 34 },
    issues: [
      { code: "MISSING_STICHPUNKT", severity: "warning",
        message: "Stichpunkt/Fokus-Thema nicht aufgegriffen: 'IRRT Traumasitzung'.",
        repair_hint: "", code_detail: { stichpunkt: "IRRT Traumasitzung", fehlend: ["irrt", "traumasitzung"] } },
      { code: "STICHPUNKTE_IGNORIERT", severity: "critical",
        message: "2 von 3 Zusatzangaben nicht aufgegriffen.",
        repair_hint: "", code_detail: { fehlend: ["IRRT Traumasitzung", "Schematherapie"], total: 3 } },
    ],
  };
  const { container } = render(<QualityCheckPanel data={data} readOnly />);
  expect(container.querySelectorAll(".qc-fehlend").length).toBe(2);
  expect(screen.getByText("traumasitzung")).toBeTruthy();
  expect(screen.getByText("Schematherapie")).toBeTruthy();
  expect(screen.getByText(/1 kritisch/)).toBeTruthy();
});

test("QcFehlendDetail ohne Liste rendert nichts", () => {
  const { container } = render(<QcFehlendDetail fehlend={undefined} />);
  expect(container.firstChild).toBeNull();
});
