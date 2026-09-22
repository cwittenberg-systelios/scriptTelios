/**
 * v19.28 (S5): src/fallformel.js - Spiegel von backend/app/services/fallformel.py.
 */
import { describe, test, expect } from "@jest/globals";
import { applyThemenAuswahl, issueLabel, parseThemen, splitSections, themaTitel, KEIN_MUSTER_SATZ, MAX_THEMEN } from "../src/fallformel.js";

const FF = `### Auftrag
Frau M. kam mit dem Wunsch nach Entlastung (Aufnahme 17.12.).

### Themenkandidaten
1. **Angst als alter Schutz** – Angst als Schutz verstanden. Belege: (Einzel 23.12.)
2. **Anpassung vs. Bedürfnisse** – Grenzen fallen schwer.
   Belege: (Bezugsgruppe 06.01.)
3. **Perfektionismus** – Belege: (Einzel 08.01.)
4. **Viertes** – zu viel.

### Wendepunkte je Modalität
- Einzeltherapie: Elternbesuch (13.01.)

### Symptomveränderung
Weniger Angst.

### Offene Themen
Alltag.
`;

describe("splitSections / parseThemen", () => {
  test("Abschnitte in Reihenfolge", () => {
    expect([...splitSections(FF).keys()]).toEqual(["Auftrag", "Themenkandidaten", "Wendepunkte je Modalität", "Symptomveränderung", "Offene Themen"]);
  });
  test("mehrzeilige Kandidaten zusammengefuehrt", () => {
    const t = parseThemen(FF);
    expect(t).toHaveLength(4);
    expect(t[1]).toContain("(Bezugsgruppe 06.01.)");
    expect(themaTitel(t[1])).toBe("Anpassung vs. Bedürfnisse");
  });
  test("kein Muster -> leer", () => {
    expect(parseThemen(FF.replace("1. **Angst", `${KEIN_MUSTER_SATZ}\n1. **Angst`))).toEqual([]);
  });
  test("leer/undefined robust", () => {
    expect(parseThemen("")).toEqual([]);
    expect(splitSections(undefined).size).toBe(0);
  });
});

describe("applyThemenAuswahl", () => {
  test("Auswahl + Neunummerierung, Rest unveraendert", () => {
    const out = applyThemenAuswahl(FF, [2, 0]);
    const t = parseThemen(out);
    expect(t.map(themaTitel)).toEqual(["Perfektionismus", "Angst als alter Schutz"]);
    expect(out).toContain("1. **Perfektionismus");
    expect(out).toContain("### Wendepunkte je Modalität\n- Einzeltherapie: Elternbesuch (13.01.)");
    expect(out.indexOf("### Auftrag")).toBeLessThan(out.indexOf("### Themenkandidaten"));
    expect(out).not.toContain("Viertes");
  });
  test("max MAX_THEMEN, leere Auswahl -> erstes Thema", () => {
    expect(parseThemen(applyThemenAuswahl(FF, [0, 1, 2, 3]))).toHaveLength(MAX_THEMEN);
    expect(parseThemen(applyThemenAuswahl(FF, []))).toHaveLength(1);
    expect(parseThemen(applyThemenAuswahl(FF, [9, -1]))).toHaveLength(1);
  });
  test("ohne Kandidaten unveraendert", () => {
    const txt = "### Auftrag\nx\n\n### Themenkandidaten\n" + KEIN_MUSTER_SATZ;
    expect(applyThemenAuswahl(txt, [0])).toBe(txt);
  });
  test("unbekannte Abschnitte bleiben hinten erhalten", () => {
    const out = applyThemenAuswahl(FF + "\n### Extra\nzeug\n", [0]);
    expect(out.endsWith("### Extra\nzeug")).toBe(true);
  });
});

describe("issueLabel", () => {
  test("Schweregrad-Symbol", () => {
    expect(issueLabel({ severity: "high", detail: "Datum ohne Quelle" })).toBe("⚠ Datum ohne Quelle");
    expect(issueLabel({ severity: "medium", type: "zu_viele_themen" })).toBe("ℹ zu_viele_themen");
    expect(issueLabel(null)).toBe("");
  });
});
