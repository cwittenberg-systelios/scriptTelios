"""
QC-Regelkatalog der SNS-Verlaufsauswertung (v19.41, Spec Abschnitt 6).

Deterministisch, ohne Repair (Sprint L). Arbeitet auf dem Job-Ergebnis-JSON
(result_text): text, faktenblock, fakten, stage_a, flags, namen, quelle.
Schweregrade wie quality_check: critical | warning | info.
"""
from __future__ import annotations

import json
import re

from app.services.sns_llm import ABSCHNITTE, sections_present, stage_a_events

_ROEMISCH_RE = r"(?:VI|IV|V|III|II|I)"
_NUM_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d+)?)(?![\d.,]*\d)")
_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(?:(\d{4}))?")
_QUOTE_RE = re.compile(r"[„\"“]([^„“”\"]{12,300})[“”\"]")


def _qi(code, severity, message, repair_hint="", code_detail=None):
    from app.services.quality_check import QualityIssue
    return QualityIssue(code=code, severity=severity, message=message,
                        repair_hint=repair_hint, code_detail=code_detail or {})


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _fact_numbers(faktenblock: str) -> set[float]:
    out = set()
    for m in _NUM_RE.finditer(faktenblock or ""):
        try:
            out.add(float(m.group(1).replace(",", ".")))
        except ValueError:
            pass
    return out


def _fact_dates(faktenblock: str) -> set[tuple[int, int]]:
    return {(int(m.group(1)), int(m.group(2))) for m in _DATE_RE.finditer(faktenblock or "")}


def _body(text: str) -> str:
    """Text ohne die neun Ueberschriften (deren Nummern sind keine Fakten)."""
    for h in ABSCHNITTE:
        num = h.split(".")[0]
        text = re.sub(rf"(^|\n)\s*#*\s*{num}\.\s+[^\n]*", r"\1", text)
    return text


# ── Regeln ───────────────────────────────────────────────────────────────────

def _rule_zahlen(res: dict) -> list:
    text, fb = _body(res.get("text") or ""), res.get("faktenblock") or ""
    facts, fdates = _fact_numbers(fb), _fact_dates(fb)
    fehl_dates = []
    for m in _DATE_RE.finditer(text):
        d = (int(m.group(1)), int(m.group(2)))
        if d not in fdates:
            fehl_dates.append(m.group(0))
    text_wo_dates = _DATE_RE.sub(" ", text)
    fehl = []
    for m in _NUM_RE.finditer(text_wo_dates):
        raw = m.group(1)
        v = float(raw.replace(",", "."))
        if v <= 12 and "." not in raw and "," not in raw:
            continue                     # kleine Ganzzahlen (Tage, Items, Phasen) nicht pruefen
        if v in facts or any(abs(v - f) <= 0.051 for f in facts):
            continue
        fehl.append(raw)
    issues = []
    if fehl_dates:
        issues.append(_qi("SNS_ZAHL_NICHT_IN_FAKTEN", "critical",
                          f"Datum nicht im Faktenblock: {', '.join(sorted(set(fehl_dates))[:6])}",
                          "Nur Daten aus dem Faktenblock verwenden.", {"daten": sorted(set(fehl_dates))}))
    if fehl:
        issues.append(_qi("SNS_ZAHL_NICHT_IN_FAKTEN", "critical",
                          f"Zahl nicht im Faktenblock: {', '.join(sorted(set(fehl))[:8])}",
                          "Jede Zahl muss wörtlich aus dem Faktenblock stammen.", {"zahlen": sorted(set(fehl))}))
    return issues


def _rule_zitate(res: dict) -> list:
    text = res.get("text") or ""
    quelle = _norm(" ".join((e.get("tagebuch") or "") + " " + (e.get("kommentar") or "")
                            for e in res.get("quelle") or []))
    if not quelle:
        return []
    fehl = [q for q in (_norm(m.group(1)) for m in _QUOTE_RE.finditer(text))
            if len(q.split()) >= 3 and q not in quelle]
    if not fehl:
        return []
    return [_qi("SNS_ZITAT_NICHT_IN_QUELLE", "critical",
                f"{len(fehl)} Zitat(e) nicht wörtlich in der Quelle: „{fehl[0][:80]}“",
                "Zitate nur aus den Stage-A-Ereignissen übernehmen.", {"zitate": fehl[:5]})]


def _rule_name_leak(res: dict) -> list:
    text = res.get("text") or ""
    treffer = [n for n in res.get("namen") or [] if re.search(rf"\b{re.escape(n)}\b", text)]
    if not treffer:
        return []
    return [_qi("SNS_NAME_LEAK", "critical", f"Klarname im Text: {', '.join(treffer)}",
                "Namen durch Rolle/Kürzel ersetzen.", {"namen": treffer})]


def _rule_uebergang(res: dict) -> list:
    iso = (res.get("fakten") or {}).get("ordnungsuebergang")
    if not iso:
        return []
    y, m, d = iso.split("-")
    if re.search(rf"\b{int(d):02d}\.{int(m):02d}\.", res.get("text") or "") or \
            re.search(rf"\b{int(d)}\.{int(m)}\.", res.get("text") or ""):
        return []
    return [_qi("SNS_UEBERGANG_FEHLT", "critical",
                f"Datum des Ordnungsübergangs ({int(d):02d}.{int(m):02d}.{y}) fehlt im Text.",
                "Ordnungsübergang mit Datum benennen.", {"datum": iso})]


def _rule_ism_faktor(res: dict) -> list:
    ism = (res.get("fakten") or {}).get("ism")
    if not ism or not ism.get("faktoren"):
        return []
    text = res.get("text") or ""
    saetze = re.split(r"(?<=[.!?])\s+", text)
    fehl = []
    for it in ism["items"]:
        if not it.get("faktor"):
            continue
        frag = _norm(" ".join(it["titel"].split()[:5]))
        for s in saetze:
            if frag and frag in _norm(s):
                genannt = set(re.findall(rf"\b(?:Faktor\s+)?({_ROEMISCH_RE})\b(?=\s|\)|,|:|\.)", s))
                genannt = {g for g in genannt if re.search(rf"(Faktor\s+{g}\b|\({g}\)|\b{g}\s+[A-ZÄÖÜ][a-zäöü]+)", s)}
                if genannt and it["faktor"] not in genannt:
                    fehl.append({"item": it["kurz"], "soll": it["faktor"], "ist": sorted(genannt)})
    if not fehl:
        return []
    return [_qi("SNS_ISM_FAKTOR_FALSCH", "critical",
                f"Item einem falschen Faktor zugeschrieben: {fehl[0]['item']} → {fehl[0]['ist']} statt {fehl[0]['soll']}",
                "Faktorzuordnung aus dem Fragebogen-XML übernehmen.", {"treffer": fehl[:5]})]


def _flag_rule(flag: str, code: str, pattern: str, msg: str):
    def rule(res: dict) -> list:
        if flag not in (res.get("flags") or []):
            return []
        if re.search(pattern, res.get("text") or "", re.I):
            return []
        return [_qi(code, "warning", msg, f"Flag {flag} im Text aufgreifen.", {"flag": flag})]
    return rule


_rule_unbesetzt = _flag_rule("ISM_FAKTOR_UNBESETZT", "SNS_ISM_UNBESETZT_FEHLT",
                             r"unbesetzt|nicht besetzt|ohne item|kein item|keine items|nicht abgebildet",
                             "Unbesetzte ISM-Faktoren werden im Text nicht erwähnt.")
_rule_medikation = _flag_rule("MEDIKATION_IM_UEBERGANGSFENSTER", "SNS_MEDIKATION_FEHLT",
                              r"medikat|dosis|medikament", "Medikation im Übergangsfenster wird nicht aufgegriffen.")
_rule_somatik = _flag_rule("SOMATIK_NEU", "SNS_SOMATIK_FEHLT",
                           r"somat|körperlich|abklär|kopfschmerz|beschwerden", "Neue Somatik wird nicht aufgegriffen.")
_rule_decke = _flag_rule("DECKENEFFEKT_ENDE", "SNS_DECKENEFFEKT_FEHLT",
                         r"decken|obergrenze|skalenende|maximum der skala|am oberen",
                         "Deckeneffekt am Ende wird nicht aufgegriffen.")
_rule_polung_korr = _flag_rule("POLUNG_KORRIGIERT", "SNS_POLUNG_KORREKTUR_FEHLT",
                               r"umgepolt|polung|umpol", "Automatische Polungskorrektur wird im Text nicht erwähnt.")
_rule_suizid = _flag_rule("SUIZIDALITAET_IN_QUELLE", "SNS_SUIZIDALITAET_FEHLT",
                          r"suizid|selbstverletz|lebensmüd", "Tagebuch erwähnt Suizidalität/Selbstverletzung - im Text nicht benannt.")


def _rule_polung(res: dict) -> list:
    pc = [c for c in (res.get("fakten") or {}).get("polung_check") or [] if c.get("fraglich")]
    if not pc:
        return []
    return [_qi("SNS_POLUNG_FRAGLICH", "warning",
                "Polung fraglich (r mit Komposit entgegen der Annahme): " + ", ".join(c["item"] for c in pc),
                "Hinweis an die Therapeut:in - Polung im Fragebogen prüfen, kein Textfehler.", {"items": pc})]


def _rule_abschnitte(res: dict) -> list:
    miss = sections_present(res.get("text") or "")
    if not miss:
        return []
    return [_qi("SNS_ABSCHNITT_FEHLT", "critical", "Abschnitt(e) fehlen: " + "; ".join(miss),
                "Alle neun Überschriften ausgeben.", {"fehlend": miss})]


def _rule_aufzaehlung(res: dict) -> list:
    body = _body(res.get("text") or "")
    n = len(re.findall(r"^\s*[-*•]\s+\S", body, re.M))
    if n == 0:
        return []
    return [_qi("SNS_AUFZAEHLUNG", "warning", f"{n} Aufzählungszeile(n) im Fließtext.",
                "Systemische Prosa ohne Listen.", {"anzahl": n})]


def _rule_hypnosystemisch(res: dict) -> list:
    if "hypnosystem" not in (res.get("text") or "").lower():
        return []
    return [_qi("HYPNOSYSTEMISCH", "critical", "Begriff „hypnosystemisch“ im Output (Klinik-Regel v19.30).",
                "Begriff entfernen.", {})]


def _rule_stage_a(res: dict) -> list:
    sa = res.get("stage_a") or []
    verworfen = sum(int(t.get("verworfen") or 0) for t in sa)
    n = len(stage_a_events(sa))
    if not sa:
        return [_qi("SNS_STAGE_A_LEER", "warning", "Keine Tagebuch-Ereignisse extrahiert.",
                    "Tagebuchspalte prüfen.", {})]
    if verworfen:
        return [_qi("SNS_STAGE_A_VERWORFEN", "info",
                    f"{verworfen} Stage-A-Ereignis(se) verworfen (Zitat nicht in der Quelle), {n} übernommen.",
                    "", {"verworfen": verworfen, "uebernommen": n})]
    return []


SNS_CHECKS = (
    ("zahlen", _rule_zahlen), ("zitate", _rule_zitate), ("name_leak", _rule_name_leak),
    ("uebergang", _rule_uebergang), ("ism_faktor", _rule_ism_faktor), ("unbesetzt", _rule_unbesetzt),
    ("medikation", _rule_medikation), ("somatik", _rule_somatik), ("decke", _rule_decke),
    ("suizid", _rule_suizid), ("polung", _rule_polung), ("abschnitte", _rule_abschnitte),
    ("aufzaehlung", _rule_aufzaehlung), ("hypnosystemisch", _rule_hypnosystemisch),
    ("polung_korrigiert", _rule_polung_korr), ("stage_a", _rule_stage_a),
)
SNS_CHECKS_RUN = len(SNS_CHECKS)


def sns_issues_for_result(res: dict) -> list:
    issues = []
    for _name, fn in SNS_CHECKS:
        issues.extend(fn(res))
    return issues


def run_sns_quality_check(result_text: str) -> list:
    """QC-Einstieg fuer den Job-Pfad: result_text ist das Ergebnis-JSON."""
    try:
        res = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return [_qi("SNS_ERGEBNIS_UNLESBAR", "critical", "Job-Ergebnis ist kein JSON.", "", {})]
    if not isinstance(res, dict) or not (res.get("text") or "").strip():
        return [_qi("SNS_TEXT_LEER", "critical", "Kein Berichtstext im Ergebnis.", "", {})]
    return sns_issues_for_result(res)
