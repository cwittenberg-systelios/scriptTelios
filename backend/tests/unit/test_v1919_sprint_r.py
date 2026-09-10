"""
tests/unit/test_v1919_sprint_r.py — v19.19 Sprint R (Repair v2) + A3 (Diagnosekriterien).
"""
from __future__ import annotations

from unittest.mock import patch
import pytest


# ── R1: No-op-Erkennung ──────────────────────────────────────────────────────

class TestNoopDetection:
    def test_identisch_ist_noop(self):
        from app.api.jobs import _is_repair_noop
        t = "Herr M. berichtete von Sorgen. " * 60
        noop, sim = _is_repair_noop(t, t)
        assert noop and sim == 1.0

    def test_whitespace_unterschiede_sind_noop(self):
        from app.api.jobs import _is_repair_noop
        t = "Herr M. berichtete von Sorgen. " * 60
        noop, _ = _is_repair_noop(t, t.replace(". ", ".\n\n"))
        assert noop

    def test_kleine_echte_ergaenzung_kein_noop(self):
        # Log-Fall a954a3a1: sim 0.98, +3 % Woerter -> echte Aenderung
        from app.api.jobs import _is_repair_noop
        t = "Herr M. berichtete von Sorgen und Belastungen im Alltag. " * 50
        new = t + "Im Paargespräch am 12.06. wurden die Rollen in der Beziehung reflektiert und neu verhandelt. "
        noop, sim = _is_repair_noop(t, new)
        assert not noop and sim > 0.95

    def test_wants_addition(self):
        from app.api.jobs import _repair_wants_addition
        assert _repair_wants_addition("Bitte das Paargespräch mitaufnehmen", [])
        assert _repair_wants_addition("", ["MISSING_SECTION_BEHANDLUNGSVERLAUF"])
        assert not _repair_wants_addition("bitte noch etwas kürzer", [])
        assert not _repair_wants_addition("systemischer formulieren", ["WIR_FORM_IN_DOKU"])


class _Job:
    job_id = "abcdef1234567890"
    def set_progress(self, *a, **k): pass


class TestRepairCoroutine:
    @pytest.mark.asyncio
    async def test_noop_loest_retry_aus_und_uebernimmt_geaenderten_text(self):
        from app.services import repair as J
        orig = "Herr M. berichtete von Sorgen und Belastungen im Alltag. " * 40
        changed = orig + "Im Paargespräch wurden die Rollen reflektiert. " * 3
        calls = []
        async def fake_gen(system, user, **kw):
            calls.append(user)
            return {"text": orig if len(calls) == 1 else changed, "telemetry": {}, "model_used": "m"}
        with patch.object(J, "generate_text", fake_gen), \
             patch.object(J, "_log_prompt", lambda *a, **k: None), \
             patch.object(J, "_log_output", lambda *a, **k: None):
            res = await J._run_repair_coroutine(
                _Job(), "entlassbericht", "PROMPT", original_text=orig,
                user_hint="Bitte das Paargespräch mitaufnehmen",
            )
        assert len(calls) == 2
        assert ">>>ZWEITER VERSUCH<<<" in calls[1]
        flags = res["generation_telemetry"]["repair_flags"]
        assert flags["attempts"] == 2 and not flags.get("no_change")
        assert res["text"].startswith(orig.strip()[:40])
        assert "Paargespräch" in res["text"]

    @pytest.mark.asyncio
    async def test_doppel_noop_gibt_original_mit_flag(self):
        from app.services import repair as J
        orig = "Herr M. berichtete von Sorgen. " * 40
        async def fake_gen(system, user, **kw):
            return {"text": orig, "telemetry": {}, "model_used": "m"}
        with patch.object(J, "generate_text", fake_gen), \
             patch.object(J, "_log_prompt", lambda *a, **k: None), \
             patch.object(J, "_log_output", lambda *a, **k: None):
            res = await J._run_repair_coroutine(
                _Job(), "entlassbericht", "PROMPT", original_text=orig,
                user_hint="bitte systemischer formulieren",
            )
        flags = res["generation_telemetry"]["repair_flags"]
        assert flags["no_change"] is True and flags["attempts"] == 2
        assert res["text"].strip() == orig.strip()

    @pytest.mark.asyncio
    async def test_kein_retry_ohne_anweisungen(self):
        from app.services import repair as J
        orig = "Herr M. berichtete von Sorgen. " * 40
        n = []
        async def fake_gen(system, user, **kw):
            n.append(1); return {"text": orig, "telemetry": {}, "model_used": "m"}
        with patch.object(J, "generate_text", fake_gen), \
             patch.object(J, "_log_prompt", lambda *a, **k: None), \
             patch.object(J, "_log_output", lambda *a, **k: None):
            await J._run_repair_coroutine(_Job(), "entlassbericht", "P", original_text=orig)
        assert len(n) == 1

    @pytest.mark.asyncio
    async def test_schrumpfung_bei_ergaenzung_geflaggt(self):
        from app.services import repair as J
        orig = "Herr M. berichtete ausführlich von Sorgen und Belastungen. " * 60
        short = "Herr M. berichtete von Sorgen. " * 25
        async def fake_gen(system, user, **kw):
            return {"text": short, "telemetry": {}, "model_used": "m"}
        with patch.object(J, "generate_text", fake_gen), \
             patch.object(J, "_log_prompt", lambda *a, **k: None), \
             patch.object(J, "_log_output", lambda *a, **k: None):
            res = await J._run_repair_coroutine(
                _Job(), "entlassbericht", "P", original_text=orig,
                user_hint="Bitte mehr aus dem Körpereinzel ergänzen",
            )
        flags = res["generation_telemetry"]["repair_flags"]
        assert flags["shrunk"] is True and flags["new_words"] < flags["orig_words"]


# ── R1/R2 QC-Issues ──────────────────────────────────────────────────────────

class TestRepairFlagIssues:
    def _run(self, flags):
        from app.services.quality_check import run_quality_check
        return {i.code: i for i in run_quality_check(
            "Ein hinreichend langer Beispieltext. " * 30, "entlassbericht",
            repair_flags=flags)}

    def test_no_change_critical(self):
        from app.services.quality_check import ISSUE_CODE_REPAIR_NO_CHANGE, SEVERITY_CRITICAL
        iss = self._run({"no_change": True, "similarity": 1.0, "attempts": 2})
        assert iss[ISSUE_CODE_REPAIR_NO_CHANGE].severity == SEVERITY_CRITICAL
        assert "konkreter" in iss[ISSUE_CODE_REPAIR_NO_CHANGE].repair_hint

    def test_shrunk_critical(self):
        from app.services.quality_check import ISSUE_CODE_REPAIR_SHRUNK
        iss = self._run({"shrunk": True, "orig_words": 581, "new_words": 379})
        assert "581 -> 379" in iss[ISSUE_CODE_REPAIR_SHRUNK].message

    def test_ohne_flags_nichts(self):
        from app.services.quality_check import ISSUE_CODE_REPAIR_NO_CHANGE
        assert ISSUE_CODE_REPAIR_NO_CHANGE not in self._run(None)
        assert ISSUE_CODE_REPAIR_NO_CHANGE not in self._run({"attempts": 1, "similarity": 0.6})


# ── A3b: Diagnosekriterien ───────────────────────────────────────────────────

class TestDiagnosekriterien:
    def _run(self, text, dx, workflow="anamnese"):
        from app.services.quality_check import run_quality_check
        return [i for i in run_quality_check(text, workflow, diagnosen=dx)
                if i.code.startswith("DIAGNOSE")]

    def test_familien_erkennung(self):
        from app.services.quality_check import dx_families_for
        assert dx_families_for(["F33.1 rezidivierende depressive Störung"]) == ["F32/F33"]
        assert dx_families_for(["F41.0", "F43.1"]) == ["F41", "F43"]
        assert dx_families_for(["Z73.0 Burnout"]) == []

    def test_geringe_abdeckung_warnt(self):
        text = "Frau M. berichtet, sie habe Stress im Beruf und wolle das ändern. " * 20
        hits = self._run(text, ["F33.1"])
        codes = [h.code for h in hits]
        assert "DIAGNOSEKRITERIEN_COVERAGE_DEPRESSIV" in codes
        h = [x for x in hits if x.code.endswith("DEPRESSIV")][0]
        assert "Schlaf" in h.code_detail["missing"]

    def test_gute_abdeckung_ok(self):
        text = ("Frau M. berichtet, ihre Stimmung sei seit Monaten niedergeschlagen, sie "
                "fühle sich antriebslos und erschöpft, habe die Freude an allem verloren, "
                "schlafe schlecht, der Appetit sei weg, sie könne sich kaum konzentrieren, "
                "fühle sich wertlos und habe sich sozial zurückgezogen. Suizidgedanken "
                "verneine sie. " * 6)
        assert self._run(text, ["F33.1"]) == []

    def test_diagnose_im_text_warnt(self):
        from app.services.quality_check import ISSUE_CODE_DIAGNOSE_IM_TEXT
        text = ("Frau M. leide, wie die Diagnose F33.1 zeigt, an einer rezidivierenden "
                "depressiven Störung. Ihre Stimmung sei niedergeschlagen, der Antrieb gering, "
                "der Schlaf gestört, der Appetit reduziert, die Konzentration schlecht, sie "
                "fühle sich wertlos und ziehe sich zurück. " * 5)
        codes = [h.code for h in self._run(text, ["F33.1"])]
        assert ISSUE_CODE_DIAGNOSE_IM_TEXT in codes

    def test_nur_anamnese_und_nur_mit_diagnosen(self):
        assert self._run("Stress im Beruf. " * 40, ["F33.1"], workflow="dokumentation") == []
        assert self._run("Stress im Beruf. " * 40, None) == []


# ── R5: Feedback-Fallkopie ───────────────────────────────────────────────────

class TestFeedbackCase:
    def test_bloecke_werden_gesammelt_und_geschrieben(self, tmp_path, monkeypatch):
        from app.api import feedback as F
        monkeypatch.setenv("LOG_FILE", str(tmp_path / "systelios.log"))
        hdr = "=" * 80
        (tmp_path / "prompts.log").write_text(
            f"2026-09-09 10:00:00\n{hdr}\nJOB: aaaa1111  |  WORKFLOW: x  |  CALL: x\n{hdr}\nPROMPT A\n\n"
            f"2026-09-09 10:01:00\n{hdr}\nJOB: bbbb2222  |  WORKFLOW: x  |  CALL: x\n{hdr}\nPROMPT B\n\n"
            f"2026-09-09 10:02:00\n{hdr}\nJOB: aaaa1111  |  WORKFLOW: x  |  CALL: x  (OUTPUT)\n{hdr}\nOUT A\n",
            encoding="utf-8")
        (tmp_path / "prompts.log.2026-09-08").write_text(
            f"2026-09-08 09:00:00\n{hdr}\nJOB: aaaa1111  |  WORKFLOW: x  |  CALL: stage1_verlauf\n{hdr}\nSTAGE1 A\n",
            encoding="utf-8")
        blocks = F._collect_prompt_log_blocks("aaaa1111")
        assert "PROMPT A" in blocks and "OUT A" in blocks and "STAGE1 A" in blocks
        assert "PROMPT B" not in blocks
        path = F._write_feedback_case("aaaa1111", {"rating": 2, "user": "u", "context": "", "text": "zu kurz"})
        assert path and path.endswith("_aaaa1111.log")
        content = open(path, encoding="utf-8").read()
        assert content.startswith("# FEEDBACK-FALL aaaa1111") and "zu kurz" in content

    def test_unbekannter_job_keine_datei(self, tmp_path, monkeypatch):
        from app.api import feedback as F
        monkeypatch.setenv("LOG_FILE", str(tmp_path / "systelios.log"))
        (tmp_path / "prompts.log").write_text("nichts", encoding="utf-8")
        assert F._write_feedback_case("zzzz", {}) is None
