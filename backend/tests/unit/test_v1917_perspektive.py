"""
tests/unit/test_v1917_perspektive.py
────────────────────────────────────
v19.17: Erzählperspektive + Sprachstil P1, QC-Sektions-Synonyme.

Auslöser Nutzerfeedback 2026-08: Wir-Form und pathologisierende Etiketten
("konzentrationsgestört") in Gesprächszusammenfassungen; QC-Fehlalarm
MISSING_SECTION_BEHANDLUNGSVERLAUF bei "Im bisherigen Verlauf des
stationären Aufenthalts ...".
"""
from __future__ import annotations

import pytest


# ── P-1/P-2/P-6: System-Prompt-Aufbau ────────────────────────────────────────

class TestP1SystemPrompt:
    def _sp(self, workflow):
        from app.services.prompts import build_system_prompt
        return build_system_prompt(workflow, workflow_instructions="Test",
                                   source_text="Er berichtete von Sorgen.")

    def test_p1_ohne_wir_wendung_und_floskeln(self):
        sp = self._sp("dokumentation")
        # Die Glossar-Wendung "Wir erlebten [Name] zu Therapiebeginn" ist raus;
        # "Wir erlebten" darf nur noch in den NICHT-Beispielen der Regel stehen.
        assert "zu Therapiebeginn deutlich erschöpft" not in sp
        assert "Alltagstauglichkeit" not in sp
        assert "tragfähige Stabilität" not in sp
        assert "Gesprächsnahe Wendungen" in sp

    def test_p1_enthaelt_perspektiv_und_sprachregel(self):
        sp = self._sp("dokumentation")
        assert "PERSPEKTIVE: Dies ist die Dokumentation EINES Einzelgesprächs" in sp
        assert "Ich-Perspektive" in sp                      # F1: beide Formen erlaubt
        assert "Ein therapeutisches Angebot für die Zwischenzeit" in sp  # F2
        assert "beschreibt sich eingangs des Gesprächs als" in sp        # Beispielpaar
        assert "konzentrationsgestört" in sp                # als NICHT-Beispiel

    def test_p2_unveraendert(self):
        # F4: Anamnese behaelt die Berichts-Wendungen inkl. Wir.
        sp = self._sp("anamnese")
        assert "zu Therapiebeginn deutlich erschöpft" in sp
        assert "PERSPEKTIVE: Dies ist die Dokumentation EINES Einzelgesprächs" not in sp

    def test_berichts_workflows_unveraendert(self):
        for wf in ("verlaengerung", "entlassbericht"):
            sp = self._sp(wf)
            assert "zu Therapiebeginn deutlich erschöpft" in sp

    def test_swap_faellt_sicher_zurueck(self):
        from app.services.prompts import _swap_wendungen_for_doku
        glossar_ohne_marker = "Nur irgendein Text ohne Wendungsblock."
        assert _swap_wendungen_for_doku(glossar_ohne_marker) == glossar_ohne_marker


# ── P-3: QC WIR_FORM_IN_DOKU ─────────────────────────────────────────────────

class TestWirFormCheck:
    def _run(self, text, workflow="dokumentation"):
        from app.services.quality_check import (
            ISSUE_CODE_WIR_FORM_IN_DOKU, run_quality_check,
        )
        base = "Ein hinreichend langer Beispieltext fuer die Laengenpruefung. " * 20
        issues = run_quality_check(base + text, workflow)
        return [i for i in issues if i.code == ISSUE_CODE_WIR_FORM_IN_DOKU]

    def test_wir_erlebten_erkannt(self):
        from app.services.quality_check import SEVERITY_WARNING
        hits = self._run("Wir erlebten Frau G. im heutigen Gespräch als unruhig.")
        assert len(hits) == 1 and hits[0].severity == SEVERITY_WARNING

    def test_unsere_arbeit_erkannt(self):
        assert len(self._run("Im Rahmen unserer Arbeit zeigte sich Entlastung.")) == 1

    def test_klientenrede_kein_treffer(self):
        # Wiedergegebene Rede: 'wir' ohne Team-Verb darf nicht anschlagen.
        assert self._run("Er berichtete, dass er und seine Frau sagten: wir schaffen das.") == []
        assert self._run("Gemeinsames Ziel des Gesprächs war es, Ruhe zu finden.") == []

    def test_nur_p1(self):
        assert self._run("Wir erlebten Frau G. erschöpft.", workflow="entlassbericht") == []


# ── F6: QC PATHOLOGISIERENDE_SPRACHE ─────────────────────────────────────────

class TestPathoSpracheCheck:
    def _run(self, text, workflow="dokumentation"):
        from app.services.quality_check import (
            ISSUE_CODE_PATHOLOGISIERENDE_SPRACHE, run_quality_check,
        )
        base = "Ein hinreichend langer Beispieltext fuer die Laengenpruefung. " * 20
        issues = run_quality_check(base + text, workflow)
        return [i for i in issues if i.code == ISSUE_CODE_PATHOLOGISIERENDE_SPRACHE]

    def test_gestoert_kompositum_erkannt(self):
        hits = self._run("Sie wirkte konzentrationsgestört und angespannt.")
        assert len(hits) == 1
        assert "konzentrationsgestört" in hits[0].code_detail["matches"]

    def test_defizitaer_und_auffaellig_erkannt(self):
        assert len(self._run("Das Verhalten war auffällig und defizitär.")) == 1

    def test_stoerung_substantiv_kein_treffer(self):
        # Legitime Symptom-/Diagnosebenennung.
        assert self._run("Sie berichtet von einer Schlafstörung seit Wochen.") == []

    def test_ungestoert_unauffaellig_kein_treffer(self):
        assert self._run("Sie konnte ungestört arbeiten, der Kontakt blieb unauffällig.") == []

    def test_nur_p1(self):
        assert self._run("Der Affekt war auffällig verflacht.", workflow="anamnese") == []


# ── P-7: Sektions-Synonyme ───────────────────────────────────────────────────

class TestBehandlungsverlaufSynonyme:
    def test_fehlalarm_satz_matcht_jetzt(self):
        from app.services.quality_specs import section_present
        text = ("Im bisherigen Verlauf des stationären Aufenthalts zeigte sich "
                "eine schrittweise Stabilisierung.")
        assert section_present(text, "behandlungsverlauf")

    def test_weitere_varianten(self):
        from app.services.quality_specs import section_present
        for t in ("Der Therapieverlauf war von Fortschritten geprägt.",
                  "Im therapeutischen Prozess wurde deutlich, dass ...",
                  "Der Aufenthaltsverlauf gestaltete sich stabil."):
            assert section_present(t, "behandlungsverlauf"), t

    def test_negativfall_bleibt_negativ(self):
        from app.services.quality_specs import section_present
        assert not section_present(
            "Frau M. wurde am 01.02. aufgenommen. Diagnosen: F33.1.",
            "behandlungsverlauf",
        )
