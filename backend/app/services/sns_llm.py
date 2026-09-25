"""
SNS-Verlaufsauswertung (v19.41) - LLM-Teil.

  Pseudonymisierung  deterministisch vor Stage A (Spec 5.3)
  Stage A            Tagebuch-Extraktion, strict JSON, Batches
  Flags nach Stage A PLATEAU_ALS_EINBRUCH, MEDIKATION_IM_UEBERGANGSFENSTER,
                     SOMATIK_NEU, SUIZIDALITAET_IN_QUELLE
  Stage B            Interpretation als Fliesstext (9 Abschnitte)

Alle LLM-Aufrufe laufen ueber llm.generate_text (Structured Output fuer
Stage A / Zuordnung). Der Tagebuch-Rohtext geht NUR in Stage A.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any, Awaitable, Callable, Optional

from app.services.sns_verlauf import ISM_ROEMISCH, SnsAnalyse

logger = logging.getLogger(__name__)

WORKFLOW = "sns_verlauf"
STAGE_A_BATCH = 6
STAGE_A_TEMPERATURE = 0.1

KATEGORIEN = (
    "beziehung_partner", "gruppe", "leistung_bewertung", "familie_kinder",
    "therapie_intervention", "autonomie_erfahrung", "medikation", "somatik",
    "belastungserprobung", "biografie", "beruf", "sonstiges",
)
TENOR = ("belastet", "gemischt", "neutral/plateau", "gut")

ABSCHNITTE = (
    "1. Zusammenfassung",
    "2. Fragebögen und Faktorstruktur",
    "3. Verlauf der Kernfaktoren",
    "4. Individueller Fragebogen: Zielerleben und ISM-Faktoren",
    "5. Dynamische Komplexität und kritische Instabilität",
    "6. Rekurrenzmuster",
    "7. Phasen",
    "8. Anfang und Ende",
    "9. Einordnung und Hinweise für Entlassung und Nachsorge",
)

GeneratorFn = Callable[..., Awaitable[dict]]


# ═════════════════════════════════════════════════════════════════════════════
# Pseudonymisierung (Spec 5.3)
# ═════════════════════════════════════════════════════════════════════════════

_ROLLEN = {
    "partner": "Partner", "partnerin": "Partnerin", "mann": "Mann", "frau": "Frau",
    "freund": "Freund", "freundin": "Freundin", "ehemann": "Ehemann", "ehefrau": "Ehefrau",
    "tochter": "Tochter", "sohn": "Sohn", "mutter": "Mutter", "mama": "Mama", "vater": "Vater",
    "papa": "Papa", "schwester": "Schwester", "bruder": "Bruder", "oma": "Oma", "opa": "Opa",
    "kollege": "Kollege", "kollegin": "Kollegin", "chef": "Chef", "chefin": "Chefin",
    "ex": "Ex", "exmann": "Ex-Mann", "exfrau": "Ex-Frau", "kind": "Kind",
    "mitbewohner": "Mitbewohner", "mitbewohnerin": "Mitbewohnerin",
    "zimmernachbarin": "Zimmernachbarin", "zimmernachbar": "Zimmernachbar",
}
_ROLLE_RE = re.compile(
    r"\b(mein(?:e|em|en|er|es)?)\s+(" + "|".join(sorted(_ROLLEN, key=len, reverse=True)) + r")\s+"
    r"([A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?)\b", re.I,
)
_KONTEXT_RE = re.compile(
    r"\b(mit|bei|von|an|für|und|ohne|neben|über)\s+([A-ZÄÖÜ][a-zäöüß]{2,}(?:-[A-ZÄÖÜ][a-zäöüß]+)?)\b",
)
_TITEL_RE = re.compile(r"\b(Frau|Herr|Herrn|Dr\.|Dr|Prof\.|Prof|Therapeut|Therapeutin)\s+$")
_NICHT_NAMEN = {
    "Klinik", "Gruppe", "Therapie", "Wald", "Sonne", "Tee", "Kopf", "Zeit", "Tag", "Abend",
    "Morgen", "Hause", "Haus", "Woche", "Wochenende", "Nachsorge", "Musik", "Sport", "Ruhe",
    "Natur", "Angst", "Ängste", "Freude", "Dankbarkeit", "Vorfreude", "Wehmut", "Lesen", "Packen",
    "Kunsttherapie", "Rollenspiel", "Entlassung", "Medikation", "Dosis", "Abschied", "Feedback",
    "Kraft", "Körper", "Gedanken", "Selbstzweifel", "Grenzen", "Ziele", "Ziel", "Podest",
    "Stufe", "Treppe", "Kopfschmerzen", "Gespräch", "Telefonat", "Streit", "Sorge", "Druck",
    "Brust", "Bild", "Zimmer", "Mittag", "Nacht", "Beginn", "Ende", "Blick", "Weg", "Mut",
    "Lust", "Hoffnung", "Trauer", "Wut", "Scham", "Schuld", "Stress", "Erleichterung",
    "Klarheit", "Unruhe", "Nein", "Ja", "Bogen", "Fragebogen", "Einzelgespräch", "Visite",
    "Klientin", "Klient", "Mitklientin", "Mitklient", "Mitklienten", "Mitklientinnen",
}


def pseudonymisiere(entries: list[dict], vorname: str | None = None, kuerzel: str = "Kürzel",
                    ) -> tuple[list[dict], list[str]]:
    """Ersetzt Namen deterministisch in tagebuch + kommentar.

    (a) Vorname der Klient:in -> Kuerzel
    (b) Angehoerige ueber Rollenkontext ('mein Partner X') -> Rolle
    (c) uebrige grossgeschriebene Namen im Kontext 'mit/bei/von X' -> 'Mitklient:in'
        (Behandler:innen mit Titel bleiben)
    Rueckgabe: (neue Eintraege, gefundene Namen fuer QC SNS_NAME_LEAK)
    """
    namen: set[str] = set()
    vn = (vorname or "").strip()
    rollen_namen: dict[str, str] = {}

    def _a(text: str) -> str:
        if not vn:
            return text
        namen.add(vn)
        return re.sub(rf"\b{re.escape(vn)}\b", kuerzel, text)

    def _b(text: str) -> str:
        def rep(m):
            rolle = _ROLLEN.get(m.group(2).lower(), m.group(2))
            name = m.group(3)
            if name.lower() in _ROLLEN or name in _NICHT_NAMEN:
                return m.group(0)
            namen.add(name)
            rollen_namen[name] = rolle
            return f"{m.group(1)} {rolle}"
        return _ROLLE_RE.sub(rep, text)

    def _c(text: str) -> str:
        def rep(m):
            name = m.group(2)
            if name in _NICHT_NAMEN or name.lower() in _ROLLEN:
                return m.group(0)
            vor = text[:m.start()]
            if _TITEL_RE.search(vor[-14:] + " "):
                return m.group(0)
            namen.add(name)
            ersatz = rollen_namen.get(name, "Mitklient:in")
            return f"{m.group(1)} {ersatz}"
        return _KONTEXT_RE.sub(rep, text)

    def _rest(text: str) -> str:
        # Bereits erkannte Namen auch ausserhalb der Kontexte ersetzen
        for name in sorted(namen, key=len, reverse=True):
            if name == vn:
                continue
            ersatz = rollen_namen.get(name, "Mitklient:in")
            text = re.sub(rf"(?<!Frau )(?<!Herr )(?<!Dr\. )\b{re.escape(name)}\b", ersatz, text)
        return text

    out = []
    for e in entries:
        t, k = e.get("tagebuch", "") or "", e.get("kommentar", "") or ""
        t, k = _a(t), _a(k)
        t, k = _b(t), _b(k)
        t, k = _c(t), _c(k)
        out.append({**e, "tagebuch": t, "kommentar": k})
    out = [{**e, "tagebuch": _rest(e["tagebuch"]), "kommentar": _rest(e["kommentar"])} for e in out]
    return out, sorted(namen)


# ═════════════════════════════════════════════════════════════════════════════
# Stage A - Tagebuch-Extraktion
# ═════════════════════════════════════════════════════════════════════════════

def build_stage_a_schema() -> dict:
    return {
        "type": "object", "required": ["tage"],
        "properties": {"tage": {"type": "array", "items": {
            "type": "object", "required": ["datum", "tenor", "ereignisse"],
            "properties": {
                "datum": {"type": "string"},
                "tenor": {"enum": list(TENOR)},
                "ereignisse": {"type": "array", "items": {
                    "type": "object", "required": ["kategorie", "kurz", "zitat"],
                    "properties": {
                        "kategorie": {"enum": list(KATEGORIEN)},
                        "ism_faktor": {"type": ["integer", "null"]},
                        "kurz": {"type": "string"},
                        "zitat": {"type": "string"},
                    }}}}}}},
    }


def build_stage_a_system_prompt(ism_items: list[dict] | None) -> str:
    kat = ", ".join(KATEGORIEN)
    parts = [
        "Du extrahierst aus Tagebuchnotizen einer stationären Psychotherapie (SNS-Prozess-"
        "monitoring) je Tag den Tenor und konkrete Ereignisse. Antworte AUSSCHLIESSLICH mit "
        "JSON {\"tage\": [{\"datum\": \"YYYY-MM-DD\", \"tenor\": ..., \"ereignisse\": "
        "[{\"kategorie\": ..., \"ism_faktor\": <0-5 oder null>, \"kurz\": ..., \"zitat\": ...}]}]}.",
        f"\nTENOR: einer von {', '.join(TENOR)}.",
        f"KATEGORIEN: {kat}.",
        "REGELN:\n- Für JEDEN gelieferten Tag genau einen Eintrag mit demselben Datum.\n"
        "- 'zitat' ist ein WÖRTLICHER Ausschnitt (max. 200 Zeichen) aus dem Text des Tages - "
        "nichts umformulieren, nichts ergänzen. 'kurz' fasst das Ereignis in max. 140 Zeichen.\n"
        "- Nur Ereignisse, die im Text stehen. Keine Deutung, keine Diagnosen.\n"
        "- 'medikation' bei jeder Erwähnung von Medikamenten/Dosis; 'somatik' bei körperlichen "
        "Beschwerden; 'autonomie_erfahrung' bei Abgrenzung/Nein-Sagen/eigenen Entscheidungen; "
        "'therapie_intervention' bei Gruppen-/Einzelsitzungen, Rollenspielen, Übungen.\n"
        "- Tage ohne Ereignis: 'ereignisse': [] und Tenor nach Text.",
    ]
    if ism_items:
        lst = "\n".join(f"- Faktor {it.get('faktor_ids', [None])[0] if it.get('faktor_ids') else '?'} "
                        f"({it.get('faktor') or '?'}): {it['titel']}" for it in ism_items)
        parts.append(
            "\nINDIVIDUELLER FRAGEBOGEN (ism_faktor): Setze 'ism_faktor' auf die Faktor-Id, wenn "
            "der Kommentar erkennbar auf eines dieser Items antwortet (Kommentare sind oft nach "
            "Items gegliedert, z.B. 'Ängste, Selbstzweifel: ...'). Sonst null.\n" + lst
        )
    else:
        parts.append("\n'ism_faktor' immer null (kein individueller Fragebogen).")
    return "\n".join(parts)


def build_stage_a_user(batch: list[dict]) -> str:
    lines = []
    for e in batch:
        lines.append(f"### {e['datum']}")
        if e.get("tagebuch"):
            lines.append("Tagebuch: " + e["tagebuch"])
        if e.get("kommentar"):
            lines.append("Kommentar zum Kernanliegen: " + e["kommentar"])
    return "\n".join(lines)


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def parse_stage_a(data: Any, batch: list[dict], besetzte_faktoren: set[int]) -> list[dict]:
    """Postprocessing: Zitat muss Substring der Quelle sein, ism_faktor besetzt."""
    quelle = {e["datum"]: _norm_ws((e.get("tagebuch") or "") + " " + (e.get("kommentar") or ""))
              for e in batch}
    out: list[dict] = []
    tage = (data or {}).get("tage", []) if isinstance(data, dict) else []
    by_date = {}
    for t in tage:
        if isinstance(t, dict) and isinstance(t.get("datum"), str):
            by_date[t["datum"][:10]] = t
    for e in batch:
        d = e["datum"]
        t = by_date.get(d)
        tenor = t.get("tenor") if t else None
        if tenor not in TENOR:
            tenor = "neutral/plateau"
        ereignisse = []
        verworfen = 0
        for ev in (t.get("ereignisse") or []) if t else []:
            if not isinstance(ev, dict):
                continue
            zitat = str(ev.get("zitat") or "").strip()
            if not zitat or _norm_ws(zitat) not in quelle[d]:
                verworfen += 1
                continue
            kat = ev.get("kategorie") if ev.get("kategorie") in KATEGORIEN else "sonstiges"
            fid = ev.get("ism_faktor")
            fid = int(fid) if isinstance(fid, int) and fid in besetzte_faktoren else None
            ereignisse.append({"datum": d, "kategorie": kat, "ism_faktor": fid,
                               "kurz": str(ev.get("kurz") or "")[:140], "zitat": zitat[:200]})
        out.append({"datum": d, "tenor": tenor, "ereignisse": ereignisse, "verworfen": verworfen})
    return out


async def run_stage_a(entries: list[dict], ism_items: list[dict] | None, besetzte_faktoren: set[int],
                      *, generate: GeneratorFn, model: str | None, batch_size: int = STAGE_A_BATCH,
                      max_tokens: int = 2500, on_batch: Callable[[int, int], None] | None = None,
                      ) -> list[dict]:
    system = build_stage_a_system_prompt(ism_items)
    schema = build_stage_a_schema()
    out: list[dict] = []
    batches = [entries[i:i + batch_size] for i in range(0, len(entries), batch_size)]
    for k, batch in enumerate(batches):
        res = await generate(system, build_stage_a_user(batch), max_tokens=max_tokens, model=model,
                             workflow=WORKFLOW, response_format=schema,
                             temperature_override=STAGE_A_TEMPERATURE)
        data = res.get("structured_data")
        if data is None:
            logger.warning("sns_llm: Stage A Batch %d ohne valides JSON - Tage ohne Ereignisse", k)
        out.extend(parse_stage_a(data, batch, besetzte_faktoren))
        if on_batch:
            on_batch(k + 1, len(batches))
    return out


def stage_a_events(stage_a: list[dict]) -> list[dict]:
    return [ev for t in stage_a for ev in t["ereignisse"]]


# ═════════════════════════════════════════════════════════════════════════════
# Flags nach Stage A
# ═════════════════════════════════════════════════════════════════════════════

def flags_nach_stage_a(a: SnsAnalyse, stage_a: list[dict], quelle_entries: list[dict]) -> dict:
    """Ergaenzt die Flags aus der Analyse um die Stage-A-abhaengigen (Spec 4.10)
    und die Suizidalitaets-Warnung (F6). Rueckgabe {flags, details}."""
    from app.services.suizidalitaet import mentions_nssv, mentions_suizidalitaet

    flags = set(a.flags)
    details: dict[str, Any] = {}
    events = stage_a_events(stage_a)
    tenor = {t["datum"]: t["tenor"] for t in stage_a}
    f = a.fakten

    # MEDIKATION_IM_UEBERGANGSFENSTER: Ereignis 'medikation' +-2 Tage um einen Uebergang
    med_tage = [ev["datum"] for ev in events if ev["kategorie"] == "medikation"]
    ueb = [dt.date.fromisoformat(u["datum"]) for u in f["uebergaenge"]]
    treffer = [d for d in med_tage if any(abs((dt.date.fromisoformat(d) - u).days) <= 2 for u in ueb)]
    if treffer:
        flags.add("MEDIKATION_IM_UEBERGANGSFENSTER")
        details["medikation"] = sorted(set(treffer))

    # SOMATIK_NEU: 'somatik' in den letzten 7 Tagen
    ende = a.days[-1]
    som = [ev["datum"] for ev in events if ev["kategorie"] == "somatik"
           and (ende - dt.date.fromisoformat(ev["datum"])).days <= 7]
    if som:
        flags.add("SOMATIK_NEU")
        details["somatik"] = sorted(set(som))

    # PLATEAU_ALS_EINBRUCH: ereignisbezogenes Faktor-I-Item + negative z an 'neutral/plateau'-Tagen
    ism = f.get("ism")
    if ism and any(it.get("ereignisbezogen") and it.get("faktor") == "I" for it in ism["items"]):
        fI = next((x for x in a.ism_faktoren if x["id"] == 0 and x["besetzt"]), None)
        if fI is not None and fI["z"] is not None:
            plateau = [d.isoformat() for d, z in zip(a.days, fI["z"], strict=True)
                       if z == z and z < 0 and tenor.get(d.isoformat()) == "neutral/plateau"]
            if len(plateau) >= 2:
                flags.add("PLATEAU_ALS_EINBRUCH")
                details["plateau"] = plateau

    # Suizidalitaet / NSSV in der Quelle (kein Standardsatz - nur Warnung + Pflicht fuer Stage B)
    suizid = [e["datum"] for e in quelle_entries
              if mentions_suizidalitaet((e.get("tagebuch") or "") + " " + (e.get("kommentar") or ""))]
    nssv = [e["datum"] for e in quelle_entries
            if mentions_nssv((e.get("tagebuch") or "") + " " + (e.get("kommentar") or ""))]
    if suizid or nssv:
        flags.add("SUIZIDALITAET_IN_QUELLE")
        details["suizidalitaet"] = {"suizidalitaet": suizid, "nssv": nssv}

    return {"flags": sorted(flags), "details": details}


# ═════════════════════════════════════════════════════════════════════════════
# Stage B - Interpretation
# ═════════════════════════════════════════════════════════════════════════════

def _fmt(v, nd=1, suffix=""):
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.{nd}f}{suffix}".replace(".", ",")
    return f"{v}{suffix}"


def _d(iso: str | None) -> str:
    if not iso:
        return "–"
    d = dt.date.fromisoformat(iso)
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def build_faktenblock(fakten: dict, stage_a: list[dict], flags: dict, anrede: str, kuerzel: str) -> str:
    """Lesbarer Faktenblock fuer Stage B - JEDE Zahl im Bericht muss hier stehen."""
    f = fakten
    L: list[str] = []
    L.append(f"KLIENT:IN: {anrede} ({kuerzel}). Erste Nennung im Text: \"{anrede.lower()} ({kuerzel})\", danach \"{anrede.lower()}\".")
    z = f["zeitraum"]
    L.append(f"ZEITRAUM: {_d(z['start'])} bis {_d(z['ende'])} ({z['tage']} Kalendertage, "
             f"{z['messtage_hsf']} HSF-Messtage, {z['messtage_ind']} Messtage individueller Bogen, "
             f"{z['tagebucheintraege']} Tagebucheinträge).")
    lk = f["luecken"]
    if lk["hsf_x"] or lk["hsf_fehlend"]:
        L.append("LÜCKEN HSF (fehlend/übertragen): " + ", ".join(_d(x) for x in sorted(set(lk["hsf_x"] + lk["hsf_fehlend"]))))

    L.append("\nHSF-FAKTOREN (Mittel erste 3 / letzte 3 Messtage, Kendall τ; − = hoch bedeutet Belastung):")
    for x in f["hsf"]["faktoren"]:
        pm = ", ".join(f"{k} {_fmt(v)}" for k, v in x["phasenmittel"].items())
        L.append(f"- {x['name']} {x['kurz']} [{x['polung']}]: Anfang {_fmt(x['anfang'])}, Ende {_fmt(x['ende'])}, "
                 f"Δ {_fmt(x['delta'])}, τ {_fmt(x['tau'], 2)}; Phasenmittel {pm}; Items: {', '.join(x['items'])}")
    k = f["hsf"]["komposit"]
    L.append(f"RESSOURCEN-KOMPOSIT ({len(k['items'])} Items): Anfang {_fmt(k['anfang'])}, Ende {_fmt(k['ende'])}, "
             f"Δ {_fmt(k['delta'])}, τ {_fmt(k['tau'], 2)}; Phasenmittel "
             + ", ".join(f"{a} {_fmt(b)}" for a, b in k["phasenmittel"].items()))
    if f["hsf"]["konstante_items"]:
        L.append("KONSTANTE ITEMS (SD ≤ 1, ohne Verlaufsinformation): " + ", ".join(f["hsf"]["konstante_items"]))
    L.append("HSF-ITEMS Anfang → Ende: " + "; ".join(
        f"{r['kurz']} {_fmt(r['anfang'])}→{_fmt(r['ende'])} (τ {_fmt(r['tau'], 2)})" for r in f["hsf"]["items"]))

    ism = f.get("ism")
    if ism:
        q = {"xml": "aus dem Fragebogen-XML", None: "nicht möglich (kein XML)"}.get(ism["quelle"], "aus dem Fragebogen-XML")
        L.append(f"\nINDIVIDUELLER FRAGEBOGEN: {len(ism['items'])} Items, Faktorzuordnung {q}.")
        for it in ism["items"]:
            L.append(f"- Item ({it['faktor'] or 'ohne Faktor'}): \"{it['titel']}\" [{it['polung']}]"
                     + (" – ereignisbezogen formuliert" if it.get("ereignisbezogen") else ""))
        if ism["faktoren"]:
            L.append("ISM-FAKTOREN (0–100; Phasenmittel; Sprung am Übergang = Mittel 4 Tage danach − 4 Tage davor; "
                     "Krisenminimum; Endniveau):")
            for x in ism["faktoren"]:
                if not x["besetzt"]:
                    L.append(f"- {x['name']}: UNBESETZT (kein Item)")
                    continue
                pm = ", ".join(f"{a} {_fmt(b)}" for a, b in x["phasenmittel"].items())
                L.append(f"- {x['name']}: Phasenmittel {pm}; Sprung {_fmt(x['sprung_uebergang'])}; "
                         f"Krisenminimum {_fmt(x['krisen_minimum'])}; Endniveau {_fmt(x['endniveau'])}; τ {_fmt(x['tau'], 2)}")
            L.append(f"Tragende Faktoren (Sprung-Ranking): {', '.join(ism['tragende_faktoren']) or '–'}; "
                     f"Anker in der Krise: {ism['anker_faktor'] or '–'}; langsamster Faktor (Endniveau): "
                     f"{ism['langsamster_faktor'] or '–'}; unbesetzt: {', '.join(ism['unbesetzt']) or '–'}.")
        ns = ism.get("faktor_I_negative_serie")
        if ns:
            L.append(f"Faktor I (z wie SNS): längste negative Serie {_d(ns['start'])}–{_d(ns['ende'])} ({ns['tage']} Tage).")
        L.append("Items individuell Anfang → Ende: " + "; ".join(
            f"{r['kurz']} {_fmt(r['anfang'])}→{_fmt(r['ende'])}" for r in f["ind_items"]))
    else:
        L.append("\nINDIVIDUELLER FRAGEBOGEN: nicht vorhanden (Abschnitt 4 entfällt; nur Satz dazu).")

    dk = f["dk"]
    L.append(f"\nDYNAMISCHE KOMPLEXITÄT (Fenster {dk['fenster']} Tage, {len(dk['items_variierend'])} variierende Items):")
    if dk["dk_mittel_max"]:
        L.append(f"- Maximum DK-Mittel am {_d(dk['dk_mittel_max']['datum'])} ({_fmt(dk['dk_mittel_max']['wert'], 3)}); "
                 f"P75 {_fmt(dk['p75'], 3)}; Phasen-DK " + ", ".join(f"{a} {_fmt(b, 3)}" for a, b in dk["phasen_dk_mittel"].items()))
        L.append(f"- Resonanz-Maximum am {_d(dk['resonanz_max']['datum'])}: {dk['resonanz_max']['anzahl']} kritische Items "
                 f"({', '.join(dk['resonanz_max']['items'][:8])})")
        if dk["kritische_tage"]:
            L.append("- Kritische Häufungen (≥ 2 Items über dem 95-%-Konfidenzintervall): "
                     + ", ".join(f"{_d(t['datum'])} ({t['anzahl']})" for t in dk["kritische_tage"]))
    else:
        L.append("- nicht berechenbar (zu kurze Reihe)")

    L.append("\nÜBERGÄNGE (Sprung im Komposit, Mittel 4 Tage danach − 4 Tage davor):")
    if f["uebergaenge"]:
        for u in f["uebergaenge"]:
            v = u["vorlaeufer"]
            vs = (f"Vorläufer DK-Gipfel {_d(v['datum'])} ({_fmt(v['dk'], 3)}, {v['resonanz']} kritische Items, "
                  f"{'kritisch' if v['kritisch'] else 'nicht kritisch'})") if v else "kein DK-Vorläufer (Fenster noch leer)"
            L.append(f"- {_d(u['datum'])}: {u['typ'].replace('ordnungsuebergang', 'ORDNUNGSÜBERGANG').replace('niveauverschiebung', 'Niveauverschiebung')} "
                     f"(+{_fmt(u['shift'])}); {vs}")
    else:
        L.append("- keiner erkannt")
    L.append("ORDNUNGSÜBERGANG (Hauptdatum): " + (_d(f["ordnungsuebergang"]) if f["ordnungsuebergang"] else "keiner"))

    L.append("\nPHASEN (deterministisch; du vergibst nur Namen):")
    for ph in f["phasen"]:
        L.append(f"- {ph['label']} {_d(ph['start'])}–{_d(ph['ende'])} ({ph['tage']} Tage, Typ {ph['typ']}): "
                 f"Komposit {_fmt(ph['komposit_mittel'])}, DK {_fmt(ph['dk_mittel'], 3)}, max. Resonanz {ph['resonanz_max']}")

    L.append("\nEINBRÜCHE (Komposit ≤ Median der 5 Vortage − 10; Dauer bis Erholung):")
    if f["einbrueche"]:
        for e in f["einbrueche"]:
            L.append(f"- {_d(e['datum'])}: Tiefe {_fmt(e['tiefe'])}, Dauer {e['dauer'] if e['dauer'] is not None else 'bis Ende offen'} Tage")
        vn = f["einbrueche_vor_nach"]
        if vn["vor"]:
            L.append(f"- vor dem Übergang: {vn['vor']['anzahl']} Einbrüche, mittlere Dauer {_fmt(vn['vor']['dauer_mittel'])}; "
                     f"danach: {vn['nach']['anzahl']} Einbrüche, mittlere Dauer {_fmt(vn['nach']['dauer_mittel'])}")
    else:
        L.append("- keine")

    r = f["recurrence"]
    L.append(f"\nRECURRENCE (mittlere Distanz der Tagesprofile, Block {r['blocklaenge']} Tage): Anfangsblock {_fmt(r['block_anfang'])}, "
             f"Endblock {_fmt(r['block_ende'])}, Anfang↔Ende {_fmt(r['anfang_ende'])}.")
    if r["einbruch_distanz"]:
        L.append("- Einbruchstage: " + "; ".join(f"{_d(e['datum'])} zu Anfang {_fmt(e['zu_anfang'])} / zu Ende {_fmt(e['zu_ende'])}"
                                                 for e in r["einbruch_distanz"]))
    if f["kopplung"]:
        L.append("KOPPLUNGEN (Pearson r, stärkste): " + "; ".join(
            f"{k['a']} × {k['b']} r {_fmt(k['r'], 2)}" for k in f["kopplung"][:5]))
    kor = [c for c in f["polung_check"] if c.get("korrigiert")]
    if kor:
        L.append("POLUNG AUTOMATISCH KORRIGIERT (Item ressourcenseitig formuliert, im XML als Belastung "
                 "angelegt; die Werte oben sind bereits korrigiert): " + "; ".join(
                     f"{c['item']} (r vorher {_fmt(c['r_vor_korrektur'], 2)})" for c in kor)
                 + " – im Abschnitt 2 in einem Satz benennen und für die Fortsetzung des Bogens anregen, "
                   "die Polung im SNS anzupassen.")
    pc = [c for c in f["polung_check"] if c["fraglich"]]
    if pc:
        L.append("POLUNG FRAGLICH (r mit Komposit entgegen der Annahme): " + "; ".join(f"{c['item']} r {_fmt(c['r'], 2)}" for c in pc))

    L.append("\nFLAGS (zwingend aufgreifen): " + (", ".join(flags["flags"]) or "keine"))
    det = flags.get("details", {})
    if "medikation" in det:
        L.append("- Medikation im Übergangsfenster an: " + ", ".join(_d(x) for x in det["medikation"]))
    if "somatik" in det:
        L.append("- Neue Somatik in den letzten 7 Tagen an: " + ", ".join(_d(x) for x in det["somatik"]))
    if "plateau" in det:
        L.append("- Plateau-Tage mit negativem Zielerleben: " + ", ".join(_d(x) for x in det["plateau"]))
    if "suizidalitaet" in det:
        s = det["suizidalitaet"]
        L.append("- Tagebuch erwähnt Suizidalität/Selbstverletzung an: "
                 + ", ".join(_d(x) for x in sorted(set(s["suizidalitaet"] + s["nssv"])))
                 + " – in Abschnitt 9 benennen (Hinweis an Behandler:innen, keine Einschätzung aus Fragebogendaten).")
    if f.get("hinweise"):
        L.append("HINWEISE: " + " | ".join(f["hinweise"]))

    L.append("\nEREIGNISSE AUS DEM TAGEBUCH (Stage A; Zitate nur von hier, wörtlich):")
    n = 0
    for t in stage_a:
        for ev in t["ereignisse"]:
            n += 1
            fak = f" [ISM-Faktor {ISM_ROEMISCH.get(ev['ism_faktor'], '')}]" if ev.get("ism_faktor") is not None else ""
            L.append(f"- E{n} {_d(ev['datum'])} ({ev['kategorie']}{fak}): {ev['kurz']} – Zitat: \"{ev['zitat']}\"")
    if n == 0:
        L.append("- keine Ereignisse extrahiert")
    L.append("TENOR JE TAG: " + ", ".join(f"{_d(t['datum'])} {t['tenor']}" for t in stage_a))
    return "\n".join(L)


def build_stage_b_system_prompt(workflow_instructions: Optional[str], word_limits: tuple[int, int]) -> str:
    from app.services.prompts import BASE_PROMPTS, WORKFLOW_INSTRUCTIONS_DEFAULT
    instructions = (workflow_instructions.strip() if workflow_instructions and workflow_instructions.strip()
                    else WORKFLOW_INSTRUCTIONS_DEFAULT.get(WORKFLOW, ""))
    kernel = BASE_PROMPTS.get(WORKFLOW, "")
    lo, hi = word_limits
    return "\n\n".join([
        "Du bist Prozessdiagnostik-Assistent der sysTelios Klinik für Psychosomatik und Psychotherapie. "
        "Du schreibst aus einem deterministisch berechneten Faktenblock die Verlaufsauswertung eines "
        "SNS-Prozessmonitorings (Synergetisches Navigationssystem, idiographische Systemmodellierung).",
        "AUFTRAG / INHALTLICHE ANWEISUNGEN:\n" + instructions,
        kernel,
        f"ZIELLÄNGE: {lo}–{hi} Wörter (alle neun Abschnitte zusammen).",
        "Schreibe jetzt die Verlaufsauswertung. Beginne direkt mit der Überschrift \"1. Zusammenfassung\".",
    ])


def build_stage_b_user(faktenblock: str) -> str:
    return "FAKTENBLOCK:\n" + faktenblock + "\n\nSchreibe die neun Abschnitte in der vorgegebenen Reihenfolge."


def sections_present(text: str) -> list[str]:
    """Fehlende Abschnittsueberschriften (Nummer + Kernwort reicht)."""
    missing = []
    for h in ABSCHNITTE:
        num, rest = h.split(". ", 1)
        kern = rest.split(":")[0].split(" und ")[0].strip()
        if not re.search(rf"(^|\n)\s*#*\s*{num}\.?\s+.*{re.escape(kern.split()[0])}", text, re.I):
            missing.append(h)
    return missing


async def run_stage_b(faktenblock: str, *, generate: GeneratorFn, model: str | None,
                      workflow_instructions: Optional[str], word_limits: tuple[int, int],
                      max_tokens: int, on_progress=None) -> dict:
    system = build_stage_b_system_prompt(workflow_instructions, word_limits)
    res = await generate(system, build_stage_b_user(faktenblock), max_tokens=max_tokens, model=model,
                         workflow=WORKFLOW, on_progress=on_progress, max_words=word_limits[1])
    return res


def phasen_namen_anwenden(text: str, phasen: list[dict]) -> list[dict]:
    """Liest 'P1 – Name' / 'P1: Name' / 'Phase 1 (Name)' aus Abschnitt 7 und traegt
    die Namen in die Phasenliste ein (LLM vergibt nur Namen, D5=A)."""
    out = []
    m = re.search(r"7\.\s*Phasen(.*?)(\n\s*#*\s*8\.|\Z)", text, re.S | re.I)
    seg = m.group(1) if m else text
    for ph in phasen:
        name = None
        pat = rf"(?:\b{ph['label']}\b|Phase\s+{ph['id']}\b)\s*[\(:–\-]\s*[„\"]?([^\n„\"\)\.:;–]{{3,60}})"
        mm = re.search(pat, seg)
        if mm:
            name = mm.group(1).strip(" \"„“")
        out.append({**ph, "name": name})
    return out
