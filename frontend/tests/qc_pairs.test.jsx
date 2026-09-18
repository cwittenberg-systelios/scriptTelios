/**
 * scriptTelios Frontend – v19.26b: Vorher/Nachher im QualityCheckPanel
 * (code_detail.pairs bei DIAGNOSE_ENTFERNT / GRAMMAR_AUTOFIXED).
 */
import { render, screen, fireEvent } from "@testing-library/react";

jest.mock("../src/api.js", () => ({
  fetchRepairResult: jest.fn(), repairPreview: jest.fn(), repairStart: jest.fn(),
  apiFetch: jest.fn(), getApiBase: () => "http://test/api",
}));

import { QualityCheckPanel, QcPairsDetail } from "../src/qa.jsx";

const DATA = {
  summary: { info: 1, warning: 0, critical: 0, total: 1 },
  issues: [{
    code: "DIAGNOSE_ENTFERNT", severity: "info",
    message: "1 Satz/Saetze automatisch ohne Diagnosebezeichnung umformuliert.",
    repair_hint: "",
    code_detail: { replaced: 1, kept: 1, mode: "llm", pairs: [
      { before: "Hinzu kommen Flashbacks und Grübeln im Rahmen einer PTBS.", after: "Hinzu kommen Flashbacks und ein starkes Grübeln.", how: "llm" },
      { before: "Aufgrund ihrer Angststörung vermeide sie Menschen.", after: null, how: "llm_abgelehnt:laenge_2_von_7" },
    ] },
  }],
};

test("Panel zeigt aufklappbares Vorher/Nachher", () => {
  render(<QualityCheckPanel data={DATA} readOnly />);
  const summary = screen.getByText(/Vorher\/Nachher anzeigen \(2\)/);
  fireEvent.click(summary);
  expect(screen.getByText(/im Rahmen einer PTBS/)).toBeTruthy();
  expect(screen.getByText(/Hinzu kommen Flashbacks und ein starkes Grübeln\./)).toBeTruthy();
  expect(screen.getByText(/Modell$/)).toBeTruthy();
  expect(screen.getByText(/Umformulierung nicht übernommen/)).toBeTruthy();
  expect(screen.getByText(/Modellvorschlag abgelehnt/)).toBeTruthy();
});

test("ohne pairs keine Details", () => {
  const { container } = render(<QcPairsDetail pairs={undefined} />);
  expect(container.querySelector(".qc-pairs")).toBeNull();
  const { container: c2 } = render(<QcPairsDetail pairs={[]} />);
  expect(c2.querySelector(".qc-pairs")).toBeNull();
});
