"""
diagnose_rewrite.py — v19.26: satzgenaue Entdiagnostizierung der Anamnese (P2).

Problem (Log 08.09.2026, Job cae59639): "Hinzu kommen Flashbacks und ein
starkes Gruebeln im Rahmen einer Posttraumatischen Belastungsstoerung (PTBS)
und einer rezidivierenden depressiven Stoerung." - die Diagnose erklaert das
Symptom, das sie begruenden soll (zirkulaer). Prompt-Regel (A3a/A3c) und QC
(DIAGNOSE_ZIRKULAER) allein verhindern das nicht; der Ganztext-Repair ist
nach Erfahrung der Therapeuten meist nicht besser als das Original.

Loesung: NUR die betroffenen Saetze (quality_check.diagnose_sentences ->
kind == "zirkulaer") werden einzeln umformuliert:
  1. LLM-Call pro Satz (kleiner Prompt, temperature 0.2, JSON-Schema
     {"satz": str}) - "ohne Diagnosebezeichnung, alle Beschwerden/Fakten
     unveraendert, nichts hinzufuegen, ein Satz".
  2. Deterministische Verifikation (verify_rewrite): keine Diagnose-Labels
     mehr, kein Erklaerungsrahmen, Laenge 40-160 % des Originals, keine
     neuen Zahlen, kein neues 'Herr/Frau X.', keine Meta-Saetze.
  3. Besteht der Vorschlag nicht (oder LLM-Fehler): deterministischer
     Fallback (strip_frame_clause) - Erklaerungsklausel am Satzende
     streichen ("... Gruebeln im Rahmen einer PTBS und einer ... Stoerung."
     -> "... Gruebeln."), nur wenn der Restsatz >= 5 Woerter behaelt.
  4. Sonst bleibt der Satz; das kritische QC-Issue bleibt sichtbar.

Ergebnis: (neuer_text, telemetry) mit telemetry =
  {"sentences": n, "replaced": n, "kept": n, "mode": "llm|fallback|mixed",
   "pairs": [{"before", "after", "how"}], "duration_s"}.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_REWRITE_SYSTEM = (
    "Du bist ein klinisches Schreibsystem. Du bekommst EINEN Satz aus einer "
    "Anamnese, in dem eine Diagnose als Erklärung der Beschwerden verwendet wird "
    "(zirkulär). Formuliere den Satz so um, dass KEINE Diagnosebezeichnung und "
    "kein ICD-Code mehr vorkommt. Regeln:\n"
    "- Alle im Satz genannten Beschwerden, Symptome, Zeitangaben, Auslöser und "
    "Fakten bleiben unverändert erhalten.\n"
    "- NICHTS hinzufügen, was nicht im Satz steht (keine neuen Symptome, keine "
    "Erklärungen, keine Namen, keine Zahlen).\n"
    "- Den Erklärungsrahmen ('im Rahmen einer ...', 'vor dem Hintergrund ...', "
    "'aufgrund ...', 'bedingt durch ...') ersatzlos streichen; wenn dadurch nur "
    "eine Aufzählung von Beschwerden übrig bleibt, ist das richtig.\n"
    "- Ein Satz, gleiche Perspektive und Zeitform wie das Original, Deutsch, "
    "kein Markdown, keine Erklärungen.\n"
    "Antworte AUSSCHLIESSLICH als JSON-Objekt {\"satz\": \"...\"}."
)
_REWRITE_SCHEMA = {"type": "object", "properties": {"satz": {"type": "string"}}, "required": ["satz"]}

_NUM_RE = re.compile(r"\d+")
_NAME_RE = re.compile(r"\b(?:Herr|Frau)\s+[A-ZÄÖÜ][\wäöüß-]*\.?")
_META_RE = re.compile(r"(?i)\b(hier ist|umformuliert|der satz|als json|diagnose(?:bezeichnung)? entfernt)\b")
_TRAILING_FRAME_RE = re.compile(
    r",?\s+(?:im rahmen|vor dem hintergrund|aufgrund|bedingt durch|infolge|im kontext|als ausdruck|als folge|als teil)"
    r"\s+[^.;!?]*$",
    re.IGNORECASE,
)


def verify_rewrite(original: str, candidate: str) -> tuple[bool, str]:
    """Deterministische Abnahme eines Umformulierungsvorschlags."""
    from app.services.quality_check import _DX_FRAME_RE, _DX_LABEL_IN_TEXT_RE
    cand = (candidate or "").strip().strip('"').strip()
    if not cand:
        return False, "leer"
    if _DX_LABEL_IN_TEXT_RE.search(cand):
        return False, "diagnose_bleibt"
    if _DX_FRAME_RE.search(cand):
        return False, "rahmen_bleibt"
    if _META_RE.search(cand):
        return False, "meta_text"
    ow, cw = len(original.split()), len(cand.split())
    if cw < max(4, int(ow * 0.4)) or cw > int(ow * 1.6) + 3:
        return False, f"laenge_{cw}_von_{ow}"
    if set(_NUM_RE.findall(cand)) - set(_NUM_RE.findall(original)):
        return False, "neue_zahl"
    if set(_NAME_RE.findall(cand)) - set(_NAME_RE.findall(original)):
        return False, "neuer_name"
    if len(re.findall(r"[.!?](?:\s|$)", cand)) > 2:
        return False, "mehrere_saetze"
    return True, "ok"


def strip_frame_clause(sentence: str) -> Optional[str]:
    """Deterministischer Fallback: Erklaerungsklausel am Satzende streichen.
    None, wenn kein Satzend-Rahmen oder Restsatz < 5 Woerter."""
    from app.services.quality_check import _DX_LABEL_IN_TEXT_RE
    s = sentence.strip()
    end = ""
    if s and s[-1] in ".!?":
        end, s = s[-1], s[:-1]
    m = _TRAILING_FRAME_RE.search(s)
    if not m:
        return None
    rest = s[: m.start()].rstrip(" ,;")
    if len(rest.split()) < 5 or _DX_LABEL_IN_TEXT_RE.search(rest):
        return None
    return rest + (end or ".")


async def _llm_rewrite(sentence: str, generate: Callable[..., Awaitable[dict]], model: Optional[str]) -> Optional[str]:
    res = await generate(
        _REWRITE_SYSTEM, f"SATZ:\n{sentence}", max_tokens=300, model=model,
        workflow="anamnese_dx_rewrite", temperature_override=0.2,
        response_format=_REWRITE_SCHEMA, skip_aggressive_dedup=True,
    )
    sd = res.get("structured_data")
    if isinstance(sd, dict) and sd.get("satz"):
        return str(sd["satz"])
    raw = (res.get("text") or "").strip()
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and obj.get("satz"):
            return str(obj["satz"])
    except Exception:
        pass
    return raw or None


async def rewrite_diagnose_sentences(
    text: str,
    *,
    generate: Optional[Callable[..., Awaitable[dict]]] = None,
    model: Optional[str] = None,
    max_sentences: int = 6,
) -> tuple[str, dict]:
    """Ersetzt zirkulaere Diagnose-Saetze im Anamnesetext. Nie eine
    Exception nach aussen - im Zweifel bleibt der Text unveraendert."""
    from app.services.quality_check import diagnose_sentences
    t0 = time.time()
    tel: dict = {"sentences": 0, "replaced": 0, "kept": 0, "mode": "none", "pairs": []}
    try:
        targets = [c for c in diagnose_sentences(text or "") if c["kind"] == "zirkulaer"][:max_sentences]
    except Exception as e:  # pragma: no cover - defensiv
        logger.warning("diagnose_rewrite: Klassifikation fehlgeschlagen: %s", e)
        return text, tel
    tel["sentences"] = len(targets)
    if not targets:
        return text, tel
    if generate is None:
        from app.services.llm import generate_text as generate  # type: ignore

    out = text
    modes: set[str] = set()
    for c in targets:
        sent = c["sentence"]
        if sent not in out:
            tel["kept"] += 1
            tel["pairs"].append({"before": sent[:300], "after": None, "how": "nicht_gefunden"})
            continue
        new: Optional[str] = None
        how = ""
        try:
            cand = await _llm_rewrite(sent, generate, model)
            ok, why = verify_rewrite(sent, cand or "")
            if ok:
                new, how = cand.strip().strip('"').strip(), "llm"
            else:
                logger.info("diagnose_rewrite: LLM-Vorschlag abgelehnt (%s): %r", why, (cand or "")[:160])
                how = f"llm_abgelehnt:{why}"
        except Exception as e:
            logger.warning("diagnose_rewrite: LLM-Call fehlgeschlagen: %s", e)
            how = "llm_fehler"
        if new is None:
            fb = strip_frame_clause(sent)
            if fb:
                ok, why = verify_rewrite(sent, fb)
                if ok:
                    new, how = fb, "fallback"
        if new is None:
            tel["kept"] += 1
            tel["pairs"].append({"before": sent[:300], "after": None, "how": how or "kein_fallback"})
            continue
        out = out.replace(sent, new, 1)
        tel["replaced"] += 1
        modes.add(how)
        tel["pairs"].append({"before": sent[:300], "after": new[:300], "how": how})
        logger.warning("diagnose_rewrite (%s): %r -> %r", how, sent[:120], new[:120])
    tel["mode"] = "mixed" if len(modes) > 1 else (next(iter(modes)) if modes else "none")
    tel["duration_s"] = round(time.time() - t0, 1)
    return out, tel
