"""
generation_pipeline.py - Generierungs-Pipeline (v19.21, S6a-c): Eingabe-Objekte,
run_generation() mit seinen Phasen, Quellen-Gate, Input-Budget-Guard und der
ISM-Kurzpfad. app/api/jobs.py enthaelt nur noch die Routen.

Bis v19.20 las create_generate_job() neun Uploads in 18 lokale
`*_bytes`/`*_name`-Variablen und reichte sie zusammen mit 14 Form-Feldern
als Closure in ein 1.000-Zeilen-`_run()`. Das war nicht isoliert testbar
(Komplexitaet 104/101, ruff C901).

Seit S6 buendelt PipelineInput alle Eingaben. run_generation() (unten)
bekommt nur noch dieses Objekt und den JobState - kein Closure-Zustand mehr. Tests koennen ein
PipelineInput direkt bauen (siehe tests/unit/test_v1921_s6_pipeline.py).

Konvention: Feldnamen entsprechen 1:1 den frueheren Closure-Variablen,
damit die Pipeline-Logik byte-nah uebernommen werden konnte.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields
from typing import Any, Optional

import time as _t

from fastapi import HTTPException, UploadFile

from app.core.config import settings
from app.core.files import size_class as _size_class
from app.services.document_summary import summarize_document
from app.services.embeddings import retrieve_style_examples
from app.services.extraction import extract_style_context, extract_text
from app.services.llm import clean_verlauf_text, deduplicate_paragraphs, generate_text, substitute_patient_placeholders, truncate_style_context
from app.services.prompt_log import _log_output, _log_prompt
from app.services.prompts import build_system_prompt, build_user_content, split_style_examples
from app.services.stage1 import _run_transcript_stage1, _run_verlauf_stage1
from app.services.staging import compute_input_word_budget, plan_source_compression
import app.services.transcription as _transcription

# Upload-Felder des Endpoints -> Praefix der Byte/Name-Attribute.
# (Reihenfolge = Reihenfolge der Form-Felder in create_generate_job.)
UPLOAD_FIELDS: tuple[tuple[str, str], ...] = (
    ("audio",            "audio"),
    ("selbstauskunft",   "selbstauskunft"),
    ("vorbefunde",       "vorbefunde"),
    ("verlaufsdoku",     "verlaufsdoku"),
    ("antragsvorlage",   "antragsvorlage"),
    ("vorantrag",        "vorantrag"),
    ("prozessreflexion", "prozessreflexion"),
    ("style_file",       "style"),
    ("transcript_file",  "transcript_file"),
)


@dataclass
class UploadBundle:
    """Alle Datei-Uploads eines Jobs als Bytes + Originalname (None = nicht
    hochgeladen). Muss VOR dem Background-Task gelesen werden - UploadFile ist
    nicht thread-safe und nach der Response geschlossen."""
    audio_bytes:            Optional[bytes] = None
    audio_name:             Optional[str]   = None
    selbstauskunft_bytes:   Optional[bytes] = None
    selbstauskunft_name:    Optional[str]   = None
    vorbefunde_bytes:       Optional[bytes] = None
    vorbefunde_name:        Optional[str]   = None
    verlaufsdoku_bytes:     Optional[bytes] = None
    verlaufsdoku_name:      Optional[str]   = None
    antragsvorlage_bytes:   Optional[bytes] = None
    antragsvorlage_name:    Optional[str]   = None
    vorantrag_bytes:        Optional[bytes] = None
    vorantrag_name:         Optional[str]   = None
    prozessreflexion_bytes: Optional[bytes] = None
    prozessreflexion_name:  Optional[str]   = None
    style_bytes:            Optional[bytes] = None
    style_name:             Optional[str]   = None
    transcript_file_bytes:  Optional[bytes] = None
    transcript_file_name:   Optional[str]   = None

    @classmethod
    async def read(cls, **uploads: Optional[UploadFile]) -> "UploadBundle":
        """Liest die uebergebenen UploadFiles (Schluessel = Endpoint-Feldname,
        z.B. style_file=...). Leere Uploads (kein Dateiname) zaehlen als
        nicht vorhanden - wie die fruehere `if x and x.filename`-Leiter."""
        bundle = cls()
        for form_name, prefix in UPLOAD_FIELDS:
            up = uploads.get(form_name)
            if up is not None and up.filename:
                setattr(bundle, f"{prefix}_bytes", await up.read())
                setattr(bundle, f"{prefix}_name", up.filename)
        unknown = set(uploads) - {f for f, _ in UPLOAD_FIELDS}
        if unknown:
            raise ValueError(f"Unbekannte Upload-Felder: {sorted(unknown)}")
        return bundle


@dataclass
class PipelineInput:
    """Vollstaendige Eingabe eines Generierungs-Jobs (Form-Felder, bereits
    normalisiert, plus Uploads). `instructions`, `geschlecht_norm` und
    `model` sind die im Endpoint validierten/aufgeloesten Werte."""
    workflow:        str
    instructions:    str
    model:           Optional[str]
    therapeut_id:    Optional[str] = None
    befund_vorlage:  Optional[str] = None
    patientenname:   Optional[str] = None
    geschlecht_norm: Optional[str] = None
    transcript:      Optional[str] = None
    p0_recording_id: Optional[str] = None
    bullets:         Optional[str] = None
    style_text:      Optional[str] = None
    dx_list:         list[str] = field(default_factory=list)
    ism_n_items:     Optional[int] = None
    uploads:         UploadBundle = field(default_factory=UploadBundle)

    # Upload-Attribute direkt am Input verfuegbar machen (ctx.audio_bytes ...),
    # damit die Pipeline-Logik ihre alten Namen behalten konnte.
    def __getattr__(self, name: str):
        if name.endswith(("_bytes", "_name")) and name in _UPLOAD_ATTRS:
            return getattr(self.uploads, name)
        raise AttributeError(name)

    @property
    def patient_kuerzel(self) -> Optional[str]:
        """Kompakte Patientenkennung fuer die Job-Liste (Sprint B): der String
        kommt vom Frontend so, wie er angezeigt werden soll ("Frau M.")."""
        return self.patientenname.strip() if self.patientenname and self.patientenname.strip() else None

    def input_meta(self) -> dict:
        """Performance-Tracking: welche Inputs hat dieser Job?"""
        u = self.uploads
        return {
            "has_audio":            bool(u.audio_bytes),
            "audio_mb":             round(len(u.audio_bytes) / 1e6, 1) if u.audio_bytes else 0,
            "has_selbstauskunft":   bool(u.selbstauskunft_bytes),
            "has_vorbefunde":       bool(u.vorbefunde_bytes),
            "has_verlaufsdoku":     bool(u.verlaufsdoku_bytes),
            "has_antragsvorlage":   bool(u.antragsvorlage_bytes),
            "has_vorantrag":        bool(u.vorantrag_bytes),
            "has_prozessreflexion": bool(u.prozessreflexion_bytes),
            "has_style":            bool(u.style_bytes) or bool(self.style_text and self.style_text.strip()),
            "has_transcript":       bool(self.transcript and self.transcript.strip()),
            "has_fokus_themen":     bool(self.bullets and self.bullets.strip()),
            "diagnosen":            self.dx_list,
            "model_requested":      self.model or "default",
        }


@dataclass
class PipelineState:
    """Phasenuebergreifender Zwischenstand von run_generation (S6b).
    Nur Attribute, die in mehr als einer Phase gelesen/geschrieben werden;
    rein lokale Werte bleiben in den Phasenfunktionen. Namen = die frueheren
    Closure-Locals (fuer die Nachvollziehbarkeit gegen v19.20 beibehalten)."""
    _ex_t0:                         Any = None  # Startzeit Extraktion
    _glossar_source:                Any = None  # Quelltext fuer die Glossar-Konditionalitaet
    _has_audio:                     Any = None
    _has_docs:                      Any = None
    _ocr_warnings:                  Any = None  # OCR-Muell-Warnungen der Extraktion
    _patient_initial_early:         Any = None  # Initiale aus explizitem Namen (fuer Stage-1)
    _stage1_audit:                  Any = None  # Audit der Verlauf-Stage-1
    _style_raw_texts:               Any = None  # Rohtexte der Stilvorlagen (Laengenanker)
    _t0:                            Any = None  # Startzeit LLM-Call
    _transcript_stage1_audit:       Any = None  # Audit der Transkript-Stage-1
    _transcript_summary_text:       Any = None  # Stage-1-Verdichtung des Transkripts
    _transkript_raw_for_result:     Any = None  # Roh-Transkript fuer result_transcript
    antragsvorlage_text:            Any = None
    bands:                          Any = None  # Progress-Bands je Phase (progress_bands.compute_bands)
    patient_name:                   Any = None  # Aufgeloester Patientenname/Anrede
    phase_times:                    Any = None  # Timing je Phase fuer perf_logger
    prozessreflexion_text:          Any = None
    result:                         Any = None  # LLM-Ergebnis (text, telemetry, ...)
    selbstauskunft_empty:           Any = None
    selbstauskunft_text:            Any = None
    style_context:                  Any = None  # Stilbeispiele fuer den Prompt
    style_info:                     Any = None  # Meta (Quelle, Anzahl) der Stilbeispiele
    style_is_example:               Any = None  # True = Volltext-Beispiel statt Stichworte
    system:                         Any = None  # System-Prompt
    transcript_failure_reason:      Any = None  # Warum kein Transkript (fuer QC/Frontend)
    transkript_text:                Any = None  # Gespraechsquelle (ggf. Stage-1-verdichtet)
    user:                           Any = None  # User-Content
    verlaufsdoku_raw_text:          Any = None  # Verlaufsdoku roh
    verlaufsdoku_text:              Any = None  # Verlaufsdoku (ggf. Stage-1-verdichtet)
    vorantrag_text:                 Any = None
    vorbefunde_text:                Any = None
    word_limits:                    Any = None  # (min, max) Woerter aus resolve_length_anchor


_UPLOAD_ATTRS = frozenset(f.name for f in fields(UploadBundle))


def parse_dx_list(diagnosen: Optional[str]) -> list[str]:
    return [d.strip() for d in diagnosen.split(",") if d.strip()] if diagnosen else []


def normalize_geschlecht(geschlecht: Optional[str]) -> Optional[str]:
    """v19.8 Whitelist: alles ausser "w"/"m" (auch "auto", leer, Muell) -> None
    (= aus Quellen ableiten, wie bisher)."""
    if geschlecht and geschlecht.strip().lower() in ("w", "m"):
        return geschlecht.strip().lower()
    return None


# ── v19.21 (S6c): Pipeline-Logik aus app/api/jobs.py verschoben ─────────────

logger = logging.getLogger(__name__)


# Anzeige-Labels fuer die Quellen im Budget-Guard (gehen in den Verdichter-Prompt).
_SOURCE_LABELS: dict[str, str] = {
    "transkript":     "Sitzungstranskript",
    "verlaufsdoku":   "Verlaufsdokumentation",
    "selbstauskunft": "Selbstauskunft des Patienten",
    "vorbefunde":     "Vorbefunde",
    "antragsvorlage": "Antragsvorlage",
    "vorantrag":      "Vorheriger Antrag",
    "prozessreflexion": "Prozessreflexion des Klienten",
}


def _missing_source_error(
    workflow: str,
    *,
    transkript_text: str = "",
    selbstauskunft_text: str = "",
    vorbefunde_text: str = "",
    bullets: str = "",
    transcript_failure_reason: "str | None" = None,
) -> "str | None":
    """v19.16 (G1): Quellen-Gate gegen Konfabulation.

    Gibt eine nutzerverstaendliche Fehlermeldung zurueck, wenn fuer den
    Workflow KEINE inhaltliche Quelle vorliegt - sonst None.

    Hintergrund (Job 58db7006, 2026-08-04): Ein P1-Job lief nach
    wait_for_transcript-Timeout mit leerem Transkript weiter; der Prompt
    enthielt keinen Quellblock und das Modell erfand ein vollstaendiges,
    klinisch plausibel klingendes Dokument aus dem Stil-Referenzwissen.
    Konfabulierte klinische Inhalte sind nie verwertbar - der Job wird
    abgebrochen, bevor ein solches Dokument ueberhaupt entsteht (D1).

    Nur P1 (dokumentation) und P2 (anamnese) haben das Transkript bzw.
    Klienten-Dokumente als inhaltliche Primaerquellen; P2b/P3/P3b/P4 haben
    eigene Pflichtquellen mit eigener Validierung.
    """
    def _has(t: str) -> bool:
        return bool(t and t.strip())

    reason_suffix = f" {transcript_failure_reason}" if transcript_failure_reason else ""

    if workflow == "dokumentation":
        if not _has(transkript_text) and not _has(bullets):
            return (
                "Kein Gespraechsinhalt verfuegbar - die Dokumentation wurde "
                "NICHT erstellt, um ein erfundenes Dokument zu verhindern."
                + reason_suffix
            )
        return None

    if workflow == "anamnese":
        if not (_has(transkript_text) or _has(selbstauskunft_text)
                or _has(vorbefunde_text)):
            return (
                "Keine verwertbare Quelle (Selbstauskunft, Vorbefunde oder "
                "Aufnahmegespraech) verfuegbar - die Anamnese wurde NICHT "
                "erstellt, um ein erfundenes Dokument zu verhindern."
                + reason_suffix
            )
        return None

    return None


async def _apply_input_budget_guard(
    *,
    workflow: str,
    system_prompt: str,
    patient_initial: Optional[str],
    sources: dict[str, dict],
) -> tuple[dict[str, str], dict]:
    """
    v19.4: Kombinierter Input-Budget-Guard.

    Prueft die SUMME aller Quellen gegen ein Wort-Budget (Output zuerst
    reserviert via max_tokens_for, exakte System-Prompt-Groesse abgezogen) und
    verdichtet die groessten noch-rohen Quellen quellentreu nach, bis der
    kombinierte Input passt — statt _sample_uniformly in llm.py das gesamte
    User-Content verlustbehaftet zerhacken zu lassen.

    sources: {label: {"text": str, "compressed": bool}}
    Returns: (updated_texts: {label: neuer_text}, audit: dict)
    """
    if not getattr(settings, "SOURCE_COMPRESSION_ENABLED", True):
        return {}, {"applied": False, "reason": "disabled"}

    budget_words = compute_input_word_budget(
        workflow, system_prompt_chars=len(system_prompt or "")
    )

    plan_input: list[dict] = []
    for label, meta in sources.items():
        text = (meta or {}).get("text") or ""
        if not text.strip():
            continue
        plan_input.append({
            "label":        label,
            "words":        len(text.split()),
            "compressed":   bool(meta.get("compressed")),
            "compressible": True,
        })

    total_before = sum(s["words"] for s in plan_input)
    plan = plan_source_compression(plan_input, budget_words)

    if not plan:
        return {}, {
            "applied":       False,
            "reason":        "within_budget",
            "budget_words":  budget_words,
            "total_words":   total_before,
        }

    logger.info(
        "Input-Budget-Guard: Summe %dw > Budget %dw -> Nachverdichtung: %s",
        total_before, budget_words, plan,
    )

    updated: dict[str, str] = {}
    compressions: list[dict] = []
    for label, target in plan.items():
        text = sources[label]["text"]
        try:
            res = await summarize_document(
                text,
                target_words=target,
                doc_label=_SOURCE_LABELS.get(label, label),
                workflow=workflow,
                patient_initial=patient_initial,
            )
            updated[label] = res["summary"]
            compressions.append({
                "label":         label,
                "raw_words":     res["raw_word_count"],
                "summary_words": res["summary_word_count"],
                "target_words":  target,
                "degraded":      res.get("degraded", False),
                "ok":            True,
            })
        except Exception as e:
            # Roh-Text bleibt; _sample_uniformly in llm.py greift als Notbremse.
            logger.warning(
                "Input-Budget-Guard: Nachverdichtung von '%s' fehlgeschlagen "
                "(%s) - Roh-Text bleibt", label, e,
            )
            compressions.append({"label": label, "ok": False, "error": str(e)[:200]})

    total_after = total_before
    for c in compressions:
        if c.get("ok"):
            total_after -= (c["raw_words"] - c["summary_words"])

    audit = {
        "applied":            True,
        "budget_words":       budget_words,
        "total_words_before": total_before,
        "total_words_after":  total_after,
        "compressions":       compressions,
    }
    return updated, audit


async def _run_ism_generation(
    *,
    job,
    bands: dict,
    transkript_text: str,
    transcript_failure_reason: "str | None",
    themen: "str | None",
    instructions: str,
    model: "str | None",
    n_items_raw: "int | None",
) -> dict:
    """Kompletter Generierungspfad fuer workflow="ism_fragebogen".

    Structured-Output-Call (Ollama format=JSON-Schema, Pattern Befund v19.7)
    mit genau EINEM Retry bei unbrauchbarem JSON. Das validierte Ergebnis
    wird als JSON-String in result["text"] persistiert - das Frontend parst
    es fuer die editierbare Vorschau, der QualityCheck laeuft ueber den
    ISM-Dispatch in quality_check.run_quality_check().

    Bewusst KEINE Quell-Felder fuer den Fidelity-Check gesetzt
    (source_verlauf_text etc.) - SOURCE_FIDELITY greift fuer ISM ohnehin
    nicht (eigener QC), und Repair-Jobs sind fuer diesen Workflow nicht
    vorgesehen (Editierung passiert direkt in der Vorschau).
    """
    from app.services.ism import (
        build_ism_json_schema,
        build_ism_system_prompt,
        build_ism_user_content,
        clamp_n_items,
        validate_ism_payload,
    )
    from app.core.workflows import max_tokens_for as _max_tokens_for

    # Quellen-Gate (Pattern G1/v19.16): ohne Gespraechsinhalt KEIN Fragebogen -
    # ein aus Referenzwissen konfabulierter Fragebogen waere klinisch wertlos.
    if not (transkript_text and transkript_text.strip()):
        reason_suffix = f" {transcript_failure_reason}" if transcript_failure_reason else ""
        raise RuntimeError(
            "Kein Gespraechsinhalt verfuegbar - der ISM-Fragebogen wurde "
            "NICHT erstellt, um erfundene Items zu verhindern. Bitte in P0 "
            "ein Gespraech mit fertigem Transkript auswaehlen." + reason_suffix
        )

    n_items = clamp_n_items(n_items_raw)
    system = build_ism_system_prompt(instructions, n_items)
    user = build_ism_user_content(transkript_text, themen)
    schema = build_ism_json_schema()
    max_tok = _max_tokens_for("ism_fragebogen", fallback=3500)

    lb = bands["llm"]
    from app.core.workflows import expected_tokens_for as _expected_tokens_for
    expected_tok = _expected_tokens_for("ism_fragebogen", fallback=900)

    def _on_tok(n):
        pct = lb[0] + (lb[1] - lb[0]) * min(1.0, n / expected_tok)
        job.set_progress(int(pct), "KI-Generierung", f"{n} Wörter")

    job.set_progress(lb[0], "KI-Generierung", f"ISM-Fragebogen ({n_items} Items)")

    fb = None
    result = None
    last_errors: list[str] = []
    for attempt in (1, 2):
        if job._cancel_requested:
            raise RuntimeError("__CANCELLED__")
        _log_prompt(job.job_id, "ism_fragebogen", f"ism_attempt{attempt}",
                    system, user)
        result = await generate_text(
            system, user, max_tokens=max_tok, model=model,
            workflow="ism_fragebogen", on_progress=_on_tok,
            response_format=schema,
        )
        _sd = result.get("structured_data")
        _log_output(job.job_id, "ism_fragebogen", f"ism_attempt{attempt}",
                    result.get("text") or "", result.get("telemetry"))
        if result.get("structured_parse_error") or not isinstance(_sd, dict):
            last_errors = ["LLM-Antwort war kein parsebares JSON-Objekt."]
            logger.warning(
                "ISM [%s] Versuch %d: JSON unbrauchbar (parse_error=%s, "
                "type=%s)%s",
                job.job_id, attempt, result.get("structured_parse_error"),
                type(_sd).__name__,
                " - Retry." if attempt == 1 else " - Abbruch.",
            )
            continue
        fb, hard_errors = validate_ism_payload(_sd, n_items)
        if fb is not None:
            break
        last_errors = hard_errors
        logger.warning(
            "ISM [%s] Versuch %d strukturell invalide: %s%s",
            job.job_id, attempt, "; ".join(hard_errors[:3]),
            " - Retry." if attempt == 1 else " - Abbruch.",
        )

    if fb is None:
        raise RuntimeError(
            "Der ISM-Fragebogen konnte nicht in gueltiger Struktur erzeugt "
            "werden (auch nach Wiederholung). Details: "
            + "; ".join(last_errors[:3])
        )

    if len(fb.items) != n_items:
        # Weiche Abweichung: loggen, Ergebnis trotzdem liefern (D1c erlaubt
        # Spielraum; der Therapeut editiert in der Vorschau).
        logger.info(
            "ISM [%s]: %d Items geliefert (angefordert: %d) - Ergebnis wird "
            "trotzdem uebernommen.",
            job.job_id, len(fb.items), n_items,
        )

    import json as _json
    result_json = _json.dumps(
        fb.model_dump(), ensure_ascii=False, indent=2
    )

    tel = result.get("telemetry") or {}
    return {
        "text":        result_json,
        "befund_text": None,
        "akut_text":   None,
        "transcript":  transkript_text or None,
        "model_used":  result.get("model_used"),
        "style_info":  None,
        "ocr_warnings": None,
        "generation_telemetry": {
            **tel,
            "retry_used":      result.get("retry_used", False),
            "degraded":        result.get("degraded", False),
            "degraded_reason": result.get("degraded_reason"),
            "structured_output": True,
            "ism_n_items_requested": n_items,
            "ism_n_items_delivered": len(fb.items),
        },
    }


async def run_generation(ctx: PipelineInput, job) -> dict:
    """v19.21 (S6b): Die Generierungs-Pipeline eines Jobs - vorher das
    1.000-Zeilen-Closure `_run()` in create_generate_job(). Eingaben kommen
    aus `ctx` (PipelineInput), Zwischenstand lebt in `st` (PipelineState),
    Fortschritt geht ueber `job` (JobState). Wird von job_queue.run_job()
    als Coroutine ausgefuehrt. Die Phasen sind einzeln testbar; ihre Logik
    ist byte-nah aus dem Closure uebernommen (Closure-Variablen -> ctx.*,
    phasenuebergreifende Locals -> st.*).
    """
    from app.services.progress_bands import compute_bands

    st = PipelineState()
    # Bands + Timing frühzeitig initialisieren (werden in allen Phasen gebraucht)
    st._has_audio = bool(ctx.audio_bytes)
    st._has_docs = bool(ctx.verlaufsdoku_bytes or ctx.antragsvorlage_bytes or ctx.selbstauskunft_bytes)
    st.bands = compute_bands(ctx.workflow, has_audio=st._has_audio, has_docs=st._has_docs)
    st.phase_times = {}

    await _resolve_transcript(ctx, job, st)

    # ── v19.18 (PX): ISM-Fragebogen - dedizierter Kurzpfad ───────────
    # Der Workflow braucht NUR das Transkript (+ optionale Themen).
    # Die gesamte nachfolgende Dokumenten-/Stil-/Namens-/Budget-
    # Maschinerie ist fuer JSON-Output irrelevant bis kontraproduktiv
    # (Stilbeispiele aus der pgvector-Bibliothek wuerden klinischen
    # Fliesstext-Stil in einen Fragebogen-Prompt injizieren) - deshalb
    # frueher Return, vollstaendig gekapselt in _run_ism_generation().
    if ctx.workflow == "ism_fragebogen":
        return await _run_ism_generation(
            job=job,
            bands=st.bands,
            transkript_text=st.transkript_text,
            transcript_failure_reason=st.transcript_failure_reason,
            themen=ctx.bullets,
            instructions=ctx.instructions,
            model=ctx.model,
            n_items_raw=ctx.ism_n_items,
        )

    await _extract_sources(ctx, job, st)
    await _resolve_style(ctx, job, st)
    await _resolve_patient_and_gates(ctx, job, st)
    await _build_prompts(ctx, job, st)
    await _generate(ctx, job, st)
    return await _finalize(ctx, job, st)


async def _resolve_transcript(ctx: PipelineInput, job, st: PipelineState) -> None:
    """Phase 1: Transkript ermitteln - Audio (Whisper), Transkript-Datei (.txt/.docx) oder P0-Recording (DB, ggf. warten)."""
    from pathlib import Path as _Path
    from app.core.files import upload_dir
    import uuid as _uuid
    # ── 1. Audio transkribieren ──────────────────────────────────
    st.transkript_text = ctx.transcript or ""
    if ctx.audio_bytes and ctx.audio_name:
        suffix = _Path(ctx.audio_name).suffix.lower()
        audio_path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
        audio_path.write_bytes(ctx.audio_bytes)
        if "transcription" in st.bands:
            job.set_progress(st.bands["transcription"][0], "Audio-Transkription")
        st._t0 = _t.time()
        tr = await _transcription.transcribe_audio(audio_path)
        st.phase_times["transcription"] = _t.time() - st._t0
        if "transcription" in st.bands:
            job.set_progress(st.bands["transcription"][1])
        st.transkript_text = tr["transcript"]

    # ── 1a2. Transkript-Datei (.txt/.docx) einlesen (v19.16 G0) ──
    # Direkte Quelle wie Audio; greift nur wenn noch kein Transkript da ist.
    if not st.transkript_text and ctx.transcript_file_bytes and ctx.transcript_file_name:
        _tf_suffix = _Path(ctx.transcript_file_name).suffix.lower()
        if _tf_suffix in (".txt", ".text", ".md"):
            for _enc in ("utf-8", "cp1252", "latin-1"):
                try:
                    st.transkript_text = ctx.transcript_file_bytes.decode(_enc)
                    break
                except UnicodeDecodeError:
                    continue
        else:
            _tf_path = upload_dir() / f"{_uuid.uuid4().hex}{_tf_suffix}"
            _tf_path.write_bytes(ctx.transcript_file_bytes)
            try:
                st.transkript_text = await extract_text(_tf_path)
            except Exception as e:
                logger.warning("Transkript-Datei-Extraktion fehlgeschlagen: %s", e)
        if st.transkript_text:
            logger.info("Transkript aus Datei '%s' (%d Wörter)",
                        ctx.transcript_file_name, len(st.transkript_text.split()))

    # ── 1b. P0-Recording: Transkript aus DB holen (ggf. warten) ──
    # Wenn kein Transkript direkt mitgegeben wurde aber eine Recording-ID,
    # holt jobs.py das Transkript selbst. Ist die Aufnahme noch nicht
    # fertig transkribiert, wird sie in der P0-Queue priorisiert und
    # der Job wartet bis sie bereit ist (max. 10 Min).
    # v19.16 (G2): Grund fuer ein fehlendes Recording-Transkript - fliesst
    # in die Gate-Fehlermeldung (G1) ein, damit der Therapeut versteht
    # was passiert ist und was zu tun ist.
    st.transcript_failure_reason = None
    if not st.transkript_text and ctx.p0_recording_id:
        rec_id_int = None
        try:
            rec_id_int = int(ctx.p0_recording_id)
        except (ValueError, TypeError):
            logger.warning("Ungültige p0_recording_id: %r", ctx.p0_recording_id)
            st.transcript_failure_reason = (
                "Die Aufnahme-Referenz war ungueltig."
            )

        if rec_id_int is not None:
            from app.core.database import async_session_factory as _asf
            from app.models.db import Recording as _Recording
            from sqlalchemy import select as _sel
            from app.core.files import recordings_dir

            async with _asf() as _db:
                _res = await _db.execute(
                    _sel(_Recording).where(_Recording.id == rec_id_int)
                )
                _rec = _res.scalar_one_or_none()

            if _rec and _rec.transcript:
                # Bereits fertig — direkt verwenden
                st.transkript_text = _rec.transcript
                logger.info("P0-Recording %d: Transkript aus DB (%d Wörter)",
                            rec_id_int, len(st.transkript_text.split()))
            elif _rec and _rec.status in ("uploading", "transcribing"):
                # Noch nicht fertig → priorisieren + warten.
                # v19.16 (G3): Timeout skaliert mit der Aufnahmelaenge -
                # fix 600 s reichte fuer eine 62-min-Aufnahme nicht
                # (Fall 58db7006). Formel: max(600, 2x Audiodauer + 120),
                # gedeckelt bei 1800 s. Ohne bekannte Dauer: 900 s.
                from app.api.recordings import reprioritize_recording, wait_for_transcript
                _rec_dur = float(_rec.duration_s or 0.0)
                if _rec_dur > 0:
                    _wait_timeout = int(max(600, min(_rec_dur * 2 + 120, 1800)))
                else:
                    _wait_timeout = 900
                audio_path_rec = recordings_dir() / _rec.filename
                if audio_path_rec.exists():
                    await reprioritize_recording(rec_id_int, audio_path_rec)
                job.set_progress(5, "Warte auf Transkription",
                                 "Aufnahme wird priorisiert transkribiert…")
                st.transkript_text = await wait_for_transcript(rec_id_int, timeout_s=_wait_timeout) or ""
                if st.transkript_text:
                    logger.info("P0-Recording %d: Transkript nach Wartezeit (%d Wörter)",
                                rec_id_int, len(st.transkript_text.split()))
                else:
                    logger.warning("P0-Recording %d: Transkript nach Timeout (%ds) nicht verfügbar",
                                   rec_id_int, _wait_timeout)
                    st.transcript_failure_reason = (
                        f"Die Aufnahme war nach {_wait_timeout // 60} Minuten "
                        "Wartezeit noch nicht fertig transkribiert. Bitte warten "
                        "bis die Aufnahme in P0 'Bereit' zeigt und den Auftrag "
                        "erneut starten."
                    )
            elif _rec and _rec.status == "error":
                logger.warning("P0-Recording %d: Status=error, kein Transkript verfügbar", rec_id_int)
                st.transcript_failure_reason = (
                    "Die Transkription dieser Aufnahme ist fehlgeschlagen "
                    f"({(_rec.error_msg or 'unbekannter Fehler')[:160]}). In P0 kann die "
                    "Transkription erneut gestartet werden."
                )
            else:
                logger.warning("P0-Recording %d: nicht gefunden oder unbekannter Status", rec_id_int)
                st.transcript_failure_reason = (
                    "Die gewaehlte Aufnahme wurde nicht gefunden - "
                    "moeglicherweise wurde sie geloescht."
                )

            # v19.16 (T4/D3): Coverage-Luecke des Recordings an den Job
            # heften - der QualityCheck macht daraus ein CRITICAL-Issue.
            if _rec is not None and getattr(_rec, "coverage_gap_s", None):
                job.transcript_coverage_gap_s = float(_rec.coverage_gap_s)

    # Cancel-Check nach Transkription (teuerster Schritt)
    if job._cancel_requested:
        raise RuntimeError("__CANCELLED__")


async def _extract_sources(ctx: PipelineInput, job, st: PipelineState) -> None:
    """Phase 2: Dokumente extrahieren (Selbstauskunft, Vorbefunde, Verlaufsdoku, Antragsvorlage, Vorantrag, Prozessreflexion) inkl. Stage-1-Verdichtung von Verlauf und Transkript und OCR-Guard."""
    from pathlib import Path as _Path
    from app.core.files import upload_dir
    import uuid as _uuid
    # ── 2. Dokumente extrahieren (jedes Feld → eigene Variable) ──

    # P2: Selbstauskunft des Klienten
    if "extraction" in st.bands:
        job.set_progress(st.bands["extraction"][0], "Dokument-Extraktion")
    st._ex_t0 = _t.time()

    # P2: Helper fuer OCR-Mülldaten-Validierung. Loggt Auffaelligkeiten und
    # gibt im Job einen Warn-Hinweis zurueck. Verworfen wird nichts hart -
    # die Klinik soll trotzdem ein Ergebnis bekommen, aber im UI sehen
    # dass die Quelle problematisch war.
    from app.services.extraction import validate_or_reject
    st._ocr_warnings: list[str] = []

    def _check_ocr_garbage(text: str, label: str, file_name: str = "") -> str:
        """Duenner Wrapper um extraction.validate_or_reject, der den
        Warn-Text in die outer-scope-Liste _ocr_warnings legt und das
        Logging zentral erledigt. Die eigentliche Logik
        (Garbage-Detection + Hard-Reject ab 3+ Problemen) sitzt jetzt
        getestet in extraction.py."""
        text_out, warning = validate_or_reject(text, label, file_name)
        if warning is None:
            return text_out
        logger.warning("[OCR-Validator] %s", warning)
        st._ocr_warnings.append(warning)
        if text_out == "" and text:
            logger.error(
                "[OCR-Validator] %s als unbrauchbar verworfen (Hard-Reject)",
                label,
            )
        return text_out

    st.selbstauskunft_text = ""
    st.selbstauskunft_empty = False   # v19.7: leeres/unextrahierbares Formular?
    if ctx.selbstauskunft_bytes and ctx.selbstauskunft_name:
        suffix = _Path(ctx.selbstauskunft_name).suffix.lower()
        path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
        path.write_bytes(ctx.selbstauskunft_bytes)
        try:
            st.selbstauskunft_text = await extract_text(path)
            st.selbstauskunft_text = _check_ocr_garbage(
                st.selbstauskunft_text, "Selbstauskunft", ctx.selbstauskunft_name)
        except Exception as e:
            logger.warning("Selbstauskunft-Extraktion fehlgeschlagen: %s", e)
            st.selbstauskunft_empty = True
        else:
            from app.services.extraction import source_extraction_is_empty
            st.selbstauskunft_empty = source_extraction_is_empty(path, st.selbstauskunft_text)
        if st.selbstauskunft_empty:
            logger.warning(
                "[QC] Selbstauskunft '%s' ohne verwertbaren Inhalt "
                "(leeres/unausgefuelltes Formular?) - QC-Warnung gesetzt.",
                ctx.selbstauskunft_name)

    # P2: Vorbefunde (Berichte früherer Therapeuten/Kliniken)
    st.vorbefunde_text = ""
    if ctx.vorbefunde_bytes and ctx.vorbefunde_name:
        suffix = _Path(ctx.vorbefunde_name).suffix.lower()
        path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
        path.write_bytes(ctx.vorbefunde_bytes)
        try:
            st.vorbefunde_text = await extract_text(path)
            st.vorbefunde_text = _check_ocr_garbage(
                st.vorbefunde_text, "Vorbefunde", ctx.vorbefunde_name)
        except Exception as e:
            logger.warning("Vorbefunde-Extraktion fehlgeschlagen: %s", e)

    # P3/P4: Verlaufsdokumentation der aktuellen Behandlung
    st.verlaufsdoku_text = ""
    st.verlaufsdoku_raw_text = ""  # v19.2: Roh-Verlauf vor Stage 1 (fuer Audit)
    if ctx.verlaufsdoku_bytes and ctx.verlaufsdoku_name:
        suffix = _Path(ctx.verlaufsdoku_name).suffix.lower()
        path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
        path.write_bytes(ctx.verlaufsdoku_bytes)
        try:
            st.verlaufsdoku_text = await extract_text(path)
            st.verlaufsdoku_text = clean_verlauf_text(st.verlaufsdoku_text)
            st.verlaufsdoku_text = _check_ocr_garbage(
                st.verlaufsdoku_text, "Verlaufsdokumentation", ctx.verlaufsdoku_name)
            # v19.3: Roh-Verlauf SOFORT snapshotten, unabhaengig von Stage-1.
            # Damit ist der Repair-Kontext auch bei kurzen Verlaeufen
            # (< STAGE1_VERLAUF_MIN_WORDS) verfuegbar.
            st.verlaufsdoku_raw_text = st.verlaufsdoku_text
        except Exception as e:
            logger.warning("Verlaufsdoku-Extraktion fehlgeschlagen: %s", e)

    # ── v19.2 Schritt 5: Stage-1-Pipeline (Verlauf-Verdichtung) ────────
    # R2 (2026-07-01): Logik extrahiert nach _run_verlauf_stage1()
    # (Modul-Level, oberhalb von create_generate_job).
    #
    # v19.4 / B-Fix "Frau S.": Den EXPLIZIT uebergebenen Patientennamen schon
    # VOR Stage 1 aufloesen und an die Verdichter durchreichen. Sonst lief
    # summarize_transcript mit patient_initial=None und nahm den Beispielnamen
    # ("Frau S.") aus dem Prompt woertlich — der dann durch die Synthese in
    # den Hauptcall leakte (Substitution dort faengt nur [Patient/in]/Frau X.).
    # Die vollstaendige Namensaufloesung aus Dokumenten passiert weiterhin
    # erst in Schritt 4 (nach Stage 1) — fuer Stage 1 reicht der explizite Name;
    # fehlt er, nutzen die Verdichter neutral "die Patientin/der Patient".
    st._patient_initial_early: Optional[str] = (
        ctx.patientenname.strip() if ctx.patientenname and ctx.patientenname.strip() else None
    )

    st.verlaufsdoku_text, st._stage1_audit = await _run_verlauf_stage1(
        workflow=ctx.workflow,
        verlaufsdoku_text=st.verlaufsdoku_text,
        patient_initial=st._patient_initial_early,
        job=job,
        bands=st.bands,
    )

    # ── v19.3 Transkript-Stage-1 (Transkript-Verdichtung) ──────────────
    # R2 (2026-07-01): Logik extrahiert nach _run_transcript_stage1()
    # (Modul-Level). WICHTIG: Wir snapshotten das Roh-Transkript VOR dem
    # Stage-1-Lauf — die Job-API liefert weiterhin das ROH-Transkript ans
    # Frontend (Therapeut*innen wollen Whisper-Output zum Download),
    # waehrend die LLM-Pipeline (build_user_content, P2-Befund) mit der
    # Verdichtung weiterarbeitet.
    st._transkript_raw_for_result = st.transkript_text
    (
        st.transkript_text,
        st._transcript_summary_text,   # v19.3: fuer Repair-Kontext persistieren
        st._transcript_stage1_audit,
    ) = await _run_transcript_stage1(
        workflow=ctx.workflow,
        transkript_text=st.transkript_text,
        patient_initial=st._patient_initial_early,
        job=job,
        bands=st.bands,
    )

    # P3/P4: Antragsvorlage (EB/VA mit Diagnosen, Anamnese, ohne Verlauf)
    st.antragsvorlage_text = ""
    if ctx.antragsvorlage_bytes and ctx.antragsvorlage_name:
        suffix = _Path(ctx.antragsvorlage_name).suffix.lower()
        path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
        path.write_bytes(ctx.antragsvorlage_bytes)
        try:
            st.antragsvorlage_text = await extract_text(path)
            st.antragsvorlage_text = _check_ocr_garbage(
                st.antragsvorlage_text, "Antragsvorlage", ctx.antragsvorlage_name)
        except Exception as e:
            logger.warning("Antragsvorlage-Extraktion fehlgeschlagen: %s", e)

    # Folgeverlängerung: Vorheriger Bericht (Verlauf + Anamnese + Diagnosen)
    st.vorantrag_text = ""
    if ctx.vorantrag_bytes and ctx.vorantrag_name:
        suffix = _Path(ctx.vorantrag_name).suffix.lower()
        path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
        path.write_bytes(ctx.vorantrag_bytes)
        try:
            st.vorantrag_text = await extract_text(path)
            st.vorantrag_text = _check_ocr_garbage(
                st.vorantrag_text, "Vorantrag", ctx.vorantrag_name)
            logger.info("Vorantrag extrahiert: %s", _size_class(len(st.vorantrag_text)))
        except Exception as e:
            logger.warning("Vorantrag-Extraktion fehlgeschlagen: %s", e)

    # v19.13 P4: Prozessreflexion (Abschlussreflexion des Klienten, optional).
    # Kein Stage-1-Verdichter (typisch 2-5 Seiten, weit unter
    # STAGE1_VERLAUF_MIN_WORDS) - laeuft aber unten durch den
    # Input-Budget-Guard mit. Bewusst NICHT als Namens-/Geschlechtsquelle
    # registriert: Kandidaten-Konsens bleibt Antragsvorlage + Verlaufskopf.
    st.prozessreflexion_text = ""
    if ctx.prozessreflexion_bytes and ctx.prozessreflexion_name:
        suffix = _Path(ctx.prozessreflexion_name).suffix.lower()
        path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
        path.write_bytes(ctx.prozessreflexion_bytes)
        try:
            st.prozessreflexion_text = await extract_text(path)
            st.prozessreflexion_text = _check_ocr_garbage(
                st.prozessreflexion_text, "Prozessreflexion", ctx.prozessreflexion_name)
        except Exception as e:
            logger.warning("Prozessreflexion-Extraktion fehlgeschlagen: %s", e)

    # P2: Wenn ALLE Quell-Texte als unbrauchbar gefiltert wurden, ist die
    # Datenlage zu duenn fuer eine sinnvolle Generierung - frueh abbrechen.
    _all_sources = [st.selbstauskunft_text, st.vorbefunde_text, st.verlaufsdoku_text,
                    st.antragsvorlage_text, st.vorantrag_text, st.prozessreflexion_text]
    if st._ocr_warnings and not any(s and len(s.strip()) > 100 for s in _all_sources):
        # Wir haben kein Transkript noch keinen Text - Hard-Stop.
        if not (st.transkript_text or ctx.transcript or ctx.bullets):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Quelldokumente konnten nicht zuverlaessig gelesen werden. "
                    "Probleme: " + "; ".join(st._ocr_warnings[:3])
                ),
            )


async def _resolve_style(ctx: PipelineInput, job, st: PipelineState) -> None:
    """Phase 3: Stilkontext - Stiltext/Stildatei des Jobs oder Stilbibliothek (pgvector) des Therapeuten."""
    from pathlib import Path as _Path
    from app.core.files import upload_dir
    import uuid as _uuid
    st.style_context = ""
    st.style_is_example = False
    st.style_info = None   # Metadaten: source, chars – wird im Job gespeichert
    # Roh-Texte fuer Wortlimit-Berechnung (vor Destillation/Truncation gesammelt)
    st._style_raw_texts: list[str] = []

    if ctx.style_text and ctx.style_text.strip():
        cleaned = deduplicate_paragraphs(ctx.style_text.strip())
        # v13 Strategie 3: Splitter erkennt "--- Beispiel N ---"-Marker und
        # liefert Einzeltexte zurück. Bei Single-Style-Input ist das eine
        # 1-Element-Liste (Backwards-Compat). Bei Eval-Tests mit
        # vorlage.txt + vorlage2.txt sind es zwei Elemente, was
        # resolve_length_anchor präzisere Wortzahl-Schätzung erlaubt.
        st._style_raw_texts.extend(split_style_examples(cleaned))
        st.style_context = truncate_style_context(cleaned)
        st.style_is_example = True
        _n_examples = len(st._style_raw_texts)
        st.style_info = {
            "source": "text_input",
            "chars": len(st.style_context),
            "words": len(st.style_context.split()),
            "n_examples": _n_examples,
        }
        logger.info(
            "Stilvorlage via Text-Input: %s (%d Beispiel%s)",
            _size_class(len(st.style_context)),
            _n_examples,
            "e" if _n_examples != 1 else "",
        )
    elif ctx.style_bytes and ctx.style_name:
        suffix = _Path(ctx.style_name).suffix.lower()
        path = upload_dir() / f"{_uuid.uuid4().hex}{suffix}"
        path.write_bytes(ctx.style_bytes)
        try:
            # Roh-Text für Wortlimit abgreifen bevor extract_style_context ihn destilliert
            from app.services.extraction import extract_text as _extract_raw
            from app.services.extraction import extract_docx_section as _extract_section
            try:
                if suffix in (".docx", ".doc") and ctx.workflow:
                    _raw = _extract_section(path, ctx.workflow)
                else:
                    _raw = await _extract_raw(path)
                if _raw and len(_raw.split()) >= 50:
                    # v13 Strategie 3: Splitter ist bei Single-File no-op
                    # (eine Datei = ein Beispiel ohne Marker), aber konsistent
                    # mit anderen Kanälen. Falls jemals Multi-Upload kommt,
                    # ist hier nichts zu ändern.
                    st._style_raw_texts.extend(split_style_examples(_raw))
            except Exception:
                pass  # Wortlimit-Berechnung faellt auf Defaults zurueck
            st.style_context = await extract_style_context(path, generate_text, workflow=ctx.workflow)
            st.style_context = truncate_style_context(st.style_context)
            st.style_info = {"source": "file_upload", "filename": ctx.style_name, "chars": len(st.style_context)}
        except Exception as e:
            logger.warning("Stilprofil-Extraktion fehlgeschlagen: %s", e)
    elif ctx.therapeut_id and ctx.therapeut_id.strip():
        # Eigene Session im Background-Task öffnen und explizit schließen.
        # NICHT die Request-Session nutzen – die ist nach Request-Ende geschlossen.
        from app.core.database import async_session_factory
        async with async_session_factory() as db:
            query_text = st.transkript_text or ctx.transcript or ctx.bullets or ""
            st.style_context = await retrieve_style_examples(
                db, ctx.therapeut_id.strip(), ctx.workflow, query_text
            )
        if st.style_context:
            # v13 Strategie 3: pgvector liefert verkettete Beispiele mit
            # "--- Beispiel N ---"-Markern. Vorher wurde die ganze
            # Konkatenation als EIN Eintrag in _style_raw_texts gespeichert
            # → derive_word_limits berechnete die Statistik auf dem
            # konkatenierten Block (z.B. 5 × 400w = 2000w) und das
            # Längen-Limit landete weit über dem Workflow-Default.
            # Jetzt: aufgesplittet, derive_word_limits mittelt sauber
            # über die Einzelvorlagen.
            st._style_raw_texts.extend(split_style_examples(st.style_context))
            _n_examples = len(st._style_raw_texts)
            st.style_info = {
                "source": "style_library",
                "therapeut_id": ctx.therapeut_id.strip(),
                "chars": len(st.style_context),
                "n_examples": _n_examples,
            }
            logger.info(
                "Stilvorlagen aus pgvector: %d Beispiel%s für %s",
                _n_examples,
                "e" if _n_examples != 1 else "",
                ctx.therapeut_id.strip(),
            )


async def _resolve_patient_and_gates(ctx: PipelineInput, job, st: PipelineState) -> None:
    """Phase 4: Laengenanker, Patientenname/Geschlecht (explizit > Dokumente > Fallback), Job-Metadaten fuer den QualityCheck, Truncation-Audit und Quellen-Gate."""
    from app.services.prompts import resolve_length_anchor
    from app.core.workflows import word_limit_for

    _anchor = resolve_length_anchor(
        workflow=ctx.workflow,
        style_raw_texts=st._style_raw_texts if st._style_raw_texts else None,
        workflow_default=word_limit_for(ctx.workflow, fallback=(200, 800)),
    )
    st.word_limits = (_anchor["min"], _anchor["max"])
    logger.info(
        "Längenanker für %s: %d–%d Wörter (target=%d, source=%s, n_substantial=%d)",
        ctx.workflow, _anchor["min"], _anchor["max"], _anchor["target"],
        _anchor["source"], _anchor["n_substantial"],
    )

    # 4. Patientennamen ermitteln — Reihenfolge:
    #    a) Explizit uebergeben (vor allem P1 Gespraechszusammenfassung)
    #    b) Aus antragsvorlage/vorantrag (Briefkopf "Wir berichten ueber ...")
    #    c) Aus selbstauskunft (Seite 1, "Nachname: ...")
    #    d) Fallback: verlaufsdoku / vorbefunde
    from app.services.extraction import extract_patient_name, parse_explicit_patient_name
    st.patient_name = None
    # v19.8: Herkunft der Anrede mitfuehren -> gender_source im Dict.
    _pn_source: Optional[str] = None

    # a) Explizit uebergeben
    if ctx.patientenname and ctx.patientenname.strip():
        st.patient_name = parse_explicit_patient_name(ctx.patientenname.strip())
        if st.patient_name:
            _pn_source = "explicit"
            logger.info("Patientenname explizit uebergeben: %s %s.",
                        st.patient_name["anrede"], st.patient_name["initial"])

    # b-d) Fallback: aus Dokumenten extrahieren
    # v19.12: extract_patient_name arbeitet mit Kandidaten-Konsens und
    # liefert quelle/anker als Telemetrie mit. Dissens innerhalb eines
    # Dokuments -> None (Warnung kommt aus der Extraktion selbst).
    if not st.patient_name:
        for src_text in (st.antragsvorlage_text, st.vorantrag_text, st.selbstauskunft_text, st.verlaufsdoku_text, st.vorbefunde_text):
            if src_text:
                st.patient_name = extract_patient_name(src_text)
                if st.patient_name:
                    _pn_source = "document"
                    logger.info(
                        "Patientenname aus Unterlagen erkannt: %s %s. "
                        "[v19.12-Telemetrie: quelle=%s anker=%s]",
                        st.patient_name["anrede"], st.patient_name["initial"],
                        st.patient_name.get("quelle"), st.patient_name.get("anker"),
                    )
                    break

    # v19.12 Kreuzcheck: Kopfzeile der Verlaufsdoku ("Nachname, Vorname
    # (Aufnahmenr)") gegen den erkannten Namen. Die Verlaufsdoku ist
    # handschriftlich (OCR/Vision) und traegt KEINE Anrede - sie ist nie
    # Geschlechtsquelle, aber ein unabhaengiges Namenssignal. Bei
    # Abweichung wird die Erkennung verworfen: still falsch ist
    # schlechter als offen.
    if st.patient_name and _pn_source == "document" and st.verlaufsdoku_text:
        from app.services.extraction import extract_verlaufskopf_name
        _vk = extract_verlaufskopf_name(st.verlaufsdoku_text)
        if _vk:
            _stamm_doc = (st.patient_name.get("nachname_stamm")
                          or st.patient_name.get("nachname") or "").lower()
            _stamm_vk = (_vk.get("nachname_stamm") or "").lower()
            if _stamm_doc and _stamm_vk and _stamm_doc != _stamm_vk:
                logger.warning(
                    "v19.12 Kreuzcheck: Antragsvorlage (%s) vs. "
                    "Verlaufsdoku-Kopf (%s) nennen verschiedene Nachnamen "
                    "- Erkennung verworfen, Geschlecht bleibt offen.",
                    _stamm_doc, _stamm_vk,
                )
                st.patient_name = None
                _pn_source = None
            elif _stamm_doc and _stamm_vk:
                logger.info(
                    "v19.12 Kreuzcheck: Verlaufsdoku-Kopf bestaetigt "
                    "Nachnamen (%s, Aufnahmenr %s).",
                    _stamm_vk, _vk.get("aufnahmenummer"),
                )

    # v19.8 (Identitaets-Guard, Schritt S2): strukturiertes Geschlecht aus
    # dem UI ueberstimmt jede abgeleitete Anrede. Ist NUR das Geschlecht
    # gesetzt (Kuerzel leer, nichts extrahiert), entsteht ein Dict OHNE
    # initial - alle Konsumenten (substitute_patient_placeholders,
    # build_system_prompt, _check_forbidden_names) pruefen .get("initial")
    # bzw. nachname/vorname und bleiben dann no-op; der QualityCheck
    # (GENDER_MISMATCH, Patch B) nutzt das gender-Feld trotzdem.
    if ctx.geschlecht_norm and not st.patient_name:
        st.patient_name = {"anrede": "", "vorname": "", "nachname": "", "initial": None}
        _pn_source = None
    if st.patient_name:
        if ctx.geschlecht_norm:
            st.patient_name["anrede"] = "Frau" if ctx.geschlecht_norm == "w" else "Herr"
            st.patient_name["gender"] = ctx.geschlecht_norm
            st.patient_name["gender_source"] = "explicit"
        else:
            _g = {"Frau": "w", "Herr": "m"}.get(st.patient_name.get("anrede") or "")
            st.patient_name["gender"] = _g
            st.patient_name["gender_source"] = _pn_source if _g else None
        logger.info(
            "Patient-Identitaet aufgeloest: initial=%s gender=%s source=%s",
            st.patient_name.get("initial"), st.patient_name.get("gender"),
            st.patient_name.get("gender_source"),
        )

    if not st.patient_name:
        # v16 Audit: Frueher (sog. Bug-Fix #4) wurde hier ein Fallback gesetzt:
        #   patient_name = {"initial": "die Klientin/der Klient", ...}
        # Genau dieser String war die URSACHE der "die Klientin/der Klient"-
        # Kontamination im Output - er wurde via Replace-Logik in
        # build_system_prompt durch ALLE [Patient/in]-Platzhalter im Glossar
        # und in Few-Shots ersetzt. Loesung in v16: KEIN Fallback mehr.
        #
        # Statt dessen: patient_name bleibt None. Dann:
        #   - Replace-Logik in prompts.py ueberspringt die Substitution
        #   - [Patient/in]-Platzhalter in den Few-Shots bleiben stehen
        #   - Das Modell ersetzt sie kontextuell richtig (mit dem Namen
        #     den es aus dem Transkript ableitet, oder mit "Frau X."/
        #     einer plausiblen Bezeichnung).
        # Eval-Verifikation: dok-01 v15 zeigt korrekten Output mit
        # "Herr M." obwohl kein expliziter Patientenname gesetzt war.
        logger.warning(
            "Kein Patientenname ermittelbar (Workflow=%s) - Modell muss "
            "aus den Quellen ableiten, [Patient/in]-Platzhalter bleiben stehen",
            ctx.workflow,
        )

    # v19.6: Kontext fuer den QualityCheck (laeuft in run_job nach DONE) auf
    # dem Job hinterlegen - in-process, wird dort per getattr gelesen.
    job.patient_name = st.patient_name   # Datenschutz-Namensleck-Check (Punkt 1)
    job.fokus_themen = ctx.bullets        # Stichpunkt/Fokus-Themen-Check (Punkt 6)
    job.selbstauskunft_empty = st.selbstauskunft_empty  # v19.7: leere Selbstauskunft (P2)
    # v19.13: Reflexions-Referenz-Check (P4) - nur wenn Reflexion vorhanden.
    job.prozessreflexion_present = bool(st.prozessreflexion_text and st.prozessreflexion_text.strip())
    # v19.15 (B3): Antragsvorlagen-Text fuer den Platzhalter-Check
    # (TEMPLATE_PLACEHOLDER_DETECTED) - erkennt Muster-/Stilvorlagen im
    # Antragsvorlage-Slot ("Herr X", "N.N.", ...).
    job.antragsvorlage_qc_text = st.antragsvorlage_text or None
    # v19.19 (A3b): Einweisungsdiagnosen fuer den Kriterien-Check (P2).
    job.diagnosen_qc = list(ctx.dx_list) if ctx.dx_list else None
    # v19.15 (C1): Trunkierungs-Heuristik ueber die dokumentartigen
    # Quellen (Selbstauskunft bewusst ausgenommen - Formulare enden
    # regulaer auf Labels/Kurztokens und wuerden Fehlalarme erzeugen).
    # Rohtexte VOR dem Budget-Guard, d.h. wie extrahiert.
    from app.services.extraction import looks_truncated, truncation_tail
    _trunc: list[dict] = []
    for _src_name, _src_text in (
        ("Verlaufsdokumentation", st.verlaufsdoku_text),
        ("Antragsvorlage",        st.antragsvorlage_text),
        ("Vorheriger Antrag",     st.vorantrag_text),
        ("Prozessreflexion",      st.prozessreflexion_text),
    ):
        if _src_text and looks_truncated(_src_text):
            _trunc.append({
                "source": _src_name,
                "tail":   truncation_tail(_src_text),
            })
            logger.warning(
                "Quelle '%s' endet vermutlich unvollstaendig "
                "(Job %s): ...%s",
                _src_name, job.job_id, truncation_tail(_src_text),
            )
    job.truncated_sources = _trunc or None

    # v19.16 (G1): Quellen-Gate gegen Konfabulation - VOR jeder weiteren
    # (teuren) Verarbeitung. Wirft mit nutzerverstaendlicher Meldung;
    # job_queue uebernimmt str(e) als error_msg.
    _gate_msg = _missing_source_error(
        ctx.workflow,
        transkript_text=st.transkript_text,
        selbstauskunft_text=st.selbstauskunft_text,
        vorbefunde_text=st.vorbefunde_text,
        bullets=ctx.bullets or "",
        transcript_failure_reason=st.transcript_failure_reason,
    )
    if _gate_msg:
        logger.error("Quellen-Gate (%s): %s", ctx.workflow, _gate_msg)
        raise RuntimeError(_gate_msg)


async def _build_prompts(ctx: PipelineInput, job, st: PipelineState) -> None:
    """Phase 5: System-Prompt (inkl. Geschlechtshinweis, Glossar-Konditionalitaet), Input-Budget-Guard und User-Content."""
    effective_instructions = ctx.instructions
    if ctx.geschlecht_norm and "KLIENT-GESCHLECHT" not in effective_instructions:
        _g_anrede = "Frau" if ctx.geschlecht_norm == "w" else "Herr"
        _g_wort = "weiblich" if ctx.geschlecht_norm == "w" else "männlich"
        _g_adj = "weibliche" if ctx.geschlecht_norm == "w" else "männliche"
        _g_hint = (
            f"\n\nKLIENT-GESCHLECHT: {_g_wort} – verwende konsequent "
            f"{_g_adj} Pronomen und Endungen."
        )
        if st.patient_name and st.patient_name.get("initial"):
            _g_hint += (
                f' Verwende als Namenskürzel durchgehend '
                f'"{st.patient_name["initial"]}" (z.B. "{_g_anrede} {st.patient_name["initial"]}").'
            )
        effective_instructions = effective_instructions + _g_hint

    # 5. Generieren – jede Variable hat genau eine Bedeutung
    # v18: prompt-Feld → workflow_instructions, neuer Parameter befund_vorlage.
    # `instructions` wurde oben aus workflow_instructions/prompt geholt und validiert.
    # Issue-2: Quellen fuer die Glossar-Konditionalitaet zusammenfuehren.
    # Bewusst die ROH-Quellen VOR dem Budget-Guard (der verdichtet nur -
    # Erkennung auf den volleren Texten ist die konservative Richtung).
    st._glossar_source = "\n".join(t for t in (
        st.transkript_text, st.verlaufsdoku_text, st.selbstauskunft_text,
        st.vorbefunde_text, st.antragsvorlage_text, st.vorantrag_text,
        st.prozessreflexion_text,
    ) if t)
    st.system = build_system_prompt(
        workflow=ctx.workflow,
        workflow_instructions=effective_instructions,
        style_context=st.style_context,
        style_is_example=st.style_is_example,
        diagnosen=ctx.dx_list,
        patient_name=st.patient_name,
        word_limits=st.word_limits,
        source_text=st._glossar_source,
    )
    # ── v19.4: Kombinierter Input-Budget-Guard ───────────────────────────
    # Nach allen isolierten Stage-1-Verdichtungen: prueft die SUMME aller
    # Quellen gegen das Wort-Budget und verdichtet die groessten noch-rohen
    # Quellen (v.a. Selbstauskunft/Vorbefunde) quellentreu nach. Laeuft NACH
    # build_system_prompt (exakte System-Groesse) und VOR build_user_content
    # (das die ggf. verdichteten Quellen konsumiert). Der System-Prompt
    # haengt seit Issue-2 nur SCHWACH von den Quellen ab (Glossar-Wahl auf den
    # ROH-Quellen); der Guard verdichtet quellentreu, die Wahl bleibt gueltig.
    _budget_updated, _budget_audit = await _apply_input_budget_guard(
        workflow=ctx.workflow,
        system_prompt=st.system,
        patient_initial=st._patient_initial_early,
        sources={
            "transkript":     {"text": st.transkript_text,
                               "compressed": bool(st._transcript_stage1_audit
                                                  and st._transcript_stage1_audit.get("applied"))},
            "verlaufsdoku":   {"text": st.verlaufsdoku_text,
                               "compressed": bool(st._stage1_audit
                                                  and st._stage1_audit.get("applied"))},
            "selbstauskunft": {"text": st.selbstauskunft_text, "compressed": False},
            "vorbefunde":     {"text": st.vorbefunde_text,     "compressed": False},
            "antragsvorlage": {"text": st.antragsvorlage_text, "compressed": False},
            "vorantrag":      {"text": st.vorantrag_text,      "compressed": False},
            "prozessreflexion": {"text": st.prozessreflexion_text, "compressed": False},
        },
    )
    if "transkript" in _budget_updated:     st.transkript_text     = _budget_updated["transkript"]
    if "verlaufsdoku" in _budget_updated:   st.verlaufsdoku_text   = _budget_updated["verlaufsdoku"]
    if "selbstauskunft" in _budget_updated: st.selbstauskunft_text = _budget_updated["selbstauskunft"]
    if "vorbefunde" in _budget_updated:     st.vorbefunde_text     = _budget_updated["vorbefunde"]
    if "antragsvorlage" in _budget_updated: st.antragsvorlage_text = _budget_updated["antragsvorlage"]
    if "vorantrag" in _budget_updated:      st.vorantrag_text      = _budget_updated["vorantrag"]
    if "prozessreflexion" in _budget_updated: st.prozessreflexion_text = _budget_updated["prozessreflexion"]
    if _budget_audit.get("applied"):
        logger.info("Input-Budget-Guard Audit: %s", _budget_audit)

    st.user = build_user_content(
        workflow=ctx.workflow,
        transcript=st.transkript_text,
        fokus_themen=ctx.bullets,
        selbstauskunft_text=st.selbstauskunft_text,
        vorbefunde_text=st.vorbefunde_text,
        verlaufsdoku_text=st.verlaufsdoku_text,
        antragsvorlage_text=st.antragsvorlage_text,
        vorantrag_text=st.vorantrag_text,
        prozessreflexion_text=st.prozessreflexion_text,
        diagnosen=ctx.dx_list,
        # v18: custom_prompt wird in build_user_content nicht mehr verwendet
        # (Workflow-Anweisungen leben jetzt im System-Prompt). Parameter
        # bleibt fuer Backwards-Compat in der Signatur.
        patient_name=st.patient_name,
    )


async def _generate(ctx: PipelineInput, job, st: PipelineState) -> None:
    """Phase 6: LLM-Generierung (Anamnese: zwei Calls Anamnese + Befund; sonst ein Call) mit Fortschritt und Perf-Log."""
    from app.core.workflows import max_tokens_for
    max_tok = max_tokens_for(ctx.workflow, fallback=2048)
    from app.services.job_queue import perf_logger

    st.phase_times["extraction"] = _t.time() - st._ex_t0
    if "extraction" in st.bands:
        job.set_progress(st.bands["extraction"][1], "Dokument-Extraktion")

    # Cancel-Check nach Dokument-Extraktion
    if job._cancel_requested:
        raise RuntimeError("__CANCELLED__")

    lb = st.bands["llm"]
    # v13: erwartete Output-Tokens kommen aus dem zentralen WORKFLOWS-Modul.
    from app.core.workflows import expected_tokens_for
    expected_tok = expected_tokens_for(ctx.workflow, fallback=1500)
    def _on_tok(n):
        pct = lb[0] + (lb[1] - lb[0]) * min(1.0, n / expected_tok)
        job.set_progress(int(pct), "KI-Generierung", f"{n} Wörter")

    # Cancel-Check vor LLM-Generierung (zweitteuerster Schritt)
    if job._cancel_requested:
        raise RuntimeError("__CANCELLED__")

    job.set_progress(lb[0], "KI-Generierung")
    st._t0 = _t.time()

    # ── v16: Postprocessor-Parameter berechnen ──────────────────────────
    # max_words: harte Obergrenze fuer den Output (style-derived).
    # Wenn das Modell das Limit ueberschreitet, schneidet
    # postprocess_output an einer Satzgrenze ab.
    # Verwendet das schon weiter oben berechnete word_limits[1] - kein
    # zweiter Aufruf von derive_word_limits noetig.
    v16_max_words = st.word_limits[1] if st.word_limits else None

    # expected_keywords: wichtige Source-Begriffe (Trennung, ADS, F33.1 etc.)
    # die im Output vorhanden sein muessten. Wenn nicht: Warning im Log.
    v16_expected_keywords: list[str] = []
    try:
        from app.services.postprocessing import extract_likely_keywords
        _src_combined = " ".join(filter(None, [
            st.transkript_text or "",
            st.selbstauskunft_text or "",
            st.vorbefunde_text or "",
            st.verlaufsdoku_text or "",
            st.antragsvorlage_text or "",
            st.vorantrag_text or "",
            st.prozessreflexion_text or "",
        ]))
        v16_expected_keywords = extract_likely_keywords(_src_combined)
        if v16_expected_keywords:
            logger.info(
                "v16 Keyword-Check: erwarte %d Source-Keywords im Output: %s",
                len(v16_expected_keywords), v16_expected_keywords,
            )
    except ImportError:
        pass
    except Exception as _e:
        logger.warning("Keyword-Extraktion fehlgeschlagen: %s", _e)

    # Anamnese: ZWEI sequenzielle LLM-Calls (Anamnese + Befund separat)
    # Vorteil: zuverlaessige Trennung ohne Marker, fokussierte Prompts
    if ctx.workflow == "anamnese":
        # Call 1: Anamnese-Fließtext (max ~60% des Token-Budgets)
        anamnese_max_tok = int(max_tok * 0.6)
        def _on_tok_a(n):
            # Erste Haelfte des LLM-Bands fuer Anamnese
            mid = lb[0] + (lb[1] - lb[0]) * 0.5
            pct = lb[0] + (mid - lb[0]) * min(1.0, n / (expected_tok * 0.6))
            job.set_progress(int(pct), "KI-Generierung", f"Anamnese: {n} Wörter")
        _log_prompt(job.job_id, ctx.workflow, "anamnese", st.system, st.user)
        result_a = await generate_text(st.system, st.user, max_tokens=anamnese_max_tok,
                                        model=ctx.model, workflow=ctx.workflow, on_progress=_on_tok_a,
                                        max_words=v16_max_words,
                                        expected_keywords=v16_expected_keywords)
        anamnese_text = (result_a.get("text") or "").strip()
        _log_output(job.job_id, ctx.workflow, "anamnese", anamnese_text,
                    result_a.get("telemetry"))

        # Cancel-Check zwischen den Calls
        if job._cancel_requested:
            raise RuntimeError("__CANCELLED__")

        # Call 2: Befund (mit eigenem Prompt + Anamnese als zusaetzlichem Kontext)
        # v18: build_system_prompt uebernimmt die Vorlage-Substitution.
        # befund_vorlage kommt aus dem Frontend (Form-Feld), Default = BEFUND_VORLAGE.
        befund_system = build_system_prompt(
            workflow="befund",
            workflow_instructions="",  # Befund hat keinen Frontend-Auftragsteil
            diagnosen=ctx.dx_list,
            befund_vorlage=ctx.befund_vorlage,
            source_text=st._glossar_source,
        )
        # User-Content fuer Befund: Selbstauskunft + Vorbefunde + die generierte Anamnese
        befund_user_parts = []
        if st.selbstauskunft_text:
            befund_user_parts.append(f"SELBSTAUSKUNFT DES PATIENTEN:\n{st.selbstauskunft_text}")
        if st.vorbefunde_text:
            befund_user_parts.append(f"VORBEFUNDE:\n{st.vorbefunde_text}")
        if st.transkript_text:
            befund_user_parts.append(f"AUFNAHMEGESPRÄCH (Transkript):\n{st.transkript_text}")
        befund_user_parts.append(f"BEREITS GENERIERTE ANAMNESE (als Kontext):\n{anamnese_text}")
        befund_user_parts.append("\nErstelle nun den psychopathologischen Befund.")
        befund_user = "\n\n".join(befund_user_parts)

        befund_max_tok = int(max_tok * 0.5)
        def _on_tok_b(n):
            # Zweite Haelfte des LLM-Bands fuer Befund
            mid = lb[0] + (lb[1] - lb[0]) * 0.5
            pct = mid + (lb[1] - mid) * min(1.0, n / (expected_tok * 0.4))
            job.set_progress(int(pct), "KI-Generierung", f"Befund: {n} Wörter")
        # ── v19.7 S2: Structured-Output-Pfad (Ollama format=JSON-Schema) ──
        # Das Modell liefert NUR die Slot-Werte; die (editierbare) Vorlage
        # wird deterministisch im Backend gefuellt -> Fixtext garantiert
        # 100% wortidentisch, kein Markdown-/Praeambel-/Klebebug-Risiko im
        # Vorlagentext. Fallback-Kette (jeweils mit lautem Log):
        #   1. Vorlage ohne {Slots}          -> direkt Freitext-Pfad
        #   2. JSON-Parse-Fehler / leeres Obj -> Freitext-Pfad
        #   3. Unerwartete Exception          -> Freitext-Pfad
        # Der Freitext-Pfad ist der bisherige Call und bleibt unveraendert.
        befund_text_generated = None
        result_b = None
        _structured_used = False
        try:
            from app.services.prompts import (
                BEFUND_VORLAGE as _BEFUND_VORLAGE_DEFAULT,
                build_befund_structured_prompt,
                fill_befund_vorlage,
            )
            _bv = (
                ctx.befund_vorlage
                if (ctx.befund_vorlage and ctx.befund_vorlage.strip())
                else _BEFUND_VORLAGE_DEFAULT
            )
            _sys_struct, _slots, _schema = build_befund_structured_prompt(
                diagnosen=ctx.dx_list,
                befund_vorlage=_bv,
                source_text=st._glossar_source,
            )
            if not _slots:
                logger.info(
                    "Befund structured: Vorlage enthaelt keine {Slots} "
                    "- nutze direkt den Freitext-Pfad."
                )
            else:
                _log_prompt(job.job_id, ctx.workflow, "befund_structured",
                            _sys_struct, befund_user)
                result_b = await generate_text(
                    _sys_struct, befund_user, max_tokens=befund_max_tok,
                    model=ctx.model, workflow="befund", on_progress=_on_tok_b,
                    response_format=_schema,
                )
                _sd = result_b.get("structured_data")
                if (not result_b.get("structured_parse_error")
                        and isinstance(_sd, dict) and _sd):
                    befund_text_generated = fill_befund_vorlage(_bv, _sd).strip()
                    _structured_used = True
                    _empty_slots = [
                        s for s in _slots if not str(_sd.get(s) or "").strip()
                    ]
                    if _empty_slots:
                        logger.warning(
                            "Befund structured: %d leere Slot-Werte "
                            "(-> 'nicht erhoben' eingesetzt): %s",
                            len(_empty_slots), _empty_slots,
                        )
                else:
                    logger.warning(
                        "Befund structured: JSON unbrauchbar "
                        "(parse_error=%s, type=%s) -> Freitext-Fallback.",
                        result_b.get("structured_parse_error"),
                        type(_sd).__name__,
                    )
        except Exception as _e:
            logger.warning(
                "Befund structured: Pfad fehlgeschlagen (%s: %s) "
                "-> Freitext-Fallback.", type(_e).__name__, _e,
            )

        if not _structured_used:
            _log_prompt(job.job_id, ctx.workflow, "befund", befund_system, befund_user)
            # Befund: keine max_words-Cap (Format ist fix), aber Keyword-Check sinnvoll
            result_b = await generate_text(befund_system, befund_user, max_tokens=befund_max_tok,
                                            model=ctx.model, workflow="befund", on_progress=_on_tok_b,
                                            expected_keywords=v16_expected_keywords)
            befund_text_generated = (result_b.get("text") or "").strip()
        _log_output(job.job_id, ctx.workflow, "befund", befund_text_generated,
                    result_b.get("telemetry"))

        # v19.1: Telemetrie beider Anamnese-Calls aggregieren.
        # Worst-case-Logik: wenn EINER der beiden Calls degradiert ist,
        # ist auch der Gesamt-Job degraded markiert.
        tel_a = result_a.get("telemetry") or {}
        tel_b = result_b.get("telemetry") or {}
        agg_telemetry = {
            "anamnese": {
                **tel_a,
                "retry_used":      result_a.get("retry_used", False),
                "degraded":        result_a.get("degraded", False),
                "degraded_reason": result_a.get("degraded_reason"),
            },
            "befund": {
                **tel_b,
                "retry_used":      result_b.get("retry_used", False),
                "degraded":        result_b.get("degraded", False),
                "degraded_reason": result_b.get("degraded_reason"),
                # v19.7 S2: True = Vorlage deterministisch aus
                # Slot-Werten gefuellt; False = Freitext-Pfad (Fallback
                # oder Vorlage ohne Slots).
                "structured_output": _structured_used,
            },
            # Top-Level-Aggregate fuer das Performance-Log
            "retry_used": result_a.get("retry_used", False) or result_b.get("retry_used", False),
            "degraded":   result_a.get("degraded", False)   or result_b.get("degraded", False),
            "degraded_reason": (
                result_a.get("degraded_reason")
                or result_b.get("degraded_reason")
            ),
            "think_ratio": max(
                tel_a.get("think_ratio", 0) or 0,
                tel_b.get("think_ratio", 0) or 0,
            ),
            "tokens_hit_cap": bool(
                tel_a.get("tokens_hit_cap") or tel_b.get("tokens_hit_cap")
            ),
            "used_thinking_fallback": bool(
                tel_a.get("used_thinking_fallback") or tel_b.get("used_thinking_fallback")
            ),
        }

        # Direkt zwei separate Felder zurueckgeben — keine Marker noetig
        st.result = {
            "text": anamnese_text,
            "befund_text": befund_text_generated,
            "transcript": result_a.get("transcript") or result_b.get("transcript"),
            "model_used": result_a.get("model_used"),
            "generation_telemetry": agg_telemetry,
        }
    else:
        _log_prompt(job.job_id, ctx.workflow, ctx.workflow, st.system, st.user)
        st.result = await generate_text(st.system, st.user, max_tokens=max_tok, model=ctx.model,
                                      workflow=ctx.workflow, on_progress=_on_tok,
                                      max_words=v16_max_words,
                                      expected_keywords=v16_expected_keywords)
        _log_output(job.job_id, ctx.workflow, ctx.workflow,
                    st.result.get("text") or "", st.result.get("telemetry"))
        # v19.1: Telemetrie aus generate_text in result["generation_telemetry"]
        # konsolidieren (im Anamnese-Pfad oben schon explizit gesetzt).
        tel = st.result.get("telemetry") or {}
        st.result["generation_telemetry"] = {
            **tel,
            "retry_used":      st.result.get("retry_used", False),
            "degraded":        st.result.get("degraded", False),
            "degraded_reason": st.result.get("degraded_reason"),
        }

    st.phase_times["llm"] = _t.time() - st._t0

    try:
        import json as _j
        perf_logger.info(_j.dumps({
            "workflow": ctx.workflow,
            "phases": st.phase_times,
            "has_audio": st._has_audio,
            "has_docs": st._has_docs,
        }))
    except Exception:
        pass


async def _finalize(ctx: PipelineInput, job, st: PipelineState) -> dict:
    """Phase 7: Platzhalter fuer Patientennamen einsetzen, Ergebnis-Dict fuer JobState zusammenstellen."""
    raw = st.result["text"] or ""

    # Platzhalter-Substitution: "[Patient/in]" etc. durch echten Namen ersetzen.
    # Das Modell kopiert Platzhalter aus Few-Shot-Beispielen manchmal in den Output,
    # obwohl der Name im System-Prompt schon substituiert wurde.
    if st.patient_name:
        raw = substitute_patient_placeholders(raw, st.patient_name)
        if st.result.get("befund_text"):
            st.result["befund_text"] = substitute_patient_placeholders(
                st.result["befund_text"], st.patient_name
            )
        if st.result.get("akut_text"):
            st.result["akut_text"] = substitute_patient_placeholders(
                st.result["akut_text"], st.patient_name
            )

    # Anamnese-Workflow: Befund kommt bereits separat aus dem zweiten LLM-Call.
    # Fuer alle anderen Workflows: kein Befund/Akut-Splitting noetig.
    if ctx.workflow == "anamnese":
        anamnese_part = raw
        befund_part = st.result.get("befund_text") or None
        akut_part = st.result.get("akut_text") or None
    else:
        anamnese_part = raw
        befund_part = None
        akut_part = None

    logger.info(
        "Job %s _run result: raw=%d anamnese=%d befund=%s akut=%s",
        job.job_id, len(raw),
        len(anamnese_part) if anamnese_part else 0,
        len(befund_part) if befund_part else 0,
        len(akut_part) if akut_part else 0,
    )

    return {
        "text":        anamnese_part,
        "befund_text": befund_part,
        "akut_text":   akut_part,
        # v19.3: ROH-Transkript an's Frontend (Therapeut*in soll den
        # ungekuerzten Whisper-Output sehen/herunterladen koennen).
        # transkript_text selbst kann durch Stage-1 mit der Verdichtung
        # ueberschrieben sein; _transkript_raw_for_result haelt das
        # Original (wird unbedingt vor dem Stage-1-Block gesetzt, siehe
        # weiter oben in _run).
        "transcript":  st._transkript_raw_for_result or None,
        "model_used":  st.result["model_used"],
        "style_info":  st.style_info,
        # P2: OCR-Validator-Warnungen ans Frontend durchreichen.
        # UI kann eine Warnbanner anzeigen wenn diese Liste nicht leer ist.
        "ocr_warnings": st._ocr_warnings if st._ocr_warnings else None,
        # v19.1: Think-Block-Telemetrie aus llm.generate_text() an
        # job_queue durchreichen (landet in jobs.generation_telemetry).
        "generation_telemetry": st.result.get("generation_telemetry"),
        # v19.2 Schritt 5: Stage-1-Audit (None wenn Stage 1 nicht relevant).
        # Wird in run_job in JobState.verlauf_summary_audit gespiegelt und
        # mit dem persist-Schritt in die DB-Spalte verlauf_summary_audit
        # geschrieben.
        "verlauf_summary_audit": st._stage1_audit,
        # v19.2 Schritt 7: Der tatsaechliche Stage-1-Text (falls Stage 1
        # erfolgreich war). Wird ebenfalls in JobState gespiegelt und in
        # die DB-Spalte verlauf_summary_text geschrieben. None wenn
        # Stage 1 nicht lief oder fehlgeschlagen ist (in dem Fall steht
        # in verlaufsdoku_text noch das Original).
        "verlauf_summary_text": (
            st.verlaufsdoku_text
            if (st._stage1_audit and st._stage1_audit.get("applied"))
            else None
        ),
        # v19.3: Repair-Kontext-Quellen.
        # source_verlauf_text:         Roh-Verlauf nach clean_verlauf_text,
        #                              UNABHAENGIG von Stage 1.
        # transcript_summary_text:     Synthese des Transkripts wenn
        #                              Transcript-Stage-1 lief.
        # source_antragsvorlage_text:  Antragsvorlage nach extract_text
        #                              (Akutantrag/Verlaengerung/Entlassb.).
        # source_vorantrag_text:       Vorantrag bei Folgeverlaengerung.
        "source_verlauf_text":        st.verlaufsdoku_raw_text or None,
        "transcript_summary_text":    st._transcript_summary_text,
        "source_antragsvorlage_text": st.antragsvorlage_text or None,
        "source_vorantrag_text":      st.vorantrag_text or None,
        # v19.13: Prozessreflexion fuer Repair-Fidelity-Kontext.
        "source_prozessreflexion_text": st.prozessreflexion_text or None,
        # v19.3: Transkript-Stage-1-Audit (None wenn nicht relevant oder
        # Workflow nicht in _TRANSCRIPT_STAGE1_WORKFLOWS).
        "transcript_summary_audit": st._transcript_stage1_audit,
    }
