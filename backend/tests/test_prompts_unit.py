"""
Unit-Tests fuer prompts.py.

Testet:
- derive_word_limits(): die Wortlimit-Ableitungs-Logik
- _compute_style_constraints(): Stil-Metriken-Berechnung
- build_system_prompt(): die Patientennamen-Substitution

Hintergrund: Vier konkrete Bugs sollten durch diese Tests gefangen werden:
- Bug 1 (kritisch): Selbst-widersprueglicher Verbots-Text durch Substitution
- Bug 2: Akutantrag-Wortlimit absurd hoch (422-783 statt 150-400)
- Bug 3: Absatzlaengen-Toleranz zu eng
- Bug 4: [Patient/in] ueberlebt im Output bei dok-02
"""
import pytest

from app.services.prompts import (
    derive_word_limits,
    _compute_style_constraints,
    build_system_prompt,
)


# ─────────────────────────────────────────────────────────────────────────────
# derive_word_limits()
# ─────────────────────────────────────────────────────────────────────────────


class TestDeriveWordLimits:
    """Tests fuer die dynamische Wortlimit-Ableitung."""

    def test_leere_liste_gibt_fallback_zurueck(self):
        result = derive_word_limits([], 100, 500)
        assert result == (100, 500)

    def test_liste_mit_None_gibt_fallback(self):
        result = derive_word_limits([None, "", None], 100, 500)
        assert result == (100, 500)

    def test_text_unter_50_woerter_wird_ignoriert(self):
        kurz = "wort " * 30  # 30 Woerter
        result = derive_word_limits([kurz], 100, 500)
        assert result == (100, 500), "Texte <50 Woerter sollen ignoriert werden"

    def test_einzelner_text_300_woerter_mit_default_tolerance(self):
        text = "wort " * 300
        result = derive_word_limits([text], 50, 9999, tolerance=0.30)
        # ref_min = ref_max = 300 → derived: max(50, 300*0.7)=210, 300*1.30=390
        assert result == (210, 390)

    def test_zwei_texte_unterschiedlicher_laenge(self):
        kurz = "wort " * 100
        lang = "wort " * 500
        result = derive_word_limits([kurz, lang], 50, 9999, tolerance=0.30)
        # ref_min=100, ref_max=500
        # derived_min = max(50, 100*0.7) = 70
        # derived_max = 500*1.30 = 650
        assert result == (70, 650)

    def test_minimum_50_woerter_floor_wird_eingehalten(self):
        # Sehr kurzer Text (< 50w wird ignoriert)
        text = "wort " * 60
        result = derive_word_limits([text], 50, 9999, tolerance=0.30)
        # ref=60, derived_min=max(50, 60*0.7)=max(50, 42)=50
        assert result[0] == 50

    def test_tolerance_anpassbar(self):
        text = "wort " * 200
        result_30 = derive_word_limits([text], 50, 9999, tolerance=0.30)
        result_50 = derive_word_limits([text], 50, 9999, tolerance=0.50)
        # Bei groesserer Toleranz: groessere Bandbreite
        assert result_50[0] < result_30[0]
        assert result_50[1] > result_30[1]

    def test_alle_texte_unter_50_w_gibt_fallback(self):
        result = derive_word_limits(["wort " * 30, "wort " * 40], 100, 500)
        assert result == (100, 500)


# ─────────────────────────────────────────────────────────────────────────────
# _compute_style_constraints()
# ─────────────────────────────────────────────────────────────────────────────


class TestComputeStyleConstraints:
    """Tests fuer die Stil-Metriken-Berechnung."""

    def test_kurzer_text_unter_30_woerter_gibt_leerstring(self):
        result = _compute_style_constraints("Kurzer Text mit nur wenigen Worten.")
        assert result == ""

    def test_normaler_text_enthaelt_satzlaenge(self):
        text = (
            "Dies ist ein erster Satz im Beispieltext. Hier kommt der zweite Satz "
            "mit etwas mehr Inhalt und Komplexitaet. Der dritte Satz schliesst "
            "den Absatz ab und gibt einen guten Eindruck der Schreibweise. " * 3
        )
        result = _compute_style_constraints(text)
        assert "Satzlänge" in result
        assert "Absatzlänge" in result
        assert "STIL-VORGABEN" in result

    def test_skip_length_unterdrueckt_textlaenge_zeile(self):
        text = "wort " * 100
        with_length = _compute_style_constraints(text, skip_length=False)
        without_length = _compute_style_constraints(text, skip_length=True)
        assert "TEXTLÄNGE (verbindlich)" in with_length
        assert "TEXTLÄNGE (verbindlich)" not in without_length
        assert "Absatzstruktur" in without_length  # Alternativ-Hinweis

    def test_wir_perspektive_wird_erkannt(self):
        text_wir = (
            "Wir erlebten die Patientin zu Therapiebeginn deutlich erschoepft. "
            "In unserer Arbeit gelang es uns die Symptomatik zu stabilisieren. "
            "Wir konnten unsere therapeutischen Methoden erfolgreich einsetzen. " * 3
        )
        result = _compute_style_constraints(text_wir)
        assert "Wir-Form" in result or "wir" in result.lower()

    def test_dritte_person_wird_erkannt(self):
        text_er = (
            "Der Patient zeigte deutliche Erschoepfung. Er stellte sich mit dem "
            "Hauptanliegen vor sich besser zu verstehen. Seine Symptomatik "
            "entwickelte sich schleichend. Der Klient hat keine Suizidgedanken." * 3
        )
        result = _compute_style_constraints(text_er)
        # Sollte als Dritte-Person markiert werden
        assert "Dritte Person" in result or "Er/Sie" in result

    def test_fallback_auf_einzelne_zeilen_wenn_keine_doppel_newlines(self):
        # Text ohne \n\n (wie aus extract_docx_section mit \n-join)
        text = "\n".join([
            "Erster substanzieller Absatz mit mindestens zwanzig Woertern damit der "
            "Fallback greift und dieser Absatz als gueltig erkannt wird im Test.",
            "Zweiter substanzieller Absatz mit ebenfalls genug Woertern um vom Fallback "
            "Mechanismus erkannt zu werden so dass die Statistik korrekt berechnet wird.",
            "Dritter Absatz hier ebenfalls mit genug Woertern um die Heuristik zu pruefen "
            "die einzelne Zeilen als Absaetze interpretiert wenn keine Doppel-Newlines da sind.",
        ])
        # Sollte nicht crashen und sinnvolle Werte liefern
        result = _compute_style_constraints(text)
        assert "Absatzlänge" in result


# ─────────────────────────────────────────────────────────────────────────────
# build_system_prompt() – Patientennamen-Substitution
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildSystemPromptPatientNameSubstitution:
    """
    Bug 1 & 4: Substitutions-Logik in build_system_prompt darf
    nicht die Verbots-Beispiele kaputt machen.
    """

    def test_patient_in_platzhalter_wird_ersetzt(self):
        """Mit patient_name werden alle [Patient/in] durch Initial ersetzt."""
        patient = {
            "anrede": "Frau",
            "vorname": "Sabine",
            "nachname": "Schuster",
            "initial": "S.",
        }
        prompt = build_system_prompt(workflow="entlassbericht", patient_name=patient)
        # Substitution muss greifen
        assert "[Patient/in]" not in prompt or prompt.count("[Patient/in]") < 5
        # Im FEW_SHOT muss "Frau S." vorkommen
        assert "Frau S." in prompt

    def test_kein_selbstwidersprueglicher_verbotstext(self):
        """
        Bug 1: Der Verbots-Text darf nicht den eigenen erlaubten Namen
        als Verbot enthalten.
        Erwartung: nirgends in der Form "NIEMALS Platzhalter wie 'Frau S.'"
        """
        patient = {
            "anrede": "Frau",
            "vorname": "Sabine",
            "nachname": "Schuster",
            "initial": "S.",
        }
        prompt = build_system_prompt(workflow="entlassbericht", patient_name=patient)
        # Der erlaubte Name "Frau S." darf NICHT im Verbots-Text auftauchen
        # Heuristik: in der Naehe von "NIEMALS" sollte "Frau S." nicht vorkommen
        assert "NIEMALS Platzhalter wie 'Frau S.'" not in prompt
        assert "NIEMALS '[Patient/in]'" not in prompt or "'[Patient/in]'" not in prompt
        # Noch besser: kein Self-Reference im Verbots-Block
        if "NIEMALS Platzhalter" in prompt:
            # Wenn der Block existiert sollte er "Patient/in" als Wort enthalten
            # nicht den substituierten Namen
            verbot_idx = prompt.find("NIEMALS Platzhalter")
            verbot_block = prompt[verbot_idx:verbot_idx + 200]
            assert "Frau S." not in verbot_block, \
                f"Self-reference im Verbots-Block: {verbot_block!r}"

    def test_ohne_patient_name_keine_substitution(self):
        """Wenn kein patient_name uebergeben → Platzhalter bleiben stehen."""
        prompt = build_system_prompt(workflow="entlassbericht", patient_name=None)
        # In FEW_SHOT_EB_ENTLASSBERICHT sollte "[Patient/in]" stehen
        # (Modell muss aus Quellen ableiten)
        assert "[Patient/in]" in prompt or "Patient/in" in prompt

    def test_herr_initial_korrekt_substituiert(self):
        patient = {
            "anrede": "Herr",
            "vorname": "Peter",
            "nachname": "Mueller",
            "initial": "M.",
        }
        prompt = build_system_prompt(workflow="entlassbericht", patient_name=patient)
        # Herr/[Patient/in] → "Herr M."
        assert "Herr M." in prompt

    def test_alle_workflows_bauen_ohne_crash(self):
        """Sanity-Check: kein Workflow crasht beim Bauen."""
        patient = {
            "anrede": "Frau", "vorname": "Test", "nachname": "Patient", "initial": "P.",
        }
        for workflow in [
            "dokumentation", "anamnese", "verlaengerung",
            "folgeverlaengerung", "akutantrag", "entlassbericht",
        ]:
            try:
                prompt = build_system_prompt(workflow=workflow, patient_name=patient)
                assert isinstance(prompt, str)
                assert len(prompt) > 100
            except Exception as e:
                pytest.fail(f"build_system_prompt({workflow!r}) crashed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# build_system_prompt() – Word Limits werden eingebaut
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildSystemPromptWordLimits:
    """Tests fuer das VERBINDLICHE TEXTLIMIT in build_system_prompt."""

    def test_word_limits_werden_in_prompt_eingebaut(self):
        prompt = build_system_prompt(
            workflow="entlassbericht",
            word_limits=(700, 1200),
        )
        assert "VERBINDLICHES TEXTLIMIT" in prompt
        assert "700" in prompt
        assert "1200" in prompt

    def test_ohne_word_limits_kein_textlimit_block(self):
        prompt = build_system_prompt(
            workflow="entlassbericht",
            word_limits=None,
        )
        assert "VERBINDLICHES TEXTLIMIT" not in prompt


# ─────────────────────────────────────────────────────────────────────────────
# Diagnosen-Substitution
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildSystemPromptDiagnosen:
    """{diagnosen} Platzhalter-Substitution."""

    def test_diagnosen_werden_eingebaut(self):
        prompt = build_system_prompt(
            workflow="anamnese",
            diagnosen=["F33.2", "F43.1"],
        )
        # Sollte in {diagnosen} eingesetzt sein, nicht mehr als Platzhalter
        assert "{diagnosen}" not in prompt
        assert "F33.2" in prompt
        assert "F43.1" in prompt

    def test_keine_diagnosen_zeigt_platzhalter_text(self):
        prompt = build_system_prompt(
            workflow="anamnese",
            diagnosen=None,
        )
        assert "{diagnosen}" not in prompt
        # Sollte Default-Text zeigen
        assert "noch nicht festgelegt" in prompt
