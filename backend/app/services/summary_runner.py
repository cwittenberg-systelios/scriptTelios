"""
summary_runner.py - gemeinsames Geruest der Stage-1-Verdichter (v19.21, R7).

Bis v19.20 trugen transcript_summary.py, verlauf_summary.py und
document_summary.py denselben Ablauf jeweils eigenstaendig: Anti-Think-
Systemzusatz, /no_think-Rahmen im User-Content, Quellenblock mit
>>>TAG<<<-Markern, Modell aufloesen, generate_text mit identischen Stage-1-
Parametern, Chunk-Orchestrierung mit Telemetrie-Merge, Ergebnis-Dict.
Fixes landeten deshalb dreimal (oder zweimal, siehe zip(strict) in S2).

Hier liegt das Geruest EINMAL. Die Verdichter behalten ihre fachlichen
Unterschiede als Strategie (Prompt-Texte, Zielwortzahlen, Retry-Politik,
Audit-Felder) - siehe Modul-Docstrings dort. Die erzeugten Prompts und
LLM-Parameter sind byte-identisch zu v19.20 (Snapshot-Test
tests/unit/test_v1921_r7_summary_runner.py).
"""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Stage-1-Parameter fuer generate_text - fuer alle Verdichter gleich.
# v19.2.1: skip_aggressive_dedup - thematische Wiederholungen in Synthesen
#          sind strukturell erwuenscht, nicht echte Dopplungen.
# v19.2.2: force_hard_no_think - Verdichtungs-Tasks triggern bei Qwen3 lange
#          Think-Bloecke trotz "think":False + /no_think (ollama/12907).
STAGE1_GENERATE_KWARGS = dict(
    workflow=None,               # kein BASE_PROMPT, kein Primer
    skip_aggressive_dedup=True,
    force_hard_no_think=True,
)


def word_count(text: Optional[str]) -> int:
    return len(text.split()) if text else 0


def anti_think_suffix(verb: str, start_hint: str = "") -> str:
    """System-Prompt-Anhang gegen inneres Nachdenken (v19.2.2, defense in
    depth zusammen mit doppeltem /no_think und Temperatur >= 0.3).
    verb: "Verdichtung" | "Zusammenfassung"; start_hint: Satz, womit der
    Output beginnen soll (leer = kein Hinweis)."""
    return (
        "\n\nWICHTIG: KEIN INNERES NACHDENKEN. "
        f"Schreibe direkt die {verb}. "
        "KEINE <think>-Tags, KEINE Meta-Reflexion, KEINE Vorbemerkungen."
        + (f" {start_hint}" if start_hint else "")
    )


def source_block(
    *,
    label: str,
    tag: str,
    text: str,
    patient_initial: Optional[str] = None,
    workflow: Optional[str] = None,
) -> str:
    """Kopf des User-Contents: Patient, Workflow-Kontext, Quelle in Markern."""
    return (
        (f"AKTUELLER PATIENT: {patient_initial}\n\n" if patient_initial else "")
        + (f"WORKFLOW-KONTEXT: {workflow}\n\n" if workflow else "")
        + f"QUELLE — {label}:\n"
        + f">>>{tag}<<<\n"
        + text
        + f"\n>>>/{tag}<<<\n\n"
    )


def wrap_no_think(body: str) -> str:
    """/no_think doppelt - Anfang UND Ende (v19.2.2). llm.py haengt am Ende
    ohnehin /no_think an (idempotent), der Anfang wirkt staerker."""
    return "/no_think\n\n" + body + "\n\n/no_think"


async def stage1_generate(
    system_prompt: str,
    user_content: str,
    *,
    max_tokens: int,
    temperature: float,
    model: Optional[str] = None,
) -> dict:
    """Ein Stage-1-LLM-Call mit den gemeinsamen Parametern. model=None ->
    garantiert geladenes Verdichtungsmodell (v19.5.2, resolve_summary_model)."""
    # Lokal-Import: llm.py kennt die Verdichter nicht (Zirkelimport).
    from app.services.llm import generate_text, resolve_summary_model
    if model is None:
        model = await resolve_summary_model()
    return await generate_text(
        system_prompt=system_prompt,
        user_content=user_content,
        max_tokens=max_tokens,
        model=model,
        temperature_override=temperature,
        **STAGE1_GENERATE_KWARGS,
    )


def merge_chunk_telemetry(parts: list[dict]) -> dict:
    return {
        "chunked": True,
        "chunks": len(parts),
        "tokens_hit_cap": any(t.get("tokens_hit_cap") for t in parts),
        "input_truncated": any(t.get("input_truncated") for t in parts),
        "parts": parts,
    }


async def run_chunked(
    *,
    raw_text: str,
    chunks: list[str],
    total_target: int,
    summarize_part: Callable[[str, int], Awaitable[dict]],
    part_heading: str,
    header: str,
    log_label: str,
    min_share: int = 300,
) -> dict:
    """Chunk-Orchestrierung (v19.19 S2/S4): jeden Teil separat verdichten
    (summarize_part(chunk, share_words) -> Verdichter-Ergebnis), Ziel-
    wortzahl proportional zur Teil-Laenge verteilen, chronologisch
    zusammenfuegen, Audit-Flags ODER-verknuepfen.

    part_heading: Format mit {i} und {n}, z.B. "Teil {i}/{n} (chronologisch)".
    header:       Format mit {n}, Einleitungszeile vor den Teilen.
    """
    t0 = time.time()
    raw_words = word_count(raw_text)
    chunk_words = [word_count(c) for c in chunks]
    n = len(chunks)
    logger.info(
        "%s chunked: %d Zeichen / %d Woerter -> %d Teile, Ziel %dw",
        log_label, len(raw_text), raw_words, n, total_target,
    )
    parts: list[str] = []
    tel_parts: list[dict] = []
    issues: list = []
    retry_used = cap_retry_used = degraded = False
    sys_prompts: list[str] = []
    user_contents: list[str] = []
    for i, (chunk, cw) in enumerate(zip(chunks, chunk_words, strict=True), start=1):
        share = max(min_share, int(total_target * (cw / max(raw_words, 1))))
        res = await summarize_part(chunk, share)
        parts.append(f"### {part_heading.format(i=i, n=n)}\n\n{res['summary']}")
        tel_parts.append(res.get("telemetry") or {})
        issues.extend(res.get("issues") or [])
        retry_used = retry_used or bool(res.get("retry_used"))
        cap_retry_used = cap_retry_used or bool(res.get("cap_retry_used"))
        degraded = degraded or bool(res.get("degraded"))
        sys_prompts.append(res.get("system_prompt") or "")
        user_contents.append(res.get("user_content") or "")
    summary = header.format(n=n) + "\n\n" + "\n\n".join(parts)
    return {
        "summary":              summary,
        "system_prompt":        sys_prompts[0] if sys_prompts else "",
        "user_content":         (
            f"[CHUNKED: {n} Teile - hier Teil 1/{n}]\n\n"
            + (user_contents[0] if user_contents else "")
        ),
        "raw_word_count":       raw_words,
        "summary_word_count":   word_count(summary),
        "compression_ratio":    round(word_count(summary) / raw_words, 3) if raw_words else 0.0,
        "duration_s":           round(time.time() - t0, 1),
        "telemetry":            merge_chunk_telemetry(tel_parts),
        "retry_telemetry":      {},
        "issues":               issues,
        "retry_used":           retry_used,
        "cap_retry_used":       cap_retry_used,
        "degraded":             degraded,
        "target_words":         total_target,
        "min_acceptable":       0,
    }
