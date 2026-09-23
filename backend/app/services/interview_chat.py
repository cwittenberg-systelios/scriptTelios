"""
interview_chat.py
─────────────────
Dialog-Modus des Interviews (v19.31, S2): das Modell fuehrt das Gespraech,
die Fragenliste ist seine Checkliste, deterministische Leitplanken sichern
Pflichtpunkte, Suizidalitaet, Budget und Redundanz.

Entscheidungen (Sprintplan v19.31, 2026-09-23):
  G1  System duzt den Behandler.
  G2  Klient wird im Gespraech benannt, wie der Behandler ihn nennt (D1=B);
      die Doku verwendet nur das Kuerzel (Regel in INTERVIEW_MODUS_REGELN).
  G3  Reihenfolge der Fragenliste = Rueckgrat, Abweichungen erlaubt.
  G4  Budget grosszuegig, konfigurierbar (Default 4 je Thema, 24 gesamt).
  D2  Historie lebt im Frontend und kommt mit jedem Turn.
  D5  Structured Output je Turn, `sage` als erstes Feld (gestreamt).

Ablauf je Turn (plan_turn -> generate_chat_stream -> apply_turn):
  1. plan_turn(): Checkliste fortschreiben, Leitplanken pruefen, Regie
     bestimmen (Text, der als letzte System-Nachricht in den Prompt geht).
  2. Modell antwortet gemaess TURN_SCHEMA.
  3. apply_turn(): `abgedeckt` monoton in die Checkliste uebernehmen,
     `fertig` nur akzeptieren, wenn die Pflichtpunkte per Marker-Check
     bestaetigt sind.

Alle Funktionen sind rein (kein State, kein I/O) - der Endpoint ruft sie.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.interview_sets import KLIENT_KEY, SELBSTGEFAEHRDUNG_KEY, InterviewSet, get_set
from app.services.interview_protokoll import extract_klient
from app.services.interview_trigger import pruefe_trigger
from app.services.suizidalitaet import mentions_nssv, mentions_suizidalitaet

# ── Konfiguration (G4) ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChatConfig:
    max_rueckfragen_thema: int = 4
    max_rueckfragen_gesamt: int = 24
    max_turns: int = 60                 # harte Notbremse
    verdichten_ab_turns: int = 20       # aeltere Turns zusammenfassen (Kontext)


STATUS_OFFEN = "offen"
STATUS_UNKLAR = "unklar"
STATUS_ABGEDECKT = "abgedeckt"

TURN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "sage": {"type": "string"},
        "abgedeckt": {"type": "array", "items": {"type": "string"}},
        "unklar": {"type": "array", "items": {"type": "string"}},
        "thema": {"type": "string"},
        "fertig": {"type": "boolean"},
    },
    "required": ["sage", "abgedeckt", "unklar", "thema", "fertig"],
}

AUFTRAG = (
    "Fuehre mit mir ein Interview ueber das Therapiegespraech entlang der "
    "Fragenliste und sammle alles, was fuer eine schriftliche Zusammenfassung "
    "noetig ist. Wende dein therapeutisches Hintergrundwissen an, um Punkte zu "
    "klaeren, zu hinterfragen oder dir bestaetigen zu lassen, die dir nicht "
    "eindeutig klar sind - so dass in der Zusammenfassung nichts hinzuerfunden "
    "werden muss."
)

REGELN = (
    "REGELN FUER DICH ALS INTERVIEWER:\n"
    "- Du sprichst mit dem Behandler (Therapeut/in) und duzt ihn. Die Person, "
    "um die es geht, ist Klient/in des Behandlers; nenne sie so, wie der "
    "Behandler sie nennt (Name, Vorname, Kuerzel oder 'sie/er').\n"
    "- Stelle GENAU EINE Frage je Turn - eine Sache, nicht mehrere Teilfragen "
    "in einem Satz. Kurze Saetze, gesprochene Sprache - deine Saetze werden "
    "vorgelesen. Keine Aufzaehlungen, keine Ueberschriften.\n"
    "- Die Fragenliste nennt THEMEN, nicht den Wortlaut: formuliere jede Frage "
    "in eigenen, knappen Worten. Lies sie nicht vor.\n"
    "- Nicht jede Antwort bestaetigen. Eine kurze Bestaetigung ('Verstanden.') "
    "nur vor einer Nachfrage oder nach einer schweren Antwort; sonst direkt "
    "die naechste Frage. Keine Floskel zweimal im Gespraech - die bereits "
    "verwendeten Einstiege stehen unten.\n"
    "- Mit [OPTIONAL] markierte Punkte fragst du einmal kurz; was immer der "
    "Behandler antwortet (auch 'nichts' oder 'weiss nicht'), ist genug - "
    "nicht nachfragen. Sie muessen fuer den Abschluss nicht abgedeckt sein.\n"
    "- Die Fragenliste ist dein Rueckgrat: arbeite sie in ihrer Reihenfolge ab. "
    "Du darfst abweichen, wenn ein Punkt schon beantwortet wurde (dann NICHT "
    "erneut fragen) oder wenn eine Antwort einen spaeteren Punkt schon "
    "beruehrt. Am Ende muss jeder Punkt abgedeckt sein.\n"
    "- Klaere nur, was der Behandler gesagt hat. Frage nach, wenn etwas "
    "unklar, mehrdeutig oder widerspruechlich ist, oder wenn ein fachlich "
    "wichtiger Aspekt fehlt (z.B. Zustand am Ende, Vereinbarung, Kontakt).\n"
    "- Biete KEINE Hypothesen, Deutungen oder Formulierungen an, die der "
    "Behandler nur bestaetigen muesste ('Meinst du, dass er sich schaemte?'). "
    "Frage offen ('Wie hast du das verstanden?').\n"
    "- Wiederhole keine Frage, die du schon gestellt hast, und frage nicht "
    "nach, was der Behandler bereits klar gesagt hat.\n"
    "- Bewerte nichts, gib keine Ratschlaege, keine Therapieempfehlungen.\n"
    "- Wenn eine REGIE-ANWEISUNG vorliegt, befolge sie in diesem Turn "
    "wortgetreu; sie hat Vorrang vor allem anderen.\n"
    "- Antworte ausschliesslich als JSON gemaess Schema. `sage` ist dein "
    "gesprochener Text. `abgedeckt` nennt die Schluessel der Fragen, die "
    "durch die bisherigen Antworten vollstaendig beantwortet sind. `unklar` "
    "nennt Schluessel, zu denen du noch nachfragen willst. `thema` ist der "
    "Schluessel der Frage, zu der dein aktueller Satz gehoert. `fertig` ist "
    "true, wenn alle Punkte abgedeckt sind und du dich verabschiedest.\n"
    "- Beim Abschluss: bedanke dich kurz und sage, dass die Verlaufsnotiz "
    "jetzt erstellt werden kann. Sage NICHT, dass du sie erstellst - das "
    "startet der Behandler selbst.\n"
)


# ── Datenmodell des Turns ─────────────────────────────────────────────────────

@dataclass
class Turn:
    rolle: str          # "system" (Interviewer) | "behandler"
    text: str
    thema: str = ""     # Frage-Key, den das System diesem Turn zugeordnet hat


@dataclass
class ChatState:
    set_key: str
    historie: list[Turn]
    checkliste: dict[str, str]          # frage_key -> STATUS_*
    rueckfragen_je_thema: dict[str, int] = field(default_factory=dict)
    trigger_stufe: int = 0
    klient: dict | None = None
    fertig: bool = False


@dataclass
class Plan:
    system_prompt: str
    messages: list[dict]
    regie: str | None
    regie_typ: str | None       # "trigger" | "pflicht" | "budget" | "redundanz" | "abschluss" | None
    state: ChatState


# ── Hilfen ────────────────────────────────────────────────────────────────────

def _set(set_key: str) -> InterviewSet:
    s = get_set(set_key)
    if s is None:
        raise ValueError(f"Unbekanntes Fragen-Set: {set_key}")
    return s


def behandler_text(historie: list[Turn]) -> str:
    return "\n".join(t.text for t in historie if t.rolle == "behandler" and t.text.strip())


def letzte_behandler_antwort(historie: list[Turn]) -> str:
    for t in reversed(historie):
        if t.rolle == "behandler":
            return t.text
    return ""


def pflicht_erfuellt(state: ChatState) -> dict[str, bool]:
    """Marker-Check statt Modellmeinung (Leitplanke 2)."""
    text = behandler_text(state.historie)
    klient_ok = state.klient is not None or extract_klient(text) is not None
    lo = text.lower()
    suizid_ok = (
        mentions_suizidalitaet(text) or mentions_nssv(text)
        or any(m in lo for m in ("keine hinweise", "kein hinweis", "keine anzeichen",
                                  "nicht suizidal", "keine selbstgefährd", "keine selbstgefaehrd",
                                  "keine krise", "unauffällig", "unauffaellig"))
    )
    return {KLIENT_KEY: klient_ok, SELBSTGEFAEHRDUNG_KEY: suizid_ok}


def _redundant(frage: str, historie: list[Turn]) -> bool:
    """Grobe Redundanz-Erkennung: >70% Wortueberlappung mit einer frueheren
    Systemfrage (ohne Stoppwoerter)."""
    stop = {"und", "oder", "der", "die", "das", "du", "ist", "war", "hat", "wie", "was",
            "wer", "wo", "in", "im", "zu", "mit", "ein", "eine", "es", "sich", "bei", "von",
            "auf", "den", "dem", "des", "noch", "auch", "dann", "ob", "dass", "kurz", "magst"}
    def bag(t: str) -> set[str]:
        return {w.strip(".,;:?!\"'„“") for w in t.lower().split()} - stop - {""}
    b = bag(frage)
    if len(b) < 4:
        return False
    for t in historie:
        if t.rolle != "system":
            continue
        ob = bag(t.text)
        if ob and len(b & ob) / len(b | ob) > 0.7:
            return True
    return False


# ── Prompt ────────────────────────────────────────────────────────────────────

def _checkliste_block(s: InterviewSet, state: ChatState) -> str:
    lines = ["FRAGENLISTE (dein Rueckgrat, mit Status):"]
    for i, f in enumerate(s.fragen, 1):
        st = state.checkliste.get(f.key, STATUS_OFFEN)
        pflicht = " [PFLICHT]" if f.pflicht else (" [OPTIONAL]" if getattr(f, "optional", False) else "")
        lines.append(f"{i}. ({f.key}) [{st}]{pflicht} {f.text}")
    return "\n".join(lines)


_EINSTIEG_MUSTER = (
    "alles klar", "verstanden", "okay", "gut", "danke", "super", "prima",
    "sehr gut", "in ordnung", "aha", "ah",
)


def verwendete_einstiege(historie: list[Turn]) -> list[str]:
    """v19.31.2: Floskeln, mit denen das System bisher Saetze begonnen hat -
    gehen in den Prompt, damit das Modell sie nicht wiederholt."""
    out: list[str] = []
    for t in historie:
        if t.rolle != "system":
            continue
        lo = t.text.strip().lower()
        for m in sorted(_EINSTIEG_MUSTER, key=len, reverse=True):
            if lo.startswith(m) and (len(lo) == len(m) or not lo[len(m)].isalpha()):
                if m not in out:
                    out.append(m)
                break
    return out


def klient_nennung(klient: dict | None) -> str | None:
    """Wie das System die Person im Gespraech nennt: so, wie der Behandler sie
    genannt hat (G2); Kuerzel nur als Rueckfall."""
    if not klient:
        return None
    return klient.get("nennung") or (f"{klient.get('anrede', '')} {klient.get('initial', '')}".strip() or None)


def build_system_prompt(state: ChatState, cfg: ChatConfig, regie: str | None) -> str:
    s = _set(state.set_key)
    parts = [
        "Du bist ein Dokumentationsassistent in einer psychosomatischen Klinik "
        "(sysTelios). Verfahren dieser Stunde: " + s.label + ".",
        "AUFTRAG DES BEHANDLERS: " + AUFTRAG,
        REGELN,
        _checkliste_block(s, state),
        f"BUDGET: Du hast noch {max(0, cfg.max_rueckfragen_gesamt - sum(state.rueckfragen_je_thema.values()))} "
        f"Nachfragen insgesamt und hoechstens {cfg.max_rueckfragen_thema} je Punkt.",
    ]
    if state.klient:
        parts.append(
            f"KLIENT/IN: im Gespraech '{klient_nennung(state.klient)}' "
            f"(Kuerzel fuer die Doku: {state.klient['anrede']} {state.klient['initial']})."
        )
    einst = verwendete_einstiege(state.historie)
    if einst:
        parts.append("BEREITS VERWENDETE EINSTIEGE (nicht wiederholen): "
                     + ", ".join(f"'{e.capitalize()}'" for e in einst))
    if regie:
        parts.append("REGIE-ANWEISUNG FUER DIESEN TURN: " + regie)
    return "\n\n".join(parts)


def _messages(state: ChatState, cfg: ChatConfig) -> list[dict]:
    """Historie als Chat-Messages; ab cfg.verdichten_ab_turns werden aeltere
    Turns zu einer kompakten Zusammenfassung gefaltet (Kontextlaenge)."""
    hist = state.historie
    msgs: list[dict] = []
    if len(hist) > cfg.verdichten_ab_turns:
        alt, neu = hist[:-12], hist[-12:]
        zusammen = "\n".join(f"{'Interviewer' if t.rolle == 'system' else 'Behandler'}: {t.text}" for t in alt)
        msgs.append({"role": "user", "content": "BISHERIGER GESPRAECHSVERLAUF (verkuerzt):\n" + zusammen})
        msgs.append({"role": "assistant", "content": '{"sage": "Verstanden, ich habe den bisherigen Verlauf.", "abgedeckt": [], "unklar": [], "thema": "", "fertig": false}'})
        hist = neu
    for t in hist:
        msgs.append({"role": "assistant" if t.rolle == "system" else "user", "content": t.text})
    if not msgs or msgs[-1]["role"] == "assistant":
        # Erster Turn oder Fortsetzung ohne neue Antwort: Aufforderung
        msgs.append({"role": "user", "content": "(Beginne bzw. setze das Interview fort.)"})
    return msgs


# ── Leitplanken ───────────────────────────────────────────────────────────────

def plan_turn(state: ChatState, cfg: ChatConfig = ChatConfig()) -> Plan:
    s = _set(state.set_key)
    letzte = letzte_behandler_antwort(state.historie)
    regie: str | None = None
    typ: str | None = None

    # Klient aus der Historie aufsammeln
    if state.klient is None:
        k = extract_klient(behandler_text(state.historie))
        if k:
            state.klient = k
            state.checkliste[KLIENT_KEY] = STATUS_ABGEDECKT

    # 1. Suizidalitaets-Trigger auf die letzte Antwort.
    # v19.31.2: Ist die Kette in diesem Gespraech schon gelaufen (Glied 1 wurde
    # gestellt), startet ein spaeterer Verweis ("siehe oben, Suizidgedanken")
    # sie nicht erneut - sonst fragt das System dieselbe Frage zweimal.
    kette_gelaufen = any(
        t.rolle == "system" and "konkrete Pläne oder Handlungen" in t.text
        for t in state.historie
    )
    if letzte and not (kette_gelaufen and state.trigger_stufe == 0):
        tr = pruefe_trigger(letzte, stufe=state.trigger_stufe, anrede=klient_nennung(state.klient))
        if tr.nachfrage:
            state.trigger_stufe = tr.stufe
            regie = f"Stelle jetzt genau diese Frage, ohne Umschweife: \"{tr.nachfrage}\""
            typ = "trigger"
        elif state.trigger_stufe > 0:
            state.trigger_stufe = 0

    # 2./3. Abschluss nur, wenn Pflichtpunkte per Marker bestaetigt; sonst Budget
    if regie is None:
        alle = all(state.checkliste.get(f.key) == STATUS_ABGEDECKT
                   for f in s.fragen if not getattr(f, "optional", False))
        pf = pflicht_erfuellt(state)
        offen_pflicht = [f for f in s.fragen if f.pflicht and not pf.get(f.key, False)]
        if alle and not offen_pflicht:
            regie = ("Alle Punkte sind abgedeckt. Bedanke dich kurz und sage, dass die "
                     "Verlaufsnotiz jetzt erstellt werden kann (NICHT, dass du sie erstellst). "
                     "Setze fertig auf true.")
            typ = "abschluss"
        elif alle and offen_pflicht:
            f = offen_pflicht[0]
            regie = f"Der Pflichtpunkt ({f.key}) ist noch nicht belastbar beantwortet. Frage jetzt genau danach: \"{f.text}\""
            typ = "pflicht"
        else:
            gesamt = sum(state.rueckfragen_je_thema.values())
            ueber = [k for k, v in state.rueckfragen_je_thema.items() if v >= cfg.max_rueckfragen_thema]
            if gesamt >= cfg.max_rueckfragen_gesamt:
                regie = "Das Nachfrage-Budget ist erschoepft. Stelle keine Nachfragen mehr; gehe zum naechsten offenen Punkt der Fragenliste oder schliesse ab."
                typ = "budget"
            elif ueber:
                regie = f"Zu den Punkten {', '.join(ueber)} nicht weiter nachfragen; gehe zum naechsten offenen Punkt."
                typ = "budget"

    if len(state.historie) >= cfg.max_turns and typ != "abschluss":
        regie = ("Das Gespraech ist lang genug. Schliesse jetzt ab: bedanke dich, sage, dass die "
                 "Verlaufsnotiz jetzt erstellt werden kann, und setze fertig auf true.")
        typ = "abschluss"

    return Plan(
        system_prompt=build_system_prompt(state, cfg, regie),
        messages=_messages(state, cfg),
        regie=regie, regie_typ=typ, state=state,
    )


def apply_turn(state: ChatState, data: dict | None, sage: str, plan: Plan,
               cfg: ChatConfig = ChatConfig()) -> dict:
    """Uebernimmt das Modellergebnis in den Zustand. Liefert das Meta-Objekt
    fuer das Frontend."""
    s = _set(state.set_key)
    keys = {f.key for f in s.fragen}
    data = data if isinstance(data, dict) else {}
    thema = str(data.get("thema") or "").strip()
    if thema not in keys:
        thema = ""
    for k in data.get("abgedeckt") or []:
        if k in keys:
            state.checkliste[k] = STATUS_ABGEDECKT       # monoton
    for k in data.get("unklar") or []:
        if k in keys and state.checkliste.get(k) != STATUS_ABGEDECKT:
            state.checkliste[k] = STATUS_UNKLAR
    # Budget zaehlen: eine Frage zu einem bereits beruehrten Thema ist eine Nachfrage
    if thema and plan.regie_typ != "trigger":
        beruehrt = any(t.rolle == "system" and t.thema == thema for t in state.historie)
        if beruehrt:
            state.rueckfragen_je_thema[thema] = state.rueckfragen_je_thema.get(thema, 0) + 1
    redundant = _redundant(sage, state.historie)

    fertig_gewuenscht = bool(data.get("fertig"))
    pf = pflicht_erfuellt(state)
    fertig = fertig_gewuenscht and all(pf.values()) and plan.regie_typ != "trigger"
    state.fertig = fertig

    state.historie.append(Turn(rolle="system", text=sage.strip(), thema=thema))
    return {
        "checkliste": dict(state.checkliste),
        "thema": thema,
        "fertig": fertig,
        "fertig_verweigert": fertig_gewuenscht and not fertig,
        "pflicht": pf,
        "regie_typ": plan.regie_typ,
        "redundant": redundant,
        "klient": state.klient,
        "trigger_stufe": state.trigger_stufe,
        "rueckfragen": dict(state.rueckfragen_je_thema),
    }
