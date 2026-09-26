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
# Stage A meldet Personennamen ohne Anrede mit Rolle (v19.41.3); ersetzt wird deterministisch.
PERSONEN_ROLLEN = ("Mitklient:in", "Therapeut:in", "Partner:in", "Kind", "Tochter", "Sohn",
                   "Mutter", "Vater", "Geschwister", "Angehörige:r", "Freund:in", "Kolleg:in",
                   "andere Person")
_SUBSTANTIV_ENDUNG = re.compile(r"(ung|heit|keit|schaft|ion|tät|nis|ismus|ment|ling|tum|ungen|heiten|keiten)$")

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
# v19.41.3: nur "mein*" + Rolle ist case-insensitiv, der Name MUSS gross beginnen
# (vorher re.I auf alles -> "meine Mama mich ..." machte "mich" zum Namen).
_ROLLE_RE = re.compile(
    r"\b((?i:mein(?:e|em|en|er|es)?))\s+((?i:" + "|".join(sorted(_ROLLEN, key=len, reverse=True)) + r"))\s+"
    r"([A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?)\b",
)
# O2=B (v19.41.3): Namen mit Anrede/Titel sind im Klinikalltag Behandler:innen und
# werden durch die Rolle ersetzt ("bei Frau S." -> "bei der Therapeutin").
_BEHANDLER_RE = re.compile(
    r"\b(?:((?i:mit|bei|von|vom|zu|zur|zum|nach|aus|seit|gegenüber|für|durch|gegen|ohne|um|an|auf|in))\s+)?"
    r"(Frau|Herrn|Herr|Dr\.|Dr|Prof\.|Prof)\s+(?:(?:Dr|Prof)\.?\s+)?"
    r"([A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?)\b"
)
_DATIV = {"mit", "bei", "von", "vom", "zu", "zur", "zum", "nach", "aus", "seit", "gegenüber"}
_AKKUSATIV = {"für", "durch", "gegen", "ohne", "um"}
_BEHANDLER_FORM = {
    # Anrede: (Nominativ, Dativ, Akkusativ, Rolle ohne Artikel)
    "w": ("die Therapeutin", "der Therapeutin", "die Therapeutin", "Therapeutin"),
    "m": ("der Therapeut", "dem Therapeuten", "den Therapeuten", "Therapeut"),
    "?": ("die Behandler:in", "der Behandler:in", "die Behandler:in", "Behandler:in"),
}
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
    (t) Namen mit Anrede/Titel ('Frau S.', 'Herrn X', 'Dr. Y') -> Rolle
        ('die Therapeutin' / 'der Therapeut' / 'die Behandler:in', kasusgerecht
        nach Praeposition) - O2=B, v19.41.3
    Namen ohne Anrede/Rollenkontext (Mitklient:innen) erkennt Stage A (Feld
    'personen'); ersetzt werden sie danach mit namen_ersetzen(). Die fruehere
    Heuristik 'Grosswort nach mit/bei/und' traf im Deutschen jedes Substantiv.
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

    def _t(text: str) -> str:
        def rep(m):
            praep, titel, name = m.group(1), m.group(2), m.group(3)
            if name in _NICHT_NAMEN or name.lower() in _ROLLEN:
                return m.group(0)
            g = "w" if titel == "Frau" else ("m" if titel.startswith("Herr") else "?")
            nom, dat, akk, rolle = _BEHANDLER_FORM[g]
            namen.add(name)
            rollen_namen[name] = rolle
            if not praep:
                satzanfang = re.search(r"(^|[.!?:]\s*|\n\s*)$", m.string[:m.start()])
                return nom[0].upper() + nom[1:] if satzanfang else nom
            p = praep.lower()
            if p in ("vom", "zum", "zur"):          # Kontraktion aufloesen: "zur Frau S." -> "zu der Therapeutin"
                praep, p = praep[:2] + {"vom": "n", "zum": "", "zur": ""}[p], "dativ"
            form = dat if (p in _DATIV or p == "dativ") else (akk if p in _AKKUSATIV else nom)
            return f"{praep} {form}"
        return _BEHANDLER_RE.sub(rep, text)

    def _rest(text: str) -> str:
        # Bereits erkannte Namen auch ausserhalb der Kontexte ersetzen
        for name in sorted(namen, key=len, reverse=True):
            if name == vn:
                continue
            text = re.sub(rf"\b{re.escape(name)}\b", rollen_namen.get(name, "Mitklient:in"), text)
        return text

    out = []
    for e in entries:
        t, k = e.get("tagebuch", "") or "", e.get("kommentar", "") or ""
        t, k = _a(t), _a(k)
        t, k = _b(t), _b(k)
        t, k = _t(t), _t(k)
        out.append({**e, "tagebuch": t, "kommentar": k})
    out = [{**e, "tagebuch": _rest(e["tagebuch"]), "kommentar": _rest(e["kommentar"])} for e in out]
    return out, sorted(namen)


# ═════════════════════════════════════════════════════════════════════════════
# Stage A - Tagebuch-Extraktion
# ═════════════════════════════════════════════════════════════════════════════

def build_stage_a_schema() -> dict:
    return {
        "type": "object", "required": ["tage", "personen"],
        "properties": {
            "personen": {"type": "array", "items": {
                "type": "object", "required": ["name", "rolle"],
                "properties": {"name": {"type": "string"}, "rolle": {"enum": list(PERSONEN_ROLLEN)}}}},
            "tage": {"type": "array", "items": {
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
        "JSON {\"personen\": [{\"name\": ..., \"rolle\": ...}], \"tage\": [{\"datum\": \"YYYY-MM-DD\", "
        "\"tenor\": ..., \"ereignisse\": [{\"kategorie\": ..., \"ism_faktor\": <0-5 oder null>, "
        "\"kurz\": ..., \"zitat\": ...}]}]}.",
        f"\nPERSONEN: Liste JEDEN Vor- oder Nachnamen einer Person, der im Text vorkommt (Mitklient:innen, "
        f"Angehörige, Behandler:innen, Freund:innen), genau so geschrieben wie im Text, mit Rolle aus: "
        f"{', '.join(PERSONEN_ROLLEN)}. Keine Substantive, Orte, Therapieformen oder Rollenwörter "
        "(\"Partner\", \"Therapeutin\") - nur Eigennamen. Wenn keine: [].",
        f"\nTENOR: einer von {', '.join(TENOR)}.",
        f"KATEGORIEN: {kat}.",
        "REGELN:\n- Für JEDEN gelieferten Tag genau einen Eintrag mit demselben Datum.\n"
        "- 'zitat' ist ein WÖRTLICHER Ausschnitt (max. 200 Zeichen) aus dem Text des Tages - "
        "nichts umformulieren, nichts ergänzen. 'kurz' fasst das Ereignis in max. 140 Zeichen.\n"
        "- Nur Ereignisse, die im Text stehen. Keine Deutung, keine Diagnosen.\n"
        "- In 'kurz' keine Namen, sondern Rollen (Mitklientin, Partner, Therapeutin).\n"
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


def parse_personen(data: Any, batch: list[dict]) -> dict[str, str]:
    """{name: rolle} aus Stage A - nur Namen, die gross geschrieben woertlich in der
    Quelle stehen, kein bekanntes Substantiv/Rollenwort sind."""
    quelle = " ".join((e.get("tagebuch") or "") + " " + (e.get("kommentar") or "") for e in batch)
    out: dict[str, str] = {}
    for pz in (data or {}).get("personen", []) if isinstance(data, dict) else []:
        if not isinstance(pz, dict):
            continue
        for name in str(pz.get("name") or "").split():
            name = name.strip(".,;:!?()\"„“'")
            if (len(name) < 2 or not name[0].isupper() or name in _NICHT_NAMEN
                    or name.lower() in _ROLLEN or _SUBSTANTIV_ENDUNG.search(name.lower())
                    or name in ("Mitklient:in", "Therapeut:in", "Klientin", "Klient", "Kürzel")
                    or not re.search(rf"(?<![\wÄÖÜäöüß]){re.escape(name)}s?(?![\wÄÖÜäöüß])", quelle)
                    or re.search(rf"\b(?i:der|die|das|den|dem|des|ein|eine|einen|einem|einer|keine|meine|viel|mehr)"
                                 rf"\s+{re.escape(name)}\b", quelle)):
                continue
            rolle = pz.get("rolle") if pz.get("rolle") in PERSONEN_ROLLEN else "andere Person"
            out[name] = rolle
    return out


def namen_ersetzen(entries: list[dict], stage_a: list[dict], personen: dict[str, str]
                   ) -> tuple[list[dict], list[dict]]:
    """Ersetzt die von Stage A gemeldeten Namen in Quelle UND Ereignissen gleich
    (Zitate bleiben so Substring der Quelle). 'andere Person' -> 'Person'."""
    if not personen:
        return entries, stage_a
    ersatz = {n: ("Person" if r == "andere Person" else r) for n, r in personen.items()}
    pat = re.compile(r"(?<![\wÄÖÜäöüß])(" + "|".join(re.escape(n) for n in sorted(ersatz, key=len, reverse=True))
                     + r")(?:s)?(?![\wÄÖÜäöüß])")

    def sub(t: str) -> str:
        return pat.sub(lambda m: ersatz[m.group(1)], t or "")

    ent = [{**e, "tagebuch": sub(e.get("tagebuch", "")), "kommentar": sub(e.get("kommentar", ""))} for e in entries]
    quelle = {e["datum"]: _norm_ws(e["tagebuch"] + " " + e["kommentar"]) for e in ent}
    sa = []
    for t in stage_a:
        evs = [{**ev, "kurz": sub(ev["kurz"]), "zitat": sub(ev["zitat"])} for ev in t["ereignisse"]]
        ok = [ev for ev in evs if _norm_ws(ev["zitat"]) in quelle.get(t["datum"], "")]
        sa.append({**t, "ereignisse": ok, "verworfen": int(t.get("verworfen") or 0) + len(evs) - len(ok)})
    return ent, sa


async def run_stage_a(entries: list[dict], ism_items: list[dict] | None, besetzte_faktoren: set[int],
                      *, generate: GeneratorFn, model: str | None, batch_size: int = STAGE_A_BATCH,
                      max_tokens: int = 2500, on_batch: Callable[[int, int], None] | None = None,
                      personen: dict[str, str] | None = None) -> list[dict]:
    """Tage mit Ereignissen; gefundene Personennamen landen in `personen` (wenn uebergeben)."""
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
        if personen is not None:
            personen.update(parse_personen(data, batch))
        if on_batch:
            on_batch(k + 1, len(batches))
    return out


def stage_a_events(stage_a: list[dict]) -> list[dict]:
    return [ev for t in stage_a for ev in t["ereignisse"]]


# ═════════════════════════════════════════════════════════════════════════════
# Flags nach Stage A
# ═════════════════════════════════════════════════════════════════════════════

_MED_RE = re.compile(
    r"\b(Medikament\w*|Medikation|Tablette\w*|Dosis|Dosierung|Psychopharm\w*|Antidepressiv\w*|"
    r"\d+(?:[.,]\d+)?\s?mg\b|Sertralin|Escitalopram|Citalopram|Fluoxetin|Paroxetin|Venlafaxin|"
    r"Duloxetin|Mirtazapin|Bupropion|Agomelatin|Quetiapin|Olanzapin|Aripiprazol|Risperidon|Lithium|"
    r"Lamotrigin|Pregabalin|Opipramol|Amitriptylin|Trimipramin|Doxepin|Lorazepam|Tavor|Diazepam|"
    r"Zopiclon|Zolpidem|Melperon|Pipamperon|Promethazin|Methylphenidat|Lisdexamfetamin)", re.I)
_SATZGRENZE_RE = re.compile(r"[.!?;\n]|\s[-–]\s")   # Tagebuecher trennen oft mit " - "
_AB_MORGEN_RE = re.compile(r"\bab morgen\b|\bab dem morgigen\b|\bmorgen (?:wird|werde|bekomme|soll|gibt)", re.I)


def medikation_stichworte(entries: list[dict]) -> list[dict]:
    """Deterministische Medikationserkennung (v19.41.3) - je Tag der erste Satz mit
    Stichwort. 'ab morgen' -> wirksam ab Folgetag. Zitat = woertlicher Satz (max. 200)."""
    out = []
    for e in entries:
        for fld in ("tagebuch", "kommentar"):
            text = e.get(fld) or ""
            m = _MED_RE.search(text)
            if not m:
                continue
            grenzen = [(g.start(), g.end()) for g in _SATZGRENZE_RE.finditer(text)]
            a = max([e_ for s_, e_ in grenzen if e_ <= m.start()], default=0)
            b = min([s_ + (1 if text[s_] in ".!?" else 0) for s_, e_ in grenzen if s_ >= m.end()],
                    default=len(text))
            satz = text[a:b].strip()
            if len(satz) > 200:
                k = satz.find(m.group(0))
                satz = satz[max(0, k - 80):max(0, k - 80) + 200].strip()
            d = dt.date.fromisoformat(e["datum"])
            ab = d + dt.timedelta(1) if _AB_MORGEN_RE.search(text[a:b]) else d
            out.append({"datum": e["datum"], "wirksam_ab": ab.isoformat(), "zitat": satz,
                        "stichwort": m.group(0)})
            break
    return out


def flags_nach_stage_a(a: SnsAnalyse, stage_a: list[dict], quelle_entries: list[dict]) -> dict:
    """Ergaenzt die Flags aus der Analyse um die Stage-A-abhaengigen (Spec 4.10)
    und die Suizidalitaets-Warnung (F6). Rueckgabe {flags, details}."""
    from app.services.suizidalitaet import mentions_nssv, mentions_suizidalitaet

    flags = set(a.flags)
    details: dict[str, Any] = {}
    events = stage_a_events(stage_a)
    tenor = {t["datum"]: t["tenor"] for t in stage_a}
    f = a.fakten

    # MEDIKATION_IM_UEBERGANGSFENSTER: Stichwort (deterministisch) oder Stage-A-Kategorie
    # 'medikation', Tag der Nennung ODER Wirksamkeitstag ("ab morgen") +-2 Tage um einen Uebergang
    med = {m["datum"]: m for m in medikation_stichworte(quelle_entries)}
    for ev in events:
        if ev["kategorie"] == "medikation" and ev["datum"] not in med:
            med[ev["datum"]] = {"datum": ev["datum"], "wirksam_ab": ev["datum"], "zitat": ev["zitat"],
                                "stichwort": "Stage A"}
    ueb = [(dt.date.fromisoformat(u["datum"]), u) for u in f["uebergaenge"]]
    alle = []
    for m in sorted(med.values(), key=lambda x: x["datum"]):
        tage = {dt.date.fromisoformat(m["datum"]), dt.date.fromisoformat(m["wirksam_ab"])}
        nah = [(abs((t - ud).days), u) for t in tage for ud, u in ueb if abs((t - ud).days) <= 2]
        naechster = min(nah, key=lambda x: x[0])[1] if nah else None
        alle.append({**m, "uebergang": naechster["datum"] if naechster else None,
                     "uebergang_typ": naechster["typ"] if naechster else None})
    if alle:
        details["medikation_alle"] = alle
    treffer = [m for m in alle if m["uebergang"]]
    if treffer:
        flags.add("MEDIKATION_IM_UEBERGANGSFENSTER")
        details["medikation"] = treffer

    # SOMATIK_NEU: 'somatik' in den letzten 7 Tagen
    ende = a.days[-1]
    som = [ev["datum"] for ev in events if ev["kategorie"] == "somatik"
           and (ende - dt.date.fromisoformat(ev["datum"])).days <= 7]
    if som:
        flags.add("SOMATIK_NEU")
        details["somatik"] = sorted(set(som))

    # PLATEAU_ALS_EINBRUCH: ereignisbezogenes Faktor-I-Item + negative z an Tagen ohne
    # belastenden Tenor - NUR nach dem Ordnungsuebergang (davor ist negativ = Krise).
    ab = f.get("ordnungsuebergang") or (f["uebergaenge"][0]["datum"] if f["uebergaenge"] else None)
    ism = f.get("ism")
    if ism and any(it.get("ereignisbezogen") and it.get("faktor") == "I" for it in ism["items"]):
        fI = next((x for x in a.ism_faktoren if x["id"] == 0 and x["besetzt"]), None)
        if fI is not None and fI["z"] is not None:
            plateau = [d.isoformat() for d, z in zip(a.days, fI["z"], strict=True)
                       if z == z and z < 0 and (ab is None or d.isoformat() > ab)
                       and tenor.get(d.isoformat()) in ("neutral/plateau", "gut")]
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


def _p(pv) -> str:
    if pv is None:
        return "p –"
    return "p < 0,001" if pv < 0.001 else f"p = {_fmt(float(pv), 3)}"


def _dauer(n) -> str:
    return "bis Ende offen" if n is None else f"{n} Tag{'' if n == 1 else 'e'}"


def artikel(anrede: str) -> str:
    """'Klientin' -> 'die Klientin', 'Klient' -> 'der Klient'."""
    return ("der " if anrede == "Klient" else "die ") + anrede


def build_faktenblock(fakten: dict, stage_a: list[dict], flags: dict, anrede: str, kuerzel: str) -> str:
    """Lesbarer Faktenblock fuer Stage B - JEDE Zahl im Bericht muss hier stehen."""
    f = fakten
    L: list[str] = []
    art = artikel(anrede)
    L.append(f"KLIENT:IN: Erste Nennung im Text \"{art} ({kuerzel})\", danach \"{art}\" "
             f"(am Satzanfang \"{art[0].upper() + art[1:]}\"). {anrede} immer groß schreiben.")

    # Ereignisnummern vorab, damit Einbrueche/Gipfel darauf verweisen koennen
    nummer: dict[int, int] = {}
    ev_by_date: dict[str, list[dict]] = {}
    for t in stage_a:
        for ev in t["ereignisse"]:
            nummer[id(ev)] = len(nummer) + 1
            ev_by_date.setdefault(ev["datum"], []).append(ev)

    def _um(iso: str, tage: int = 1) -> list[dict]:
        d0 = dt.date.fromisoformat(iso)
        return [ev for k in range(-tage, tage + 1)
                for ev in ev_by_date.get((d0 + dt.timedelta(k)).isoformat(), [])]

    def _ev_kurz(evs: list[dict]) -> str:
        if not evs:
            return "kein Tagebuch-Ereignis ±1 Tag"
        return "; ".join(f"E{nummer[id(ev)]} {ev['kategorie']}" for ev in evs)

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
                 f"Δ {_fmt(x['delta'])}, τ {_fmt(x['tau'], 2)} ({_p(x.get('p'))}); Sprung am Übergang "
                 f"{_fmt(x.get('sprung_uebergang'))}; Phasenmittel {pm}; Items: {', '.join(x['items'])}")
    gr = f["hsf"].get("gruppen") or []
    if gr:
        L.append("GRUPPEN FÜR ABBILDUNG 1 UND DIE PHASENTABELLE (Itemmittel):")
        for g in gr:
            pm = ", ".join(f"{k} {_fmt(v)}" for k, v in g["phasenmittel"].items())
            L.append(f"- {g['key']} {g['name']} [{g['polung']}]: Anfang {_fmt(g['anfang'])}, Ende {_fmt(g['ende'])}, "
                     f"τ {_fmt(g['tau'], 2)} ({_p(g.get('p'))}); Sprung am Übergang {_fmt(g['sprung_uebergang'])}; "
                     f"Phasenmittel {pm}")
    k = f["hsf"]["komposit"]
    L.append(f"RESSOURCEN-KOMPOSIT ({len(k['items'])} Items): Anfang {_fmt(k['anfang'])}, Ende {_fmt(k['ende'])}, "
             f"Δ {_fmt(k['delta'])}, τ {_fmt(k['tau'], 2)}; Phasenmittel "
             + ", ".join(f"{a} {_fmt(b)}" for a, b in k["phasenmittel"].items()))
    if f["hsf"]["konstante_items"]:
        L.append("KONSTANTE ITEMS (SD ≤ 1, ohne Verlaufsinformation): " + ", ".join(f["hsf"]["konstante_items"]))
    L.append("HSF-ITEMS Anfang → Ende: " + "; ".join(
        f"{r['kurz']} {_fmt(r['anfang'])}→{_fmt(r['ende'])} (τ {_fmt(r['tau'], 2)})" for r in f["hsf"]["items"]))
    spr = sorted([r for r in f["hsf"]["items"] + f.get("ind_items", [])
                  if r.get("sprung_uebergang") is not None and not r.get("konstant")],
                 key=lambda r: -abs(r["sprung_uebergang"]))
    if spr:
        L.append("GRÖSSTE SPRÜNGE AM ÜBERGANG (Item, Mittel 4 Tage danach − 4 Tage davor; Reihenfolge des Wandels): "
                 + "; ".join(f"{r['kurz']} {_fmt(r['sprung_uebergang'])}" for r in spr[:6])
                 + " | kleinste: " + "; ".join(f"{r['kurz']} {_fmt(r['sprung_uebergang'])}"
                                               for r in spr[-3:]))
    sg = f["hsf"].get("symptom_gipfel") or []
    if sg:
        L.append("SYMPTOMGIPFEL (lokale Maxima der Symptombelastung) mit Tagebuch-Ereignissen ±1 Tag:")
        for g in sg:
            L.append(f"- {_d(g['datum'])} (Wert {_fmt(g['wert'])}): {_ev_kurz(_um(g['datum']))}")

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
            f"{r['kurz']} {_fmt(r['anfang'])}→{_fmt(r['ende'])} (τ {_fmt(r['tau'], 2)}, {_p(r.get('p'))})"
            for r in f["ind_items"]))
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
        if dk.get("gipfel"):
            L.append("- DK-Gipfel über P75 (mit Tagebuch-Ereignissen ±1 Tag): " + " | ".join(
                f"{_d(g['datum'])} {_fmt(g['wert'], 3)}: {_ev_kurz(_um(g['datum']))}" for g in dk["gipfel"]))
        if dk.get("ende"):
            e = dk["ende"]
            L.append(f"- DK am Ende {_d(e['datum'])} {_fmt(e['wert'], 3)}"
                     + (" = niedrigster Wert des Aufenthalts" if e["ist_minimum"] else
                        f"; Minimum {_d(dk['minimum']['datum'])} {_fmt(dk['minimum']['wert'], 3)}")
                     + (f"; letzter Tag mit kritischem Item {_d(dk['letzter_kritischer_tag'])}"
                        if dk.get("letzter_kritischer_tag") else ""))
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
            L.append(f"- {_d(e['datum'])}: Tiefe {_fmt(e['tiefe'])}, Dauer {_dauer(e['dauer'])}; "
                     f"{_ev_kurz(_um(e['datum']))}")
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
    pz = r.get("phasen_zu_anfang_ende") or []
    if pz:
        L.append("- Phasen zu Anfangsblock / Endblock (kleiner = ähnlicher): " + "; ".join(
            f"{x['label']} {_fmt(x['zu_anfang'])} / {_fmt(x['zu_ende'])}" for x in pz))
    # Ausloeser: Kategorien der Ereignisse +-1 Tag um Einbrueche und Symptomgipfel
    tage = [e["datum"] for e in f["einbrueche"]] + [g["datum"] for g in f["hsf"].get("symptom_gipfel") or []]
    gesehen: set[int] = set()
    kat: dict[str, int] = {}
    for iso in tage:
        for ev in _um(iso):
            if id(ev) not in gesehen and ev["kategorie"] not in ("therapie_intervention", "sonstiges"):
                gesehen.add(id(ev))
                kat[ev["kategorie"]] = kat.get(ev["kategorie"], 0) + 1
    if kat:
        L.append("AUSLÖSER-KATEGORIEN um Einbrüche und Symptomgipfel (Häufigkeit): " + ", ".join(
            f"{k} {v}" for k, v in sorted(kat.items(), key=lambda x: -x[1])))
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
    for m in det.get("medikation_alle", []):
        ab = f", wirksam ab {_d(m['wirksam_ab'])}" if m["wirksam_ab"] != m["datum"] else ""
        fenster = (f" – IM FENSTER ±2 Tage um den Übergang {_d(m['uebergang'])} ({m['uebergang_typ']})"
                   if m.get("uebergang") else "")
        L.append(f"- Medikation im Tagebuch {_d(m['datum'])}{ab}{fenster}: Zitat \"{m['zitat']}\"")
    if "somatik" in det:
        L.append("- Neue Somatik in den letzten 7 Tagen an: " + ", ".join(_d(x) for x in det["somatik"]))
    if "plateau" in det:
        L.append("- Plateau-Tage mit negativem Zielerleben: " + ", ".join(_d(x) for x in det["plateau"]))
    if "suizidalitaet" in det:
        s = det["suizidalitaet"]
        L.append("- Tagebuch erwähnt Suizidalität/Selbstverletzung an: "
                 + ", ".join(_d(x) for x in sorted(set(s["suizidalitaet"] + s["nssv"])))
                 + " – in Abschnitt 9 benennen (Hinweis an Behandler:innen, keine Einschätzung aus Fragebogendaten).")
    else:
        L.append("- Suizidalität/Selbstverletzung: im Tagebuch nicht erwähnt – im Bericht NICHT thematisieren "
                 "(auch keine Negativaussage).")
    if f.get("hinweise"):
        L.append("HINWEISE: " + " | ".join(f["hinweise"]))

    L.append("\nEREIGNISSE AUS DEM TAGEBUCH (Stage A; Zitate nur von hier, wörtlich):")
    n = 0
    for t in stage_a:
        for ev in t["ereignisse"]:
            n = nummer[id(ev)]
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
