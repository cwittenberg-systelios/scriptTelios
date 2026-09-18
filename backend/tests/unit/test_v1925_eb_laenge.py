"""v19.25 Sprint L: Entlassbericht-Laenge.

Basis: 10 EB (11.-15.09.2026) mit 464-649 Woertern bei Ziel 500-900
(Richtwert 700). L1: QC LENGTH_BELOW_TARGET (< 550w, warning, kein
Auto-Repair); L2: explizite Mindestlaenge 600 im Laengenanker.
"""
from app.services import quality_check as QC
from app.services.prompts import (
    EB_LENGTH_QC_THRESHOLD,
    EB_MIN_WORDS_EXPLICIT,
    build_system_prompt,
    render_length_anchor_block,
    resolve_length_anchor,
)


class TestL2:

    def test_eb_anker_mit_mindestlaenge(self):
        a = resolve_length_anchor("entlassbericht")
        blk = a["anchor_block"]
        assert f"Mindestlänge: {EB_MIN_WORDS_EXPLICIT} Wörter" in blk
        assert "gilt als unvollständig" in blk
        assert "ZIELLÄNGE: 600–900" in blk and "Richtwert ca. 700" in blk
        assert "ausschweifend" not in blk
        assert "Einzeltherapie, Gruppentherapie und nonverbale" in blk

    def test_style_fenster_deckelt_floor(self):
        # Style-abgeleitetes Fenster 400-650: Floor = min(600, 550) = 550
        blk = render_length_anchor_block("entlassbericht", 400, 650)
        assert "ZIELLÄNGE: 550–650" in blk and "Mindestlänge: 550" in blk

    def test_andere_workflows_unveraendert(self):
        blk = render_length_anchor_block("verlaengerung", 350, 650)
        assert "Mindestlänge" not in blk and "ausschweifend" in blk

    def test_im_systemprompt(self):
        sp = build_system_prompt("entlassbericht", workflow_instructions=None, source_text="Gruppe.")
        assert "Mindestlänge: 600 Wörter" in sp


class TestL1:

    def _issues(self, n, wf="entlassbericht"):
        return [i for i in QC.run_quality_check("Wort " * n, wf) if i.code == QC.ISSUE_CODE_LENGTH_BELOW_TARGET]

    def test_log_faelle(self):
        assert EB_LENGTH_QC_THRESHOLD == 550
        for n in (464, 510, 522, 536, 543, 549):   # d0951034, 07806908, 26ee45d3, 32f0eda8, 4e9e9747
            hit = self._issues(n)
            assert len(hit) == 1 and hit[0].severity == QC.SEVERITY_WARNING, n
            assert hit[0].code_detail["actual"] == n
            assert "quellen" in hit[0].repair_hint.lower()
        for n in (550, 574, 649, 800):
            assert self._issues(n) == [], n

    def test_nur_entlassbericht(self):
        assert self._issues(300, "verlaengerung") == []
        assert self._issues(300, "dokumentation") == []

    def test_stub_check_bleibt_getrennt(self):
        issues = QC.run_quality_check("Wort " * 200, "entlassbericht")
        codes = [i.code for i in issues]
        assert QC.ISSUE_CODE_LENGTH_TOO_SHORT in codes and QC.ISSUE_CODE_LENGTH_BELOW_TARGET in codes
