"""
tests/unit/test_v1919_sprint_s.py — v19.19 Sprint S: Stage-1-Robustheit.

S1: max_tokens 2.2x + Cap-Retry   S2: Verlauf-Chunking
S3: Fehlschlag-Logging            S4: Transkript-Chunking
Kein Ollama: generate_text wird gemockt.
"""
from __future__ import annotations

from unittest.mock import patch
import pytest


class TestChunker:
    def test_kurzer_text_unveraendert(self):
        from app.services.staging import chunk_text_by_blocks
        assert chunk_text_by_blocks("kurz", 1000) == ["kurz"]

    def test_verlustfrei_und_an_datumsgrenzen(self):
        from app.services.staging import chunk_text_by_blocks
        t = "".join(f"### {d:02d}.06.2026\nEintrag {d}: " + ("Text. " * 400) + "\n\n" for d in range(1, 31))
        cs = chunk_text_by_blocks(t, 20_000)
        assert "".join(cs) == t
        assert all(len(c) <= 20_000 for c in cs)
        assert len(cs) >= 3
        assert all(c.lstrip().startswith("###") for c in cs)

    def test_transkript_an_sprecherzeilen(self):
        from app.services.staging import chunk_text_by_blocks
        t = "".join(f"[{'AB'[i % 2]}]: Satz {i} " + ("bla " * 60) + "\n" for i in range(300))
        cs = chunk_text_by_blocks(t, 15_000)
        assert "".join(cs) == t
        assert all(c.startswith("[") for c in cs)

    def test_ueberlanger_einzelblock_wird_geteilt(self):
        from app.services.staging import chunk_text_by_blocks
        t = "ohne grenzen " * 5000 + "\n" + "zeile zwei " * 5000
        cs = chunk_text_by_blocks(t, 20_000)
        assert all(len(c) <= 20_000 for c in cs) and "".join(cs) == t


def _fake_result(text, hit_cap=False):
    return {"text": text, "telemetry": {"tokens_hit_cap": hit_cap}, "model_used": "x"}


class TestS1MaxTokensUndCapRetry:
    @pytest.mark.asyncio
    async def test_max_tokens_2_2x_und_retry_bei_cap(self):
        from app.services import verlauf_summary as vs
        calls = []
        async def fake_gen(**kw):
            calls.append(kw["max_tokens"])
            # 1. Call: am Cap; 2. Call: sauber
            hit = len(calls) == 1
            return _fake_result("### Übersicht\n" + ("Verdichtet. " * 900), hit_cap=hit)
        async def fake_model():
            return "m"
        text = "### 01.06.2026\n" + ("Sitzung. " * 4000)  # ~4000 Wörter, < Chunk-Limit
        with patch("app.services.llm.generate_text", fake_gen), \
             patch("app.services.llm.resolve_summary_model", fake_model):
            res = await vs.summarize_verlauf(text, "entlassbericht", "N")
        assert len(calls) == 2
        assert calls[0] == max(2500, int(res["target_words"] * 2.2))
        assert calls[1] == int(calls[0] * 1.5)
        assert res["cap_retry_used"] is True

    @pytest.mark.asyncio
    async def test_kein_retry_ohne_cap(self):
        from app.services import verlauf_summary as vs
        calls = []
        async def fake_gen(**kw):
            calls.append(1)
            return _fake_result("### Übersicht\n" + ("Verdichtet. " * 900))
        async def fake_model():
            return "m"
        text = "### 01.06.2026\n" + ("Sitzung. " * 4000)
        with patch("app.services.llm.generate_text", fake_gen), \
             patch("app.services.llm.resolve_summary_model", fake_model):
            res = await vs.summarize_verlauf(text, "entlassbericht", "N")
        assert len(calls) == 1 and res["cap_retry_used"] is False


class TestS2S4Chunking:
    @pytest.mark.asyncio
    async def test_verlauf_ueber_limit_wird_gechunkt(self, monkeypatch):
        from app.services import verlauf_summary as vs
        monkeypatch.setattr("app.services.staging.stage1_chunk_chars", lambda: 30_000)
        calls = []
        async def fake_gen(**kw):
            calls.append(kw)
            return _fake_result("### Übersicht\n" + ("Teilzusammenfassung. " * 400))
        async def fake_model():
            return "m"
        text = "".join(f"### {d:02d}.06.2026\n" + ("Sitzung. " * 800) + "\n\n" for d in range(1, 16))
        assert len(text) > 90_000
        with patch("app.services.llm.generate_text", fake_gen), \
             patch("app.services.llm.resolve_summary_model", fake_model):
            res = await vs.summarize_verlauf(text, "entlassbericht", "N")
        assert res["telemetry"]["chunked"] is True
        assert res["telemetry"]["chunks"] >= 3
        assert len(calls) == res["telemetry"]["chunks"]
        assert "Teil 1/" in res["summary"] and "chronologisch" in res["summary"]
        # jeder Teil-Call hat nur seinen Teil gesehen
        assert all(len(c["user_content"]) < 45_000 for c in calls)

    @pytest.mark.asyncio
    async def test_transkript_ueber_limit_wird_gechunkt(self, monkeypatch):
        from app.services import transcript_summary as ts
        monkeypatch.setattr("app.services.staging.stage1_chunk_chars", lambda: 30_000)
        calls = []
        async def fake_gen(**kw):
            calls.append(kw)
            return _fake_result("## Auftragsklärung\n" + ("Zusammenfassung. " * 500))
        async def fake_model():
            return "m"
        text = "".join(f"[{'AB'[i % 2]}]: " + ("Rede. " * 120) + "\n" for i in range(160))
        assert len(text) > 90_000
        with patch("app.services.llm.generate_text", fake_gen), \
             patch("app.services.llm.resolve_summary_model", fake_model):
            res = await ts.summarize_transcript(text, "dokumentation", patient_initial="N")
        assert res["telemetry"]["chunked"] is True
        assert res["telemetry"]["chunks"] >= 3
        assert "Gespraechsabschnitt 1/" in res["summary"]
