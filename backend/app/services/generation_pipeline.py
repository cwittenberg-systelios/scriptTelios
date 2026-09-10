"""
generation_pipeline.py - Eingabe-Objekte der Generierungs-Pipeline (v19.21, S6a).

Bis v19.20 las create_generate_job() neun Uploads in 18 lokale
`*_bytes`/`*_name`-Variablen und reichte sie zusammen mit 14 Form-Feldern
als Closure in ein 1.000-Zeilen-`_run()`. Das war nicht isoliert testbar
(Komplexitaet 104/101, ruff C901).

Seit S6 buendelt PipelineInput alle Eingaben. Die Pipeline selbst
(run_generation in app/api/jobs.py, S6b) bekommt nur noch dieses Objekt
und den JobState - kein Closure-Zustand mehr. Tests koennen ein
PipelineInput direkt bauen (siehe tests/unit/test_v1921_s6_pipeline.py).

Konvention: Feldnamen entsprechen 1:1 den frueheren Closure-Variablen,
damit die Pipeline-Logik byte-nah uebernommen werden konnte.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Optional

from fastapi import UploadFile

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
