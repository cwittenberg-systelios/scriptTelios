"""v19.21 R7: Stage-1-Verdichter auf gemeinsamem Geruest (summary_runner).

Snapshot-Test: Fuer 8 Pfade (Haupt-Call, Chunking, Laengen-/Cap-Retry) der drei
Verdichter wurden VOR dem Umbau alle generate_text-Aufrufe (System-/User-
Prompt, max_tokens, Temperatur, Flags, Modell) und Ergebnis-Kennzahlen
festgehalten (fixtures/stage1_prompts_v1920.json; Prompts als SHA-256). Der Umbau darf daran
NICHTS aendern - das ist der Regressionsnachweis fuer eine prompt-sensible
Komponente. Aendert sich ein Prompt bewusst, Fixture neu erzeugen und die
Aenderung im CHANGELOG ausweisen.
"""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import summary_runner as R

FIX = json.loads((Path(__file__).parent / "fixtures" / "stage1_prompts_v1920.json").read_text(encoding="utf-8"))
RAW = ("Die Patientin berichtet von Schlafstörungen und Konflikten im Team. " * 120).strip()
BIG = ("Sitzung. Die Patientin arbeitet an Grenzen. " * 6000)


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _fake_llm(words_per_call):
    calls = []
    state = {"n": 0}

    async def gen(**kw):
        calls.append(dict(kw))
        state["n"] += 1
        n = words_per_call(state["n"])
        return {"text": ("Satz eins. " * n).strip(), "telemetry": {"tokens_hit_cap": n < 50}}

    async def model():
        return "test-model"
    return calls, gen, model


async def _run(path):
    from app.services import document_summary as D, transcript_summary as T, verlauf_summary as V
    if path.endswith("_retry") or path == "verlauf_cap":
        calls, gen, model = _fake_llm(lambda n: 20 if n == 1 else 900)
    else:
        calls, gen, model = _fake_llm(lambda n: 900)
    with patch("app.services.llm.generate_text", gen), patch("app.services.llm.resolve_summary_model", model):
        try:
            if path == "transcript":          res = await T.summarize_transcript(RAW, "dokumentation", patient_initial="v.M.")
            elif path == "verlauf":           res = await V.summarize_verlauf(RAW, "verlaengerung", patient_initial="v.M.")
            elif path == "document":          res = await D.summarize_document(RAW, target_words=400, doc_label="Selbstauskunft", workflow="anamnese", patient_initial="Frau M.")
            elif path == "transcript_chunked": res = await T.summarize_transcript(BIG, "dokumentation")
            elif path == "verlauf_chunked":   res = await V.summarize_verlauf(BIG, "entlassbericht")
            elif path == "transcript_retry":  res = await T.summarize_transcript(RAW, "anamnese")
            elif path == "document_retry":    res = await D.summarize_document(RAW, target_words=400, doc_label="Vorbefunde")
            elif path == "verlauf_cap":       res = await V.summarize_verlauf(RAW, "verlaengerung")
        except RuntimeError as e:
            res = {"error": str(e)}
    return calls, res


@pytest.mark.parametrize("path", sorted(FIX.keys()))
@pytest.mark.asyncio
async def test_prompts_und_parameter_unveraendert(path):
    calls, res = await _run(path)
    exp = FIX[path]
    assert len(calls) == len(exp["calls"]), "Anzahl LLM-Calls veraendert"
    for i, (got, want) in enumerate(zip(calls, exp["calls"], strict=True)):
        for key in want:
            if key.endswith("_sha256"):
                src = key[: -len("_sha256")]
                assert _sha(got[src]) == want[key], f"{path} Call {i}: {src} weicht ab (Prompt veraendert)"
            elif key.endswith("_len"):
                assert len(got[key[:-4]]) == want[key], f"{path} Call {i}: {key}"
            else:
                assert got.get(key) == want[key], f"{path} Call {i}: {key} weicht ab"
        assert set(got) == {k for k in want if not k.endswith(("_sha256", "_len"))} | {"system_prompt", "user_content"}, \
            f"{path} Call {i}: Parameter-Satz veraendert"
    for key in ("summary_word_count", "target_words", "min_acceptable", "retry_used", "degraded", "error"):
        assert res.get(key) == exp[key], f"{path}: {key}"
    if exp.get("summary_sha256"):
        assert _sha(res["summary"]) == exp["summary_sha256"], f"{path}: Summary-Text veraendert"
    if exp["cap_retry_used"] is not None:
        assert res.get("cap_retry_used") == exp["cap_retry_used"]
    # Ergebnis-Schluessel: Obermenge erlaubt (transcript_chunked liefert seit R7
    # auch system_prompt/user_content fuer prompts.log), nie weniger.
    assert set(exp["result_keys"]) <= set(res.keys())


def test_runner_bausteine():
    assert R.anti_think_suffix("Verdichtung") == (
        "\n\nWICHTIG: KEIN INNERES NACHDENKEN. Schreibe direkt die Verdichtung. "
        "KEINE <think>-Tags, KEINE Meta-Reflexion, KEINE Vorbemerkungen."
    )
    assert R.anti_think_suffix("Zusammenfassung", "Beginne mit X.").endswith("Vorbemerkungen. Beginne mit X.")
    assert R.wrap_no_think("body") == "/no_think\n\nbody\n\n/no_think"
    blk = R.source_block(label="L", tag="T", text="TXT", patient_initial="v.M.", workflow="wf")
    assert blk == "AKTUELLER PATIENT: v.M.\n\nWORKFLOW-KONTEXT: wf\n\nQUELLE — L:\n>>>T<<<\nTXT\n>>>/T<<<\n\n"
    assert R.source_block(label="L", tag="T", text="x").startswith("QUELLE — L:")
    assert R.word_count("  a b  c ") == 3 and R.word_count(None) == 0
    tel = R.merge_chunk_telemetry([{"tokens_hit_cap": True}, {"input_truncated": True}])
    assert tel["chunked"] and tel["chunks"] == 2 and tel["tokens_hit_cap"] and tel["input_truncated"]


@pytest.mark.asyncio
async def test_run_chunked_verteilt_ziel_und_merged_flags():
    seen = []

    async def part(chunk, share):
        seen.append((chunk, share))
        return {"summary": f"S({chunk})", "telemetry": {"tokens_hit_cap": chunk == "b"},
                "issues": [chunk], "retry_used": chunk == "a", "degraded": False,
                "system_prompt": "SYS", "user_content": f"U{chunk}"}

    res = await R.run_chunked(raw_text="a b", chunks=["a", "b"], total_target=1000, summarize_part=part,
                             part_heading="Teil {i}/{n}", header="[{n} Teile]", log_label="T", min_share=10)
    assert [s for _, s in seen] == [500, 500]
    assert res["summary"] == "[2 Teile]\n\n### Teil 1/2\n\nS(a)\n\n### Teil 2/2\n\nS(b)"
    assert res["issues"] == ["a", "b"] and res["retry_used"] and not res["degraded"]
    assert res["telemetry"]["tokens_hit_cap"] and res["telemetry"]["chunks"] == 2
    assert res["user_content"].startswith("[CHUNKED: 2 Teile - hier Teil 1/2]\n\nUa")
    assert res["raw_word_count"] == 2 and res["target_words"] == 1000 and res["min_acceptable"] == 0
