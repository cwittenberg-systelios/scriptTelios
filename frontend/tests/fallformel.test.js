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

// ── v19.28.1: strukturierter Editor ──────────────────────────────────────────
import { parseFallformel, parseThemaItem, serializeFallformel, MODALITAETEN, KEINE_WENDEPUNKTE } from "../src/fallformel.js";

const FF2 = `### Auftrag
Frau M. kam mit dem Wunsch nach Entlastung (Aufnahme 17.12.).

### Themenkandidaten
1. **Angst als alter Schutz** – Angst als Schutz verstanden. Belege: (Einzel 23.12.), (Einzel 30.12.)
2. **Anpassung vs. Bedürfnisse** – Grenzen fallen schwer.
   Belege: (Bezugsgruppe 06.01.)
3. Perfektionismus – ohne Fettdruck

### Wendepunkte je Modalität
- Einzeltherapie: Elternbesuch (13.01.); Sich in den Mittelpunkt stellen (20.01.)
- Gruppentherapie: keine Wendepunkte dokumentiert
- Nonverbale Therapien: Fatman-Zeichnung (08.01.)

### Symptomveränderung
Weniger Angst (20.01.).

### Offene Themen
Alltag.

### Extra
zeug`;

describe("parseThemaItem", () => {
  test("Titel, Beschreibung, Belege getrennt", () => {
    expect(parseThemaItem("**Angst als alter Schutz** – Angst als Schutz verstanden. Belege: (Einzel 23.12.)"))
      .toEqual({ titel: "Angst als alter Schutz", desc: "Angst als Schutz verstanden.", belege: "(Einzel 23.12.)" });
  });
  test("ohne Fettdruck: erster Halbsatz als Titel", () => {
    expect(parseThemaItem("Perfektionismus – ohne Fettdruck")).toEqual({ titel: "Perfektionismus", desc: "ohne Fettdruck", belege: "" });
  });
  test("nur Titel", () => {
    expect(parseThemaItem("**Nur Titel**")).toEqual({ titel: "Nur Titel", desc: "", belege: "" });
  });
});

describe("parseFallformel / serializeFallformel", () => {
  test("Struktur vollstaendig", () => {
    const ff = parseFallformel(FF2);
    expect(ff.auftrag).toMatch(/^Frau M\./);
    expect(ff.themen).toHaveLength(3);
    expect(ff.themen[1].belege).toBe("(Bezugsgruppe 06.01.)");
    expect(ff.wendepunkte).toEqual({
      Einzeltherapie: ["Elternbesuch (13.01.)", "Sich in den Mittelpunkt stellen (20.01.)"],
      Gruppentherapie: [],
      "Nonverbale Therapien": ["Fatman-Zeichnung (08.01.)"],
    });
    expect(ff.symptom).toBe("Weniger Angst (20.01.).");
    expect(ff.offen).toBe("Alltag.");
    expect(ff.extra).toEqual({ Extra: "zeug" });
  });
  test("Serialisierung laesst abgewaehlte Themen weg, Reihenfolge = Auswahl", () => {
    const ff = parseFallformel(FF2);
    const out = serializeFallformel(ff, [2, 0]);
    expect(out).toContain("1. **Perfektionismus** – ohne Fettdruck\n2. **Angst als alter Schutz** – Angst als Schutz verstanden. Belege: (Einzel 23.12.), (Einzel 30.12.)");
    expect(out).not.toContain("Anpassung");
    expect(out).toContain(`- Gruppentherapie: ${KEINE_WENDEPUNKTE}`);
    expect(out).toContain("- Einzeltherapie: Elternbesuch (13.01.); Sich in den Mittelpunkt stellen (20.01.)");
    expect(out.endsWith("### Extra\nzeug")).toBe(true);
    // Roundtrip: Backend-Format bleibt parsebar
    const again = parseFallformel(out);
    expect(again.themen.map(t => t.titel)).toEqual(["Perfektionismus", "Angst als alter Schutz"]);
    expect(again.wendepunkte.Einzeltherapie).toHaveLength(2);
  });
  test("keine Auswahl -> Standardsatz", () => {
    const out = serializeFallformel(parseFallformel(FF2), []);
    expect(out).toContain("### Themenkandidaten\nKein durchgängiges Muster dokumentiert.");
  });
  test("leere Felder und Modalitaeten", () => {
    const ff = { auftrag: "", themen: [{ titel: "", desc: "x", belege: "" }], symptom: "", offen: "", extra: {},
      wendepunkte: Object.fromEntries(MODALITAETEN.map(m => [m, [" ", ""]])) };
    const out = serializeFallformel(ff, [0]);
    expect(out).toContain("1. **Thema 1** – x");
    expect((out.match(new RegExp(KEINE_WENDEPUNKTE, "g")) || []).length).toBe(3);
  });
  test("max 3 Themen auch bei laengerer Auswahl", () => {
    const ff = parseFallformel(FF2);
    ff.themen.push({ titel: "Viertes", desc: "", belege: "" });
    const out = serializeFallformel(ff, [0, 1, 2, 3]);
    expect(out).not.toContain("Viertes");
  });
});
