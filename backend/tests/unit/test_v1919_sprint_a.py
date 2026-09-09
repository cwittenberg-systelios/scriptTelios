"""
tests/unit/test_v1919_sprint_a.py — v19.19 Sprint A1/A2: Anamnese in indirekter Rede.
Feedback e.krause (07.08., Rating 1) + c.saur (20.08.): "im Konjunktiv schreiben".
"""
from __future__ import annotations


class TestA1Prompt:
    def test_pflichtkern_enthaelt_sprachform(self):
        from app.services.prompts import build_system_prompt
        sp = build_system_prompt("anamnese", workflow_instructions="T",
                                 source_text="Sie berichtete von Sorgen.")
        assert "SPRACHFORM (verbindlich)" in sp
        assert "KONJUNKTIV I" in sp
        assert "sie fühle sich seit Monaten erschöpft" in sp   # RICHTIG-Beispiel
        assert "Sie fühlt sich seit Monaten erschöpft" in sp   # FALSCH-Beispiel

    def test_befund_und_p1_ohne_regel(self):
        from app.services.prompts import build_system_prompt
        for wf in ("dokumentation", "entlassbericht"):
            sp = build_system_prompt(wf, workflow_instructions="T",
                                     source_text="Er berichtete.")
            assert "SPRACHFORM (verbindlich)" not in sp


class TestA2KonjunktivCheck:
    def _run(self, text, workflow="anamnese"):
        from app.services.quality_check import ISSUE_CODE_KONJUNKTIV_QUOTE, run_quality_check
        issues = run_quality_check(text, workflow)
        return [i for i in issues if i.code == ISSUE_CODE_KONJUNKTIV_QUOTE]

    INDIKATIV = ("Frau M. stellt sich vor. Sie fühlt sich erschöpft und hat wenig Kontakt. "
                 "Sie schläft schlecht, arbeitet viel und lebt allein. Sie ist oft traurig "
                 "und kann sich kaum konzentrieren. Sie will das ändern. " * 4)
    KONJUNKTIV = ("Frau M. berichtet, sie fühle sich erschöpft und habe wenig Kontakt. "
                  "Sie schlafe schlecht, arbeite viel und lebe allein. Sie sei oft traurig "
                  "und könne sich kaum konzentrieren. Sie wolle das ändern. " * 4)

    def test_indikativ_anamnese_erkannt(self):
        from app.services.quality_check import SEVERITY_WARNING
        hits = self._run(self.INDIKATIV)
        assert len(hits) == 1 and hits[0].severity == SEVERITY_WARNING
        assert hits[0].code_detail["konjunktiv"] == 0

    def test_konjunktiv_anamnese_ok(self):
        assert self._run(self.KONJUNKTIV) == []

    def test_rahmenverben_zaehlen_nicht_als_indikativ(self):
        # 'berichtet/schildert' tragen die indirekte Rede - kein Fehlalarm.
        text = ("Sie berichtet, sie sei müde. Sie schildert, sie habe Angst. "
                "Er gibt an, er könne nicht schlafen. Sie beschreibt, sie leide. " * 3)
        assert self._run(text) == []

    def test_nur_anamnese_teil_vor_befund(self):
        text = self.KONJUNKTIV + "\n\n###BEFUND###\n\n" + self.INDIKATIV
        assert self._run(text) == []

    def test_nicht_fuer_andere_workflows(self):
        assert self._run(self.INDIKATIV, workflow="entlassbericht") == []

    def test_zu_wenig_formen_keine_aussage(self):
        assert self._run("Frau M. ist da. Sie hat Zeit.") == []
