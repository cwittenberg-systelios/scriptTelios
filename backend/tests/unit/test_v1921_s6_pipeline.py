"""v19.21 Sprint S6 (create_generate_job zerlegt): Tests fuer PipelineInput/
UploadBundle (S6a) und die isoliert aufrufbaren Phasen von run_generation
(S6b). Vorher war die Pipeline ein Closure - nur ueber HTTP + Mocks testbar.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.generation_pipeline import (
    PipelineInput, PipelineState, UploadBundle, normalize_geschlecht, parse_dx_list,
)


def _upload(name, data=b"x"):
    up = MagicMock()
    up.filename = name
    up.read = AsyncMock(return_value=data)
    return up


# ── S6a: Eingabe-Objekte ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_bundle_liest_nur_echte_uploads():
    leer = MagicMock(); leer.filename = ""; leer.read = AsyncMock(return_value=b"nie")
    b = await UploadBundle.read(
        audio=_upload("a.mp3", b"AUDIO"), style_file=_upload("s.docx", b"STYLE"),
        selbstauskunft=leer, vorbefunde=None,
    )
    assert b.audio_bytes == b"AUDIO" and b.audio_name == "a.mp3"
    assert b.style_bytes == b"STYLE" and b.style_name == "s.docx"   # style_file -> style_*
    assert b.selbstauskunft_bytes is None and b.selbstauskunft_name is None
    assert b.vorbefunde_bytes is None
    leer.read.assert_not_called()


@pytest.mark.asyncio
async def test_upload_bundle_unbekanntes_feld():
    with pytest.raises(ValueError):
        await UploadBundle.read(gibt_es_nicht=_upload("x.pdf"))


def test_pipeline_input_upload_attribute_und_meta():
    up = UploadBundle(audio_bytes=b"0" * 2_500_000, audio_name="g.mp3", verlaufsdoku_bytes=b"v", verlaufsdoku_name="v.pdf")
    ctx = PipelineInput(workflow="dokumentation", instructions="I", model=None, uploads=up,
                        transcript="  ", bullets="Fokus", style_text=" Stil ", dx_list=["F32.1"],
                        patientenname="  Frau M. ")
    assert ctx.audio_bytes is up.audio_bytes and ctx.verlaufsdoku_name == "v.pdf"
    assert ctx.selbstauskunft_bytes is None
    with pytest.raises(AttributeError):
        ctx.gibt_es_nicht
    assert ctx.patient_kuerzel == "Frau M."
    m = ctx.input_meta()
    assert m["has_audio"] and m["audio_mb"] == 2.5
    assert m["has_verlaufsdoku"] and not m["has_selbstauskunft"]
    assert m["has_transcript"] is False          # nur Whitespace
    assert m["has_style"] and m["has_fokus_themen"]
    assert m["diagnosen"] == ["F32.1"] and m["model_requested"] == "default"


def test_parse_dx_list_und_geschlecht():
    assert parse_dx_list(" F32.1, F41.1 ,, ") == ["F32.1", "F41.1"]
    assert parse_dx_list(None) == [] and parse_dx_list("") == []
    assert normalize_geschlecht(" W ") == "w" and normalize_geschlecht("m") == "m"
    for bad in ("auto", "", None, "divers", "x"):
        assert normalize_geschlecht(bad) is None


# ── S6b: Phasen isoliert ─────────────────────────────────────────────────────

def _job():
    job = MagicMock()
    job._cancel_requested = False
    job.set_progress = MagicMock()
    return job


def _state():
    st = PipelineState()
    st.bands = {}
    st.phase_times = {}
    return st


@pytest.mark.asyncio
async def test_resolve_transcript_text_direkt():
    from app.api.jobs import _resolve_transcript
    ctx = PipelineInput(workflow="dokumentation", instructions="I", model=None, transcript="Hallo Welt")
    st = _state()
    await _resolve_transcript(ctx, _job(), st)
    assert st.transkript_text == "Hallo Welt"
    assert st.transcript_failure_reason is None


@pytest.mark.asyncio
async def test_resolve_transcript_txt_datei_cp1252_fallback():
    from app.api.jobs import _resolve_transcript
    up = UploadBundle(transcript_file_bytes="Gespräch über Ängste".encode("cp1252"),
                      transcript_file_name="t.txt")
    ctx = PipelineInput(workflow="anamnese", instructions="I", model=None, uploads=up)
    st = _state()
    await _resolve_transcript(ctx, _job(), st)
    assert st.transkript_text == "Gespräch über Ängste"


@pytest.mark.asyncio
async def test_resolve_transcript_txt_datei_greift_nicht_wenn_text_da():
    from app.api.jobs import _resolve_transcript
    up = UploadBundle(transcript_file_bytes=b"aus datei", transcript_file_name="t.txt")
    ctx = PipelineInput(workflow="anamnese", instructions="I", model=None, transcript="direkt", uploads=up)
    st = _state()
    await _resolve_transcript(ctx, _job(), st)
    assert st.transkript_text == "direkt"


@pytest.mark.asyncio
async def test_resolve_transcript_ungueltige_p0_id():
    from app.api.jobs import _resolve_transcript
    ctx = PipelineInput(workflow="dokumentation", instructions="I", model=None, p0_recording_id="abc")
    st = _state()
    await _resolve_transcript(ctx, _job(), st)
    assert st.transkript_text == ""
    assert "ungueltig" in st.transcript_failure_reason


@pytest.mark.asyncio
async def test_run_generation_ruft_phasen_in_reihenfolge(monkeypatch):
    """Orchestrierung: alle Phasen genau einmal, in Pipeline-Reihenfolge,
    ISM-Kurzpfad ueberspringt die Dokument-/Stil-/Prompt-Phasen."""
    import app.api.jobs as J
    order = []

    def fake(name, ret=None):
        async def _f(ctx, job, st):
            order.append(name)
            return ret
        return _f

    for n in ("_resolve_transcript", "_extract_sources", "_resolve_style",
              "_resolve_patient_and_gates", "_build_prompts", "_generate"):
        monkeypatch.setattr(J, n, fake(n))
    monkeypatch.setattr(J, "_finalize", fake("_finalize", ret={"text": "ok"}))

    ctx = PipelineInput(workflow="dokumentation", instructions="I", model=None, transcript="t")
    out = await J.run_generation(ctx, _job())
    assert out == {"text": "ok"}
    assert order == ["_resolve_transcript", "_extract_sources", "_resolve_style",
                     "_resolve_patient_and_gates", "_build_prompts", "_generate", "_finalize"]

    order.clear()
    ism = AsyncMock(return_value={"text": "{}"})
    monkeypatch.setattr(J, "_run_ism_generation", ism)
    ctx = PipelineInput(workflow="ism_fragebogen", instructions="I", model=None, transcript="t", ism_n_items=8)
    out = await J.run_generation(ctx, _job())
    assert out == {"text": "{}"}
    assert order == ["_resolve_transcript"]
    assert ism.call_args.kwargs["n_items_raw"] == 8
