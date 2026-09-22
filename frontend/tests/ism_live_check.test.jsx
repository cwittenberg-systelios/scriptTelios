/**
 * scriptTelios Frontend – v19.29: Live-QC fuer den ISM-Fragebogen.
 * useIsmLiveCheck (POST /ism/check, Abbruch, Fehler-Toleranz), Helfer
 * issuesForItem/exportNeedsConfirm, IsmItemEditor-Markierung + Blur-Trigger,
 * QualityCheckPanel.onFocusItem.
 */
import { jest } from "@jest/globals";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";

const mockApiFetch = jest.fn();
jest.mock("../src/api.js", () => ({
  apiFetch: (...a) => mockApiFetch(...a), getApiBase: () => "http://test/api",
  fetchRepairResult: jest.fn(), repairPreview: jest.fn(), repairStart: jest.fn(),
}));
const apiFetch = mockApiFetch;

import { useIsmLiveCheck, issuesForItem, exportNeedsConfirm, confirmExportText } from "../src/ism-check.jsx";
import { QualityCheckPanel } from "../src/qa.jsx";
import { IsmItemEditor } from "../src/panels/P6.jsx";

const ISM = { begruessung: "Hallo", verabschiedung: "Tschüss",
  items: [{ faktor_id: 0, frage: "Heute konnte ich mich abgrenzen.", pol_min: "a", pol_max: "b" }] };
const QC_WARN = {
  version: 1, workflow: "ism_fragebogen",
  summary: { critical: 0, warning: 1, info: 0, total: 1, checks_run: 7 },
  issues: [{ code: "ISM_POLE_IDENTISCH", severity: "warning",
    message: "Item 1: min- und max-Label sind identisch.", repair_hint: "",
    code_detail: { item_index: 0, frage: "x" } }],
};
const QC_OK = { version: 1, workflow: "ism_fragebogen",
  summary: { critical: 0, warning: 0, info: 0, total: 0, checks_run: 7 }, issues: [] };

function okResponse(data) { return { ok: true, json: async () => data }; }

beforeEach(() => apiFetch.mockReset());

test("check() postet den Fragebogen und setzt qc", async () => {
  apiFetch.mockResolvedValue(okResponse(QC_WARN));
  const { result } = renderHook(() => useIsmLiveCheck(null));
  await act(async () => { await result.current.check(ISM); });
  expect(apiFetch).toHaveBeenCalledTimes(1);
  const [url, opts] = apiFetch.mock.calls[0];
  expect(url).toBe("http://test/api/ism/check");
  expect(JSON.parse(opts.body)).toEqual({ fragebogen: ISM });
  expect(result.current.qc).toEqual(QC_WARN);
});

test("check() ist fehlertolerant (kein Throw, qc bleibt)", async () => {
  const { result } = renderHook(() => useIsmLiveCheck(QC_OK));
  apiFetch.mockRejectedValue(new Error("netz"));
  await act(async () => { await result.current.check(ISM); });
  expect(result.current.qc).toEqual(QC_OK);
  apiFetch.mockResolvedValue({ ok: false, json: async () => ({}) });
  await act(async () => { await result.current.check(ISM); });
  expect(result.current.qc).toEqual(QC_OK);
  await act(async () => { expect(await result.current.check(null)).toBeNull(); });
});

test("Helfer: issuesForItem / exportNeedsConfirm / confirmExportText", () => {
  expect(issuesForItem(QC_WARN, 0)).toHaveLength(1);
  expect(issuesForItem(QC_WARN, 1)).toHaveLength(0);
  expect(issuesForItem(null, 0)).toEqual([]);
  expect(exportNeedsConfirm(QC_WARN)).toBe(true);
  expect(exportNeedsConfirm(QC_OK)).toBe(false);
  expect(exportNeedsConfirm({ summary: { info: 3 } })).toBe(false);
  expect(confirmExportText(QC_WARN)).toMatch(/1 Hinweis offen/);
});

test("IsmItemEditor: Blur loest onBlur aus, Issues markieren das Item", () => {
  const onBlur = jest.fn();
  const { container } = render(
    <IsmItemEditor item={ISM.items[0]} index={0} onChange={() => {}} onDelete={() => {}}
      onBlur={onBlur} issues={QC_WARN.issues} focused />);
  const box = container.querySelector("#ism-item-0");
  expect(box.className).toMatch(/ism-item-flagged/);
  expect(box.className).toMatch(/ism-item-focus/);
  expect(screen.getByText(/1 Hinweis/)).toBeTruthy();
  fireEvent.blur(container.querySelector("textarea"));
  fireEvent.blur(container.querySelectorAll("input[type=text]")[1]);
  expect(onBlur).toHaveBeenCalledTimes(2);
});

test("IsmItemEditor ohne Issues: keine Markierung; info markiert nicht", () => {
  const info = [{ code: "ISM_POL_ZU_LANG", severity: "info", message: "m", code_detail: { item_index: 0 } }];
  const { container, rerender } = render(
    <IsmItemEditor item={ISM.items[0]} index={0} onChange={() => {}} onDelete={() => {}} />);
  expect(container.querySelector(".ism-item-flagged")).toBeNull();
  rerender(<IsmItemEditor item={ISM.items[0]} index={0} onChange={() => {}} onDelete={() => {}} issues={info} />);
  expect(container.querySelector(".ism-item-flagged")).toBeNull();
});

test("QualityCheckPanel: onFocusItem macht Item-Issues klickbar", () => {
  const onFocusItem = jest.fn();
  render(<QualityCheckPanel data={QC_WARN} readOnly onFocusItem={onFocusItem} />);
  fireEvent.click(screen.getByRole("button", { name: /min- und max-Label/ }));
  expect(onFocusItem).toHaveBeenCalledWith(0);
});

test("QualityCheckPanel: ISM-Status alle 7 Checks bestanden", () => {
  render(<QualityCheckPanel data={QC_OK} readOnly />);
  expect(screen.getByText(/alle 7 Checks bestanden/)).toBeTruthy();
});

test("QualityCheckPanel ohne onFocusItem: kein Button", async () => {
  render(<QualityCheckPanel data={QC_WARN} readOnly />);
  await waitFor(() => expect(screen.queryByRole("button", { name: /min- und max-Label/ })).toBeNull());
});
