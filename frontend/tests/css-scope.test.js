/**
 * scriptTelios Frontend – CSS-Scoping-Guard (v19.10)
 *
 * Das Stylesheet aus src/styles.jsx wird per useHeadStyle() global in
 * <head> injiziert und gilt damit fuer die komplette Confluence-Seite.
 * Jeder ungescopte Selektor leakt in die Seite (v19.10: 182 von 189).
 *
 * Dieser Test schlaegt fehl, sobald ein Top-Level-Selektor weder mit
 * .st-scope noch mit #st-root beginnt.
 *
 * Ausfuehren: npm test (im frontend/-Verzeichnis)
 */

import fs from "fs";
import path from "path";

// v19.21: Jest laeuft jetzt ueber babel-jest (CJS) - __dirname ist verfuegbar,
// import.meta.url nicht mehr noetig.

/** Erlaubte Ausnahmen: globale Custom-Properties und At-Rules. */
const ALLOWED_EXACT = [":root"];
const ALLOWED_PREFIX = [".st-scope", "#st-root", "@"];

/** Liest das Template-Literal `const S = \`...\`;` aus styles.jsx. */
function readStylesheet() {
  const src = fs.readFileSync(
    path.join(__dirname, "..", "src", "styles.jsx"),
    "utf8",
  );
  const m = src.match(/const S = `([\s\S]*?)`;\n/);
  if (!m) throw new Error("Stylesheet-Literal 'const S = `...`' nicht gefunden");
  return m[1];
}

/** Extrahiert alle Top-Level-Selektoren (Bloecke in @-Regeln bleiben aussen vor). */
function topLevelSelectors(css) {
  const noComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const selectors = [];
  let depth = 0;
  let buf = "";

  for (const ch of noComments) {
    if (ch === "{") {
      if (depth === 0) {
        selectors.push(buf.trim());
        buf = "";
      }
      depth += 1;
    } else if (ch === "}") {
      depth -= 1;
      if (depth === 0) buf = "";
    } else if (depth === 0) {
      buf += ch;
    }
  }
  return selectors.filter(Boolean);
}

function isScoped(selector) {
  if (ALLOWED_EXACT.includes(selector)) return true;
  return ALLOWED_PREFIX.some((p) => selector.startsWith(p));
}

describe("CSS-Scoping (src/styles.jsx)", () => {
  const css = readStylesheet();
  const selectors = topLevelSelectors(css);

  test("Stylesheet enthaelt Regeln", () => {
    expect(selectors.length).toBeGreaterThan(100);
  });

  test("geschweifte Klammern sind ausbalanciert", () => {
    const noComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
    const open = (noComments.match(/{/g) || []).length;
    const close = (noComments.match(/}/g) || []).length;
    expect(open).toBe(close);
  });

  test("kein Top-Level-Selektor leakt in die Confluence-Seite", () => {
    const leaking = selectors.filter((s) => !isScoped(s));
    expect(leaking).toEqual([]);
  });

  test("kein nackter Element-Selektor (z.B. textarea)", () => {
    const bare = selectors.filter((s) => /^[a-z][a-z0-9]*(\s|:|,|$)/i.test(s));
    expect(bare).toEqual([]);
  });

  test("keine Serif-Schrift im Stylesheet", () => {
    expect(/\bserif\b/.test(css.replace(/sans-serif/g, ""))).toBe(false);
  });

  test("Guard erkennt einen neu eingefuegten ungescopten Selektor", () => {
    const injected = topLevelSelectors(css + "\n  .foo { color: red; }\n");
    expect(injected.filter((s) => !isScoped(s))).toEqual([".foo"]);
  });
});
