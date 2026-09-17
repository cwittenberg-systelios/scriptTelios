"""
interview_dialog.py
───────────────────
Rueckfrage-Logik des Interview-Modus (v19.23, D1=C).

Ablauf pro Frage (Frontend fuehrt den Dialog, dieses Modul entscheidet
nur ueber die eine erlaubte Rueckfrage):

    Frage -> Antwort -> POST /api/interview/turn
        -> {"rueckfrage": null}  ->  Weiter
        -> {"rueckfrage": "…"}   ->  Antwort auf Rueckfrage -> Weiter
                                     (KEIN zweiter turn-Call: das Frontend
                                      setzt rueckfrage_bereits=True und
                                      bekommt deterministisch null)

Entscheidungsregeln, in dieser Reihenfolge:
  1. Es gab schon eine Rueckfrage zu dieser Frage       -> keine (D1: max. eine)
  2. Frage hat keine Pflichtaspekte                     -> keine (nichts zu pruefen)
  3. Antwort leer und Frage ist Pflicht                 -> deterministische
                                                          Rueckfrage, KEIN LLM
  4. Antwort leer, Frage nicht Pflicht                  -> keine ("nicht erhoben"
                                                          ist eine gueltige Antwort)
  5. sonst LLM-Check (Structured Output): welche Pflichtaspekte fehlen?
     Fehlt keiner -> keine Rueckfrage. Fehlt einer -> die vom LLM
     formulierte Rueckfrage (max. ein Satz).
  6. LLM-Fehler / unparsebares JSON -> keine Rueckfrage (der Dialog darf
     nie am Rueckfrage-Check haengen bleiben; wird geloggt).

Modell: D3=A - das Generierungsmodell des Workflows "dokumentation"
(gemma4:31b), ueber ensure_generation_model gegen die geladenen Modelle
validiert. Der Call ist klein (wenige hundert Tokens), Latenz haengt am
Kaltstart des Modells - deshalb waermt das Frontend beim Oeffnen des
Interview-Tabs nichts vor; das erste Turn laedt das Modell.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.interview_phrasen import quittung, ueberleitung
from app.core.interview_sets import KLIENT_KEY, get_frage
from app.services.interview_trigger import pruefe_trigger

logger = logging.getLogger(__name__)

MAX_RUECKFRAGE_CHARS = 300

RUECKFRAGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "fehlende_aspekte": {"type": "array", "items": {"type": "string"}},
        "rueckfrage": {"type": "string"},
    },
    "required": ["fehlende_aspekte", "rueckfrage"],
}

SYSTEM_PROMPT = (
    "Du bist ein Dokumentationsassistent in einer psychosomatischen Klinik. "
    "Ein Behandler beantwortet nach einer Therapiestunde kurze Fragen, damit "
    "daraus eine Dokumentation entsteht. Deine einzige Aufgabe: pruefen, ob "
    "die Antwort die genannten Pflichtaspekte inhaltlich abdeckt.\n\n"
    "REGELN:\n"
    "- Sei grosszuegig: ein Aspekt gilt als abgedeckt, wenn er sinngemaess, "
    "auch knapp oder umgangssprachlich, beantwortet ist. Auch 'nichts', "
    "'keine', 'nicht erhoben' oder 'weiss ich nicht' decken einen Aspekt ab.\n"
    "- Nenne unter fehlende_aspekte NUR Aspekte, zu denen die Antwort GAR "
    "NICHTS sagt.\n"
    "- Fehlt nichts: rueckfrage ist ein leerer String.\n"
    "- Fehlt etwas: formuliere GENAU EINE kurze, freundliche Rueckfrage in "
    "der Du-Form (ein Satz, maximal 25 Woerter), die nur nach dem Fehlenden fragt.\n"
    "- Bewerte nicht die fachliche Qualitaet, gib keine Ratschlaege, "
    "erfinde keine Inhalte.\n"
    "- Ist eine Anrede der Person angegeben (z.B. 'Herr M.'), verwende sie "
    "in der Rueckfrage statt 'die Person'.\n"
    "- Antworte ausschliesslich als JSON gemaess Schema."
)


@dataclass
class TurnRequest:
    set_key: str
    frage_key: str
    frage_text: str
    antwort: str
    pflicht: bool = False
    pflichtaspekte: list[str] = field(default_factory=list)
    rueckfrage_bereits: bool = False
    bisherige: list[dict] = field(default_factory=list)   # [{frage, antwort}]
    # v19.24
    trigger_stufe: int = 0          # 0 = normale Antwort, 1/2 = Antwort auf Trigger-Glied
    anrede: str | None = None       # "Herr M." aus der Klient-Frage (B2)
    frage_index: int = 0            # Seed fuer Phrasen-Variation
    vorherige_phrase: str | None = None


@dataclass
class TurnResult:
    rueckfrage: str | None
    fehlende_aspekte: list[str]
    quelle: str          # "keine" | "deterministisch" | "llm" | "llm_fehler" | "trigger" | "klient"
    model_used: str | None = None
    # v19.24
    rueckfrage_typ: str | None = None   # "aspekt" | "trigger:suizidalitaet" | "pflicht" | "klient"
    trigger_stufe: int = 0              # naechste Stufe der Trigger-Kette (0 = keine)
    quittung: str | None = None         # gesprochen VOR der Rueckfrage
    ueberleitung: str | None = None     # gesprochen VOR der naechsten Frage
    klient: dict | None = None          # {anrede, initial, gender} bei der Klient-Frage


def _resolve_aspekte(req: TurnRequest) -> tuple[bool, list[str]]:
    """Pflicht-Flag und Aspekte: Frontend-Werte gewinnen (editierbare
    Fragen), sonst Server-Default der Frage."""
    pflicht = req.pflicht
    aspekte = [a.strip() for a in (req.pflichtaspekte or []) if a and a.strip()]
    f = get_frage(req.set_key, req.frage_key)
    if f is not None:
        pflicht = pflicht or f.pflicht
        if not aspekte:
            aspekte = list(f.pflichtaspekte)
    return pflicht, aspekte


def deterministic_rueckfrage(frage_text: str) -> str:
    return ("Diese Frage ist eine Pflichtfrage - magst du sie kurz beantworten? "
            f"({frage_text.strip().rstrip('?')}?)")


KLIENT_RUECKFRAGE = ("Ich habe kein Kürzel erkannt. Magst du Anrede und "
                     "Anfangsbuchstaben nennen, zum Beispiel „Frau K.“?")


def _mit_phrasen(res: "TurnResult", req: "TurnRequest", *, antwort_leer: bool) -> "TurnResult":
    """B1/C2: Quittung nur vor einer Rueckfrage; Ueberleitung nur, wenn keine
    Rueckfrage folgt und tatsaechlich geantwortet wurde."""
    seed = req.frage_index * 31 + req.trigger_stufe * 7 + len(req.antwort or "")
    if res.rueckfrage:
        ernst = bool(res.rueckfrage_typ and res.rueckfrage_typ.startswith("trigger"))
        res.quittung = quittung(seed, ernst=ernst, vorherige=req.vorherige_phrase)
    elif not antwort_leer:
        res.ueberleitung = ueberleitung(seed, vorherige=req.vorherige_phrase)
    return res


def build_user_content(req: TurnRequest, aspekte: list[str]) -> str:
    parts = []
    if req.bisherige:
        parts.append("BISHERIGER DIALOG (nur Kontext, nicht bewerten):")
        for b in req.bisherige[-6:]:
            fq = str(b.get("frage", "")).strip()
            an = str(b.get("antwort", "")).strip() or "(nicht erhoben)"
            if fq:
                parts.append(f"- {fq}\n  -> {an}")
        parts.append("")
    if req.anrede:
        parts.append(f"PERSON: {req.anrede}")
    parts.append(f"AKTUELLE FRAGE: {req.frage_text.strip()}")
    parts.append("PFLICHTASPEKTE:")
    parts.extend(f"- {a}" for a in aspekte)
    parts.append("")
    parts.append(f"ANTWORT DES BEHANDLERS:\n{req.antwort.strip()}")
    parts.append("")
    parts.append("Pruefe die Abdeckung und antworte als JSON.")
    return "\n".join(parts)


def _clean_rueckfrage(text: str) -> str:
    t = " ".join((text or "").split()).strip()
    if len(t) > MAX_RUECKFRAGE_CHARS:
        t = t[:MAX_RUECKFRAGE_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + " …"
    return t


async def decide_turn(req: TurnRequest, *, model: str | None = None,
                      generate_fn=None) -> TurnResult:
    """Entscheidet ueber die Rueckfrage (siehe Modul-Docstring).

    generate_fn: Injektionspunkt fuer Tests (Signatur wie llm.generate_text).
    """
    pflicht, aspekte = _resolve_aspekte(req)
    antwort = (req.antwort or "").strip()

    # ── v19.24 (B2): Klient-Frage - deterministisch, kein LLM ────────────
    if req.frage_key == KLIENT_KEY:
        from app.services.interview_protokoll import extract_klient
        k = extract_klient(antwort)
        if k:
            res = TurnResult(None, [], "klient", klient=k)
            return _mit_phrasen(res, req, antwort_leer=False)
        if req.rueckfrage_bereits:
            # D4=B: einmal nachfragen, danach bleibt das Kuerzel-Feld Pflicht.
            return TurnResult(None, [], "klient")
        res = TurnResult(KLIENT_RUECKFRAGE, [], "klient", rueckfrage_typ="klient")
        return _mit_phrasen(res, req, antwort_leer=not antwort)

    # ── v19.24 (B3/C1): Trigger-Kette zuerst, fuer jede Frage ─────────────
    tr = pruefe_trigger(antwort, stufe=req.trigger_stufe, anrede=req.anrede)
    if tr.nachfrage:
        res = TurnResult(tr.nachfrage, [], "trigger",
                         rueckfrage_typ=f"trigger:{tr.trigger}", trigger_stufe=tr.stufe)
        return _mit_phrasen(res, req, antwort_leer=False)
    if req.trigger_stufe > 0:
        # Antwort auf ein Trigger-Glied ohne weiteres Glied: Kette zu Ende,
        # kein Aspekt-Check mehr fuer diese Frage (C1).
        return _mit_phrasen(TurnResult(None, [], "keine"), req, antwort_leer=not antwort)

    if req.rueckfrage_bereits:
        return _mit_phrasen(TurnResult(None, [], "keine"), req, antwort_leer=not antwort)

    if not antwort:
        if pflicht:
            res = TurnResult(deterministic_rueckfrage(req.frage_text), aspekte,
                             "deterministisch", rueckfrage_typ="pflicht")
            return _mit_phrasen(res, req, antwort_leer=True)
        return TurnResult(None, [], "keine")

    if not aspekte:
        return _mit_phrasen(TurnResult(None, [], "keine"), req, antwort_leer=False)

    if generate_fn is None:
        from app.services.llm import ensure_generation_model, generate_text
        generate_fn = generate_text
        if not model:
            model = await ensure_generation_model(None, "dokumentation")

    user = build_user_content(req, aspekte)
    _leer = not antwort
    try:
        result = await generate_fn(
            SYSTEM_PROMPT, user, max_tokens=200, model=model,
            workflow="dokumentation", temperature_override=0.1,
            response_format=RUECKFRAGE_SCHEMA,
        )
    except Exception as e:  # noqa: BLE001 - Dialog darf nie haengen bleiben
        logger.warning("interview_dialog: LLM-Check fehlgeschlagen (%s) - keine Rueckfrage", e)
        return _mit_phrasen(TurnResult(None, [], "llm_fehler", model), req, antwort_leer=_leer)

    data = result.get("structured_data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        logger.warning("interview_dialog: kein parsebares JSON - keine Rueckfrage")
        return _mit_phrasen(
            TurnResult(None, [], "llm_fehler", result.get("model_used") if isinstance(result, dict) else model),
            req, antwort_leer=_leer)

    fehlend = [str(a).strip() for a in (data.get("fehlende_aspekte") or []) if str(a).strip()]
    rueckfrage = _clean_rueckfrage(str(data.get("rueckfrage") or ""))
    model_used = result.get("model_used") or model

    if not fehlend or not rueckfrage:
        # Inkonsistente LLM-Antwort (fehlend ohne Frage oder Frage ohne
        # fehlend) wird als "nichts fehlt" gewertet - konservativ Richtung
        # weniger Rueckfragen.
        return _mit_phrasen(TurnResult(None, fehlend if rueckfrage else [], "llm", model_used),
                            req, antwort_leer=_leer)
    return _mit_phrasen(TurnResult(rueckfrage, fehlend, "llm", model_used, rueckfrage_typ="aspekt"),
                        req, antwort_leer=_leer)
