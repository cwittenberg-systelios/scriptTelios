/** v19.22: persistenter Aufnahmen-Metadaten-Cache (localStorage, pro Therapeut). */
import { saveRecordingsCache, loadRecordingsCache, fmtCacheAge } from "../src/shared.js";

const REC = [
  { id: 79, label: "Herr N. 11.09.", duration_s: 3758.7, status: "ready", created_at: "2026-09-11T10:00:00Z",
    transcript: "[A]: sehr vertraulicher Inhalt", error_msg: null, coverage_gap_s: null, has_audio: true },
  { id: 80, label: "Frau W.", status: "deleted", transcript: "x" },
  { id: "tmp-1", label: "temp", status: "uploading" },
];

beforeEach(() => { localStorage.clear(); window.SYSTELIOS_USER = "c.saur"; });

test("speichert nur Metadaten, keine Transkripte; gelöschte und temporäre Eintraege nicht", () => {
  saveRecordingsCache(REC);
  const raw = localStorage.getItem("st_recordings_c.saur");
  expect(raw).toBeTruthy();
  expect(raw).not.toMatch(/vertraulich/);
  const parsed = JSON.parse(raw);
  expect(parsed.items).toHaveLength(1);
  expect(parsed.items[0]).toEqual({ id: 79, label: "Herr N. 11.09.", duration_s: 3758.7, status: "ready",
    created_at: "2026-09-11T10:00:00Z", coverage_gap_s: null, has_audio: true });
});

test("Cache ist pro Therapeut getrennt", () => {
  saveRecordingsCache(REC);
  window.SYSTELIOS_USER = "e.krause";
  expect(loadRecordingsCache()).toBeNull();
  window.SYSTELIOS_USER = "c.saur";
  expect(loadRecordingsCache().items).toHaveLength(1);
});

test("geladene Eintraege sind als __cached markiert und ohne Transkript", () => {
  saveRecordingsCache(REC);
  const c = loadRecordingsCache();
  expect(c.savedAt).toBeGreaterThan(0);
  expect(c.items[0].__cached).toBe(true);
  expect(c.items[0].transcript).toBeNull();
  expect(fmtCacheAge(c.savedAt)).toMatch(/\d{2}\.\d{2}\., \d{2}:\d{2}/);
});

test("kaputter Cache-Inhalt wird ignoriert", () => {
  localStorage.setItem("st_recordings_c.saur", "{nicht json");
  expect(loadRecordingsCache()).toBeNull();
});
