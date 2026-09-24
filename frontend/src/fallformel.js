// ────────────────────────────────────────────────────────────────────────────
// src/fallformel.js — Fallformel des thematischen Entlassberichts (v19.28).
//
// Spiegel von backend/app/services/fallformel.py (split_sections,
// parse_themenkandidaten, select_themen): die Therapeut:in waehlt im UI
// bis zu MAX_THEMEN Themenkandidaten aus und editiert den Text; der
// reduzierte Text geht als Form-Feld `fallformel` an POST /jobs/generate
// (Stage 1b wird dann uebersprungen, D1=B). Pure Funktionen, Jest-getestet.
// ────────────────────────────────────────────────────────────────────────────

const MAX_THEMEN = 3;
const SECTION_THEMEN = "Themenkandidaten";
const SECTION_ORDER = ["Auftrag", "Themenkandidaten", "Wendepunkte je Modalität", "Symptomveränderung", "Offene Themen"];
const KEIN_MUSTER_SATZ = "Kein durchgängiges Muster dokumentiert.";

const H_RE = /^\s*#{2,4}\s*(.+?)\s*$/;
const ITEM_RE = /^\s*(\d+)[.)]\s+(.*)$/;
// v19.28.2: das Backend-Postprocessing strippt "### " und "**" - eine Zeile,
// die nackt einem Abschnittsnamen entspricht (optional ":"), zaehlt ebenfalls
// als Ueberschrift (Feedback 24.09.: Formular blieb leer).
const PLAIN_H_RE = new RegExp("^\\s*(?:#{1,6}\\s*)?\\**(" + SECTION_ORDER.map(n => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")\\s*:?\\s*\\**\\s*:?\\s*$", "i");

function _headingOf(line) {
  const m = H_RE.exec(line);
  if (m) return m[1].trim().replace(/:$/, "").replace(/^\*+|\*+$/g, "").trim();
  const p = PLAIN_H_RE.exec(line);
  if (p) return SECTION_ORDER.find(n => n.toLowerCase() === p[1].trim().toLowerCase()) || p[1].trim();
  return null;
}

// {name: body} in Dateireihenfolge (Map, damit unbekannte Abschnitte erhalten bleiben)
function splitSections(text) {
  const out = new Map();
  if (!text) return out;
  let key = null;
  let buf = [];
  for (const line of String(text).split("\n")) {
    const h = _headingOf(line);
    if (h !== null) {
      if (key !== null) out.set(key, buf.join("\n").trim());
      key = h;
      buf = [];
    } else if (key !== null) {
      buf.push(line);
    }
  }
  if (key !== null) out.set(key, buf.join("\n").trim());
  return out;
}

// Nummerierte Eintraege unter "### Themenkandidaten" (Folgezeilen zusammengefuehrt).
function parseThemen(text) {
  const sec = splitSections(text).get(SECTION_THEMEN) || "";
  if (!sec || sec.toLowerCase().includes(KEIN_MUSTER_SATZ.toLowerCase())) return [];
  const items = [];
  let cur = null;
  for (const line of sec.split("\n")) {
    const m = ITEM_RE.exec(line);
    if (m) {
      if (cur) items.push(cur.join(" ").trim());
      cur = [m[2].trim()];
    } else if (line.trim() && cur) {
      cur.push(line.trim());
    }
  }
  if (cur) items.push(cur.join(" ").trim());
  return items.filter(Boolean);
}

// Kurztitel eines Kandidaten ("**Titel** – ..." -> "Titel").
function themaTitel(item) {
  const m = /\*\*(.+?)\*\*/.exec(item || "");
  if (m) return m[1].trim();
  return String(item || "").split(" – ")[0].split(" - ")[0].trim().slice(0, 80);
}

// Reduziert die Themenkandidaten auf die gewaehlten Indizes (0-basiert, max.
// MAX_THEMEN, Reihenfolge = Auswahlreihenfolge); leere Auswahl -> erstes Thema.
function applyThemenAuswahl(text, keepIdx) {
  const items = parseThemen(text);
  if (!items.length) return text;
  let keep = (keepIdx || []).filter(i => Number.isInteger(i) && i >= 0 && i < items.length).slice(0, MAX_THEMEN);
  if (!keep.length) keep = [0];
  const sections = splitSections(text);
  sections.set(SECTION_THEMEN, keep.map((i, n) => `${n + 1}. ${items[i]}`).join("\n"));
  const parts = [];
  for (const k of SECTION_ORDER) if (sections.has(k)) parts.push(`### ${k}\n${sections.get(k)}`.trimEnd());
  for (const [k, body] of sections) if (!SECTION_ORDER.includes(k)) parts.push(`### ${k}\n${body}`.trimEnd());
  return parts.join("\n\n").trim();
}

// Anzeige-Label eines Stage-1b-Signals (Backend-Issue-Dict).
function issueLabel(issue) {
  if (!issue) return "";
  const sev = issue.severity === "critical" || issue.severity === "high" ? "⚠" : "ℹ";
  return `${sev} ${issue.detail || issue.type || ""}`.trim();
}

// ── v19.28.1: strukturierter Editor (kein Markdown-Editing durch die Therapeut:in) ──
//
// Die Fallformel wird beim Empfang EINMAL in eine Struktur geparst, im UI
// feldweise editiert und erst beim Senden wieder in dasselbe ###-Markdown
// serialisiert, das das Backend (select_themen/parse_themenkandidaten, QC-
// Wendepunkt-Regex "- <Modalitaet>: ...") versteht. Abgewaehlte Themen
// werden beim Senden weggelassen (Entscheidung 2026-09-22).

const MODALITAETEN = ["Einzeltherapie", "Gruppentherapie", "Nonverbale Therapien"];
const KEINE_WENDEPUNKTE = "keine Wendepunkte dokumentiert";
const MOD_LINE_RE = /^\s*[-*]\s*(Einzeltherapie|Gruppentherapie|Nonverbale Therapien?)\s*:\s*(.*)$/i;
const BELEGE_RE = /\bBelege?\s*:\s*/i;

function _norm(s) { return String(s || "").replace(/\s+/g, " ").trim(); }

// "**Titel** – Beschreibung … Belege: (Einzel 23.12.), (…)" -> {titel, desc, belege}
function parseThemaItem(item) {
  let rest = _norm(item);
  let titel = "";
  const m = /^\*\*(.+?)\*\*\s*(?:[–-]\s*)?/.exec(rest);
  if (m) { titel = m[1].trim(); rest = rest.slice(m[0].length); }
  let belege = "";
  const b = BELEGE_RE.exec(rest);
  if (b) { belege = rest.slice(b.index + b[0].length).trim(); rest = rest.slice(0, b.index).trim(); }
  if (!titel) {
    // ohne Fettdruck: erster Halbsatz bis " – " als Titel
    const parts = rest.split(/\s[–-]\s/);
    titel = parts[0].trim(); rest = parts.slice(1).join(" – ").trim();
  }
  return { titel, desc: rest.replace(/\s*[–-]\s*$/, "").trim(), belege };
}

function _modKey(name) {
  const n = name.toLowerCase();
  if (n.startsWith("einzel")) return MODALITAETEN[0];
  if (n.startsWith("gruppe")) return MODALITAETEN[1];
  return MODALITAETEN[2];
}

// Markdown -> Struktur. Unbekannte Abschnitte bleiben in `extra` erhalten.
function parseFallformel(text) {
  const sec = splitSections(text);
  const wendepunkte = Object.fromEntries(MODALITAETEN.map(m => [m, []]));
  for (const line of (sec.get("Wendepunkte je Modalität") || "").split("\n")) {
    const m = MOD_LINE_RE.exec(line);
    if (!m) continue;
    const body = _norm(m[2]);
    if (!body || body.toLowerCase().includes(KEINE_WENDEPUNKTE.toLowerCase())) continue;
    wendepunkte[_modKey(m[1])].push(...body.split(/;\s+/).map(_norm).filter(Boolean));
  }
  const extra = {};
  for (const [k, v] of sec) if (!SECTION_ORDER.includes(k)) extra[k] = v;
  return {
    auftrag: _norm(sec.get("Auftrag") || ""),
    themen: parseThemen(text).map(parseThemaItem),
    wendepunkte,
    symptom: _norm(sec.get("Symptomveränderung") || ""),
    offen: _norm(sec.get("Offene Themen") || ""),
    extra,
  };
}

// Struktur + Auswahl (Indizes in Berichtsreihenfolge) -> Markdown fuers Backend.
function serializeFallformel(ff, selection) {
  const sel = (selection || []).filter(i => Number.isInteger(i) && ff.themen[i]).slice(0, MAX_THEMEN);
  const th = sel.length
    ? sel.map((i, n) => {
        const t = ff.themen[i];
        const titel = _norm(t.titel) || `Thema ${n + 1}`;
        return `${n + 1}. **${titel}**` + (_norm(t.desc) ? ` – ${_norm(t.desc)}` : "") + (_norm(t.belege) ? ` Belege: ${_norm(t.belege)}` : "");
      }).join("\n")
    : KEIN_MUSTER_SATZ;
  const wp = MODALITAETEN.map(m => {
    const list = (ff.wendepunkte[m] || []).map(_norm).filter(Boolean);
    return `- ${m}: ${list.length ? list.join("; ") : KEINE_WENDEPUNKTE}`;
  }).join("\n");
  const parts = [
    `### Auftrag\n${_norm(ff.auftrag)}`,
    `### Themenkandidaten\n${th}`,
    `### Wendepunkte je Modalität\n${wp}`,
    `### Symptomveränderung\n${_norm(ff.symptom)}`,
    `### Offene Themen\n${_norm(ff.offen)}`,
  ];
  for (const [k, v] of Object.entries(ff.extra || {})) parts.push(`### ${k}\n${String(v).trim()}`);
  return parts.join("\n\n").trim();
}

export { MAX_THEMEN, KEIN_MUSTER_SATZ, MODALITAETEN, KEINE_WENDEPUNKTE, splitSections, parseThemen, themaTitel, applyThemenAuswahl, issueLabel, parseThemaItem, parseFallformel, serializeFallformel };
