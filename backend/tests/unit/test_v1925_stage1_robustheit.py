"""v19.25 Sprint S5: Stage-1-Robustheit.

Regressionsbasis (prompts.log):
  16.09. d101a786: 56.637 Zeichen -> 28k + 28k + 637 Zeichen (171w), Rest-Chunk
                   konnte Minimum 300w nie erreichen -> ganze Stufe verworfen.
  17.09. 011ee4e9: Chunk 4.916w, Retry 363w < 393w Min -> ganze Stufe verworfen.
  15.09. 26ee45d3/c93bf8d9: Verlauf 1.867w/2.487w -> 373w/364w < 400w fix.
S5-1 Rest-Chunk-Merge, S5-2 Minimum <= Rohtext/2, S5-3 Teil-Fallback,
S5-4 Toleranzband 80 %, S5-5 Verlauf-Skip 2.500w + relatives Minimum,
S5-6 Prompt + letzter Output bei Fehlschlag im prompts.log.
"""
from unittest.mock import patch

import pytest

from app.services import staging as S
from app.services import summary_runner as R
from app.services.summary_runner import Stage1Error


# ── S5-1 ────────────────────────────────────────────────────────────────────

class TestTailMerge:

    def test_rest_chunk_wird_verschmolzen(self):
        # 2 volle Chunks + kleiner Rest (wie 16.09.: 28k + 28k + 637)
        para = ("Satz mit Inhalt. " * 40).strip() + "\n\n"   # ~680 Zeichen
        text = para * 82 + "Kurzer Rest am Ende.\n"
        chunks = S.chunk_text_by_blocks(text, 28_000)
        assert len(chunks) == 2
        assert chunks[-1].rstrip().endswith("Kurzer Rest am Ende.")
        assert "".join(chunks) == text

    def test_grosser_letzter_chunk_bleibt(self):
        para = ("Satz mit Inhalt. " * 40).strip() + "\n\n"
        text = para * 100                       # ~68k -> 3 Teile, letzter ~12k (> 25 %)
        chunks = S.chunk_text_by_blocks(text, 28_000)
        assert len(chunks) == 3
        assert len(chunks[-1]) >= 28_000 * S.STAGE1_TAIL_MERGE_RATIO

    def test_passt_ganz(self):
        assert S.chunk_text_by_blocks("kurz", 100) == ["kurz"]


# ── S5-2 / S5-5 ─────────────────────────────────────────────────────────────

class TestMinimum:

    def test_transcript_min_nie_ueber_halbem_rohtext(self):
        # 171-Woerter-Rest-Chunk: vorher 300 -> jetzt 85
        assert S.compute_transcript_min_acceptable(300, raw_words=171) == 85
        # Normalfall unveraendert
        assert S.compute_transcript_min_acceptable(983, raw_words=4916) == 393
        assert S.compute_transcript_min_acceptable(983) == 393

    def test_verlauf_min_relativ(self):
        assert S.compute_verlauf_min_acceptable(800) == 400
        assert S.compute_verlauf_min_acceptable(800, raw_words=1867) == 280
        assert S.compute_verlauf_min_acceptable(800, raw_words=2487) == 373
        assert S.compute_verlauf_min_acceptable(800, raw_words=3000) == 400
        assert S.compute_verlauf_min_acceptable(1555, raw_words=12962) == 466

    def test_verlauf_skip_schwelle(self):
        assert S.STAGE1_VERLAUF_MIN_WORDS == 2500
        assert S.verlauf_stage1_skip_reason("entlassbericht", "w " * 1867) is not None
        assert S.verlauf_stage1_skip_reason("entlassbericht", "w " * 2487) is not None
        assert S.verlauf_stage1_skip_reason("entlassbericht", "w " * 2600) is None

    def test_toleranzband(self):
        assert S.within_stage1_tolerance(363, 393)          # 17.09.: 92 %
        assert S.within_stage1_tolerance(320, 400)          # genau 80 %
        assert not S.within_stage1_tolerance(319, 400)
        assert not S.within_stage1_tolerance(400, 400)      # nicht "unter"
        assert not S.within_stage1_tolerance(10, 0)


# ── S5-3 ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_chunked_teil_fallback():
    async def part(chunk, share):
        if chunk == "b":
            raise Stage1Error("zu kurz", system_prompt="SYS", user_content="Ub", last_output="x")
        return {"summary": f"S({chunk})", "telemetry": {}, "issues": [], "retry_used": False,
                "degraded": False, "system_prompt": "SYS", "user_content": f"U{chunk}"}

    res = await R.run_chunked(raw_text="a b c", chunks=["a", "b", "c"], total_target=900, summarize_part=part,
                             part_heading="Teil {i}/{n}", header="[{n} Teile]", log_label="T", min_share=10)
    assert "S(a)" in res["summary"] and "S(c)" in res["summary"]
    assert "[Teil 2/3: Rohtext - Verdichtung fehlgeschlagen (zu kurz)]\n\nb" in res["summary"]
    assert res["degraded"] is True
    assert res["telemetry"]["chunks_failed"] == 1 and res["telemetry"]["chunks"] == 3
    assert any(i["type"] == "stage1_chunk_failed" for i in res["issues"])


@pytest.mark.asyncio
async def test_run_chunked_alle_teile_fehlgeschlagen():
    async def part(chunk, share):
        raise Stage1Error("kaputt", system_prompt="SYS", user_content="U")

    with pytest.raises(Stage1Error) as ei:
        await R.run_chunked(raw_text="a b", chunks=["a", "b"], total_target=900, summarize_part=part,
                            part_heading="Teil {i}/{n}", header="[{n} Teile]", log_label="T")
    assert ei.value.system_prompt == "SYS"


# ── S5-4 (Transkript, Ende-zu-Ende mit gemocktem LLM) ───────────────────────

RAW = ("Die Patientin berichtet von Schlafstörungen und Konflikten im Team. " * 500).strip()  # 4500w


def _llm(words):
    state = {"n": 0}

    async def gen(**kw):
        state["n"] += 1
        n = words[min(state["n"], len(words)) - 1]
        return {"text": ("Satz eins. " * n).strip(), "telemetry": {"tokens_hit_cap": False}}

    async def model():
        return "test-model"
    return gen, model


@pytest.mark.asyncio
async def test_transcript_toleranzband_akzeptiert_degraded():
    from app.services import transcript_summary as T
    # raw=4500w -> target=900, min=360; initial 280w (< 80 %), Retry 340w (>= 288) -> akzeptiert
    gen, model = _llm([140, 170])   # "Satz eins." = 2 Woerter -> 280w / 340w
    with patch("app.services.llm.generate_text", gen), patch("app.services.llm.resolve_summary_model", model):
        res = await T.summarize_transcript(RAW, "dokumentation")
    assert res["min_acceptable"] == 360
    assert res["summary_word_count"] == 340
    assert res["degraded"] is True and res["retry_used"] is True
    assert any(i["type"] == "stage1_short" for i in res["issues"])


@pytest.mark.asyncio
async def test_transcript_unter_toleranz_stage1error_mit_kontext():
    from app.services import transcript_summary as T
    gen, model = _llm([100, 120])   # 200w / 240w < 288
    with patch("app.services.llm.generate_text", gen), patch("app.services.llm.resolve_summary_model", model):
        with pytest.raises(Stage1Error) as ei:
            await T.summarize_transcript(RAW, "dokumentation")
    e = ei.value
    assert "implausibel kurz nach Retry" in str(e)
    assert e.system_prompt and e.user_content and e.last_output.startswith("Satz eins.")


@pytest.mark.asyncio
async def test_transcript_rest_chunk_min_gedeckelt():
    """171-Woerter-Chunk (16.09.): Minimum jetzt 85 statt 300 -> 100w Output reicht."""
    from app.services import transcript_summary as T
    small = ("Wort " * 171).strip()
    gen, model = _llm([50])          # 100 Woerter
    with patch("app.services.llm.generate_text", gen), patch("app.services.llm.resolve_summary_model", model):
        res = await T.summarize_transcript(small, "dokumentation", target_words=300, _is_chunk=True)
    assert res["min_acceptable"] == 85 and res["degraded"] is False


# ── S5-4 (Verlauf) ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verlauf_toleranzband():
    from app.services import verlauf_summary as V
    raw = ("Sitzung mit Inhalt. " * 829).strip()   # 2487w -> min 373
    gen, model = _llm([182])         # 364w (15.09. c93bf8d9) -> >= 298 -> akzeptiert
    with patch("app.services.llm.generate_text", gen), patch("app.services.llm.resolve_summary_model", model):
        res = await V.summarize_verlauf(raw, "verlaengerung")
    assert res["min_acceptable"] == 373
    assert res["summary_word_count"] == 364 and res["degraded"] is True
    assert any(i["type"] == "stage1_short" for i in res["issues"])


@pytest.mark.asyncio
async def test_verlauf_unter_toleranz_stage1error():
    from app.services import verlauf_summary as V
    raw = ("Sitzung mit Inhalt. " * 829).strip()
    gen, model = _llm([100])         # 200w < 298
    with patch("app.services.llm.generate_text", gen), patch("app.services.llm.resolve_summary_model", model):
        with pytest.raises(Stage1Error) as ei:
            await V.summarize_verlauf(raw, "verlaengerung")
    assert ei.value.last_output.startswith("Satz eins.") and ei.value.user_content


# ── S5-6 ────────────────────────────────────────────────────────────────────

def test_log_stage1_failure_schreibt_prompt_und_output():
    from app.services import stage1 as ST
    seen = []
    with patch.object(ST, "_log_prompt", lambda *a: seen.append(("P", a))), \
         patch.object(ST, "_log_output", lambda *a: seen.append(("O", a))):
        ST._log_stage1_failure("j1", "dokumentation", "stage1_transcript", "[FEHLGESCHLAGEN] x",
                               Stage1Error("x", system_prompt="S", user_content="U", last_output="kurz kurz"))
        ST._log_stage1_failure("j2", "dokumentation", "stage1_transcript", "[FEHLGESCHLAGEN] y",
                               RuntimeError("Ollama down"))
    assert seen[0] == ("P", ("j1", "dokumentation", "stage1_transcript (FAILED)", "S", "U"))
    assert seen[1][0] == "O" and "LETZTER MODELL-OUTPUT (2 Woerter)" in seen[1][1][3] and "kurz kurz" in seen[1][1][3]
    # ohne Kontext (fremde Exception): nur der Einzeiler wie bisher
    assert seen[2] == ("O", ("j2", "dokumentation", "stage1_transcript", "[FEHLGESCHLAGEN] y", None))
