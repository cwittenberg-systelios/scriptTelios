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

// {name: body} in Dateireihenfolge (Map, damit unbekannte Abschnitte erhalten bleiben)
function splitSections(text) {
  const out = new Map();
  if (!text) return out;
  let key = null;
  let buf = [];
  for (const line of String(text).split("\n")) {
    const m = H_RE.exec(line);
    if (m) {
      if (key !== null) out.set(key, buf.join("\n").trim());
      key = m[1].trim();
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

export { MAX_THEMEN, KEIN_MUSTER_SATZ, splitSections, parseThemen, themaTitel, applyThemenAuswahl, issueLabel };
