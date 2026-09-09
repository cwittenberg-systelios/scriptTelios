"""
tests/unit/test_v1920_sprint_m.py — v19.20 Sprint M: Modalitaeten im Entlassbericht.
Analyse 09.09.: Gruppentherapie fehlte in 10/19 EB trotz Quellenbeleg.
"""
from __future__ import annotations


class TestM1M2Prompt:
    def _sp(self):
        from app.services.prompts import build_system_prompt
        return build_system_prompt("entlassbericht", workflow_instructions=None,
                                   source_text="Er berichtete in der Gruppe.")

    def test_altes_verbot_weg(self):
        sp = self._sp()
        assert "kein Block über Einzelgespräche, Gruppentherapie" not in sp

    def test_pflichtinhalt_und_modalitaetenregel(self):
        sp = self._sp()
        assert "PFLICHTINHALT" in sp
        assert "MODALITÄTEN (verbindlich)" in sp
        assert "Vollständigkeit aller dokumentierten Modalitäten hat Vorrang vor Kürze" in sp

    def test_workflow_anweisung_alle_drei(self):
        from app.services.prompts import WORKFLOW_INSTRUCTIONS_DEFAULT
        wi = WORKFLOW_INSTRUCTIONS_DEFAULT["entlassbericht"]
        assert "Der Einzeltherapie, der Gruppentherapie und den nonverbalen Therapien" in wi


class TestM4Laengenanker:
    def test_eb_ohne_kuerze_priming(self):
        from app.services.prompts import resolve_length_anchor
        a = resolve_length_anchor("entlassbericht")
        assert "ausschweifend" not in a["anchor_block"]
        assert "Vollständigkeit" in a["anchor_block"]
        assert "900" in a["anchor_block"]

    def test_andere_workflows_unveraendert(self):
        from app.services.prompts import resolve_length_anchor
        a = resolve_length_anchor("verlaengerung")
        assert "ausschweifend" in a["anchor_block"]


class TestM3QuellenbewussteSeverity:
    def _issues(self, text, source):
        from app.services.quality_check import run_quality_check
        return {i.code: i for i in run_quality_check(text, "entlassbericht", source_text=source)}

    TEXT = ("Im Einzelprozess bearbeitete Herr M. seine Selbstabwertung. "
            "In der Kunsttherapie entstand ein Bild der Erschöpfung. " * 30)

    def test_dokumentierte_modalitaet_fehlt_warning(self):
        from app.services.quality_check import SEVERITY_WARNING
        source = "Gruppentherapie: Bindungsthema. In der Gruppe erlebte er Zugehoerigkeit. Gruppensetting stabil." * 2
        iss = self._issues(self.TEXT, source)
        i = iss["MODALITY_NOT_COVERED_GRUPPENTHERAPIE"]
        assert i.severity == SEVERITY_WARNING
        assert i.code_detail["source_hits"] >= 3
        assert "QUELLE-VERLAUF" in i.repair_hint

    def test_nicht_dokumentierte_modalitaet_bleibt_info(self):
        from app.services.quality_check import SEVERITY_INFO
        iss = self._issues(self.TEXT, "Nur Einzelgespraeche dokumentiert.")
        assert iss["MODALITY_NOT_COVERED_GRUPPENTHERAPIE"].severity == SEVERITY_INFO

    def test_ohne_quelle_wie_bisher(self):
        from app.services.quality_check import SEVERITY_INFO
        iss = self._issues(self.TEXT, "")
        assert iss["MODALITY_NOT_COVERED_GRUPPENTHERAPIE"].severity == SEVERITY_INFO


class TestM5Retention:
    def test_kennzahl(self):
        from app.services.verlauf_summary import _modality_retention
        r = _modality_retention("Gruppe Gruppe Einzelgespräch Kunsttherapie", "Gruppe")
        assert r["gruppe"] == {"raw": 2, "summary": 1}
        assert r["einzel"]["raw"] == 1 and r["nonverbal"]["raw"] == 1

    def test_stage1_fokus_eb(self):
        from app.services import verlauf_summary as vs
        import inspect
        assert "MODALITÄTEN ERHALTEN" in inspect.getsource(vs)


class TestM4Produktionspfad:
    def test_word_limits_pfad_eb_ohne_priming(self):
        # jobs.py uebergibt word_limits explizit - genau dieser Pfad hatte den
        # Anker-Text ein zweites Mal hardcodiert (vom bestehenden Laengen-Test
        # aufgedeckt). Beide Pfade muessen identisch rendern.
        from app.services.prompts import build_system_prompt
        sp = build_system_prompt(workflow="entlassbericht", word_limits=(500, 900))
        assert "ausschweifend" not in sp and "Vollständigkeit" in sp
        sp2 = build_system_prompt(workflow="verlaengerung", word_limits=(300, 600))
        assert "ausschweifend" in sp2
