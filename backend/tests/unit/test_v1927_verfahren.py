"""v19.27: Verfahrensregister (services/verfahren.py) und seine Integration in
System-Prompt (dreistufige Quellentreue-Feststellung, Phasenblock nur P1,
D11: nicht fuer anamnese/befund), User-Content-Sandwich, Stage-1-Prompt und
Telemetrie/Repair-Pfad (job_queue/jobs)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import verfahren as V
from app.services.prompts import (
    KLINISCHES_GLOSSAR_NEUTRAL,
    build_system_prompt,
    build_user_content,
    source_mentions_parts_work,
)

NEUTRAL = ("Der Patient berichtet ueber Erschoepfung im Arbeitskontext, Schlafprobleme "
           "und Konflikte mit dem Vorgesetzten.")
IRRT_SRC = NEUTRAL + "\nIRRT Traumasitzung zur Operation nach der Geburt."
IFS_SRC = NEUTRAL + "\nWir arbeiteten mit dem inneren Anteil, der sich als Schutzschild zeigt."
_IFS_TOKENS = ["Manager", "Antreiber", "Verbannte", "Türsteher", "Feuerbekämpfer"]


# ── Register ─────────────────────────────────────────────────────────────────

class TestRegister:
    def test_keys_eindeutig_und_stems_lowercase(self):
        keys = [v.key for v in V.VERFAHREN_REGISTER]
        assert len(keys) == len(set(keys))
        for v in V.VERFAHREN_REGISTER:
            assert all(s == s.lower() for s in v.stems), v.key
            assert len(v.phasen) == len(v.phasen_marker), v.key

    def test_erkennung(self):
        assert V.verfahren_keys(IRRT_SRC) == ["irrt"]
        assert V.verfahren_keys(NEUTRAL) == []
        assert V.verfahren_keys(None) == []
        assert V.verfahren_keys("Es kam EMDR und Stuhlarbeit vor") == ["emdr", "ego_state"]

    def test_parts_work_flag(self):
        assert V.mentions_parts_work(IFS_SRC) is True
        assert V.mentions_parts_work(IRRT_SRC) is False

    def test_alte_stems_bleiben_fuer_anamnese(self):
        # Anamnese/Befund nutzen weiter prompts.source_mentions_parts_work (D11)
        assert source_mentions_parts_work("Es kam EMDR vor") is True
        assert V.register_applies("anamnese") is False
        assert V.register_applies("befund") is False
        assert V.register_applies("dokumentation") is True

    def test_irrt_phasen_nach_schmucker_koester(self):
        irrt = V.VERFAHREN_BY_KEY["irrt"]
        joined = " ".join(irrt.phasen)
        for m in ("Hot Spot", "Heutige Ich", "Damaligen Ich", "Abschlussbild", "Nachbesprechung"):
            assert m in joined
        assert "gelungen" not in irrt.doku_hinweise or "keine Bewertung" in irrt.doku_hinweise
        assert irrt.varianten

    def test_render_bausteine(self):
        irrt = [V.VERFAHREN_BY_KEY["irrt"]]
        assert "IRRT" in V.render_verfahren_feststellung(irrt)
        assert "Beobachtungsraster" in V.render_verfahren_phasenblock(irrt)
        assert "ANGEWENDETES VERFAHREN" in V.render_verfahren_stage1_hinweis(irrt)
        assert V.render_verfahren_feststellung([]) == ""
        assert V.render_verfahren_phasenblock([]) == ""
        assert V.render_verfahren_stage1_hinweis([]) == ""

    def test_fehlende_phasen(self):
        irrt = V.VERFAHREN_BY_KEY["irrt"]
        assert V.fehlende_phasen("", irrt) == [1, 2, 3, 4, 5]
        text = "Die Situation wurde geklaert. Am Hot Spot trat das Heutige Ich hinzu, wandte sich dem Damaligen Ich zu; Abschlussbild. Vereinbart wurde Ruhe."
        assert V.fehlende_phasen(text, irrt) == []


# ── System-Prompt ────────────────────────────────────────────────────────────

class TestSystemPrompt:
    def test_ohne_verfahren_unveraendert(self):
        prompt = build_system_prompt(workflow="dokumentation", source_text=NEUTRAL)
        assert "enthalten KEINE Teilearbeit" in prompt
        assert "VERFAHRENSSTRUKTUR" not in prompt

    def test_irrt_whitelist_statt_verbot(self):
        prompt = build_system_prompt(workflow="dokumentation", source_text=IRRT_SRC)
        assert "enthalten KEINE Teilearbeit" not in prompt
        assert "Benenne daher KEIN Therapieverfahren" not in prompt
        assert "wird folgendes Verfahren wörtlich genannt: IRRT" in prompt
        assert "VERFAHRENSSTRUKTUR IRRT" in prompt
        for tok in _IFS_TOKENS:
            assert tok not in prompt, tok   # kein Anteile-Priming

    def test_ifs_volles_glossar_plus_whitelist(self):
        prompt = build_system_prompt(workflow="dokumentation", source_text=IFS_SRC)
        assert "Manager" in prompt
        assert "IFS / Anteilearbeit" in prompt
        assert "VERFAHRENSSTRUKTUR IFS" in prompt

    def test_antraege_whitelist_ohne_phasenblock(self):
        prompt = build_system_prompt(workflow="verlaengerung", source_text=IRRT_SRC)
        assert "wörtlich genannt: IRRT" in prompt
        assert "VERFAHRENSSTRUKTUR" not in prompt

    def test_anamnese_unveraendert(self):
        prompt = build_system_prompt(workflow="anamnese", source_text=IRRT_SRC)
        assert "enthalten KEINE Teilearbeit" in prompt
        assert "VERFAHRENSSTRUKTUR" not in prompt
        assert "wörtlich genannt" not in prompt

    def test_legacy_ohne_source(self):
        prompt = build_system_prompt(workflow="dokumentation")
        assert "Manager" in prompt and "VERFAHRENSSTRUKTUR" not in prompt

    def test_feststellungs_regex_trifft_neutrales_glossar(self):
        from app.services.prompts import _NEUTRAL_FESTSTELLUNG_RE
        assert len(_NEUTRAL_FESTSTELLUNG_RE.findall(KLINISCHES_GLOSSAR_NEUTRAL)) == 1


class TestUserContent:
    def test_sandwich_nennt_verfahren(self):
        uc = build_user_content(workflow="dokumentation", transcript="T", fokus_themen="IRRT",
                                verfahren_labels=["IRRT (…)"])
        assert "IRRT (…)" in uc and "namentlich benannt" in uc

    def test_ohne_labels_unveraendert(self):
        a = build_user_content(workflow="dokumentation", transcript="T", fokus_themen="X")
        b = build_user_content(workflow="dokumentation", transcript="T", fokus_themen="X", verfahren_labels=None)
        assert a == b and "namentlich benannt" not in a


# ── Stage-1 ──────────────────────────────────────────────────────────────────

RAW = ("Die Patientin berichtet von Schlafstoerungen und Konflikten im Team. " * 500).strip()


def _llm(words):
    calls = []

    async def gen(**kw):
        calls.append(kw)
        return {"text": ("Satz eins. " * words).strip(), "telemetry": {"tokens_hit_cap": False}}

    async def model():
        return "test-model"
    return calls, gen, model


@pytest.mark.asyncio
async def test_stage1_prompt_ohne_fokus_identisch_mit_fokus_erweitert():
    from app.services import transcript_summary as T
    calls_a, gen, model = _llm(450)
    with patch("app.services.llm.generate_text", gen), patch("app.services.llm.resolve_summary_model", model):
        await T.summarize_transcript(RAW, "dokumentation")
    calls_b, gen, model = _llm(450)
    with patch("app.services.llm.generate_text", gen), patch("app.services.llm.resolve_summary_model", model):
        await T.summarize_transcript(RAW, "dokumentation", fokus_themen="IRRT Traumasitzung",
                                     verfahren=[V.VERFAHREN_BY_KEY["irrt"]])
    sys_a, sys_b = calls_a[0]["system_prompt"], calls_b[0]["system_prompt"]
    assert "SCHWERPUNKTE DES THERAPEUTEN" not in sys_a
    assert "SCHWERPUNKTE DES THERAPEUTEN" in sys_b and "IRRT Traumasitzung" in sys_b
    assert "ANGEWENDETES VERFAHREN" in sys_b
    assert calls_a[0]["user_content"] == calls_b[0]["user_content"]


@pytest.mark.asyncio
async def test_run_transcript_stage1_reicht_fokus_durch(monkeypatch):
    from app.services import stage1 as S1
    from unittest.mock import MagicMock
    seen = {}

    async def fake_summarize(**kw):
        seen.update(kw)
        return {"summary": "S", "raw_word_count": 3000, "summary_word_count": 600,
                "compression_ratio": 0.2, "duration_s": 1.0, "telemetry": {},
                "retry_used": False, "retry_telemetry": {}, "degraded": False,
                "issues": [], "target_words": 600, "min_acceptable": 300}
    monkeypatch.setattr(S1, "summarize_transcript", fake_summarize)
    monkeypatch.setattr(S1, "_log_output", lambda *a, **k: None)
    job = MagicMock(); job.job_id = "j"
    text = "wort " * 3000
    out, summ, audit = await S1._run_transcript_stage1(
        workflow="dokumentation", transkript_text=text, patient_initial="L.", job=job, bands={},
        fokus_themen="IRRT Traumasitzung", verfahren=[V.VERFAHREN_BY_KEY["irrt"]],
    )
    assert seen["fokus_themen"] == "IRRT Traumasitzung" and seen["verfahren"][0].key == "irrt"
    assert audit["applied"] is True and out == "S"


# ── Telemetrie / QC-Eingaben ─────────────────────────────────────────────────

def test_job_fokus_themen_fallback_telemetrie():
    from app.services.job_queue import _job_fokus_themen, _qc_fidelity_source
    from unittest.mock import MagicMock
    job = MagicMock(spec=[])
    job.generation_telemetry = {"fokus_themen": "IRRT Traumasitzung"}
    assert _job_fokus_themen(job) == "IRRT Traumasitzung"
    job.fokus_themen = "Direkt"
    assert _job_fokus_themen(job) == "Direkt"
    job.result_transcript = "Transkript."
    assert "Direkt" in _qc_fidelity_source(job)
