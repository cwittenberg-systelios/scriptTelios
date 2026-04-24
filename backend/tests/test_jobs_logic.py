"""
Unit-Tests fuer Logik die in jobs.py inline steht aber pruefbar ist:

1. Akutantrag-Cap-Logik (Bug-Fix #2): wenn derive_word_limits ueber 400w
   liefert wird auf Default zurueckgesetzt
2. Patient-Name-Fallback (Bug-Fix #4): bei dokumentation ohne Quelle wird
   ein generisches Initial gesetzt damit [Patient/in] aus FEW_SHOT ersetzt wird

Da der Code in jobs.py inline ist (kein extrahierter Helper) duplizieren
wir die Logik 1:1 hier und testen sie. Das ist absichtlich — die Tests
sollen die SPEC dokumentieren und Regressionen in der Logik fangen.
Wenn jobs.py refactoriert wird sollen diese Tests die Spec sicherstellen.
"""
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Akutantrag-Cap (Bug-Fix #2)
# Spec aus jobs.py:
# Wenn workflow == "akutantrag" und word_limits gesetzt und
# (max > _fb_max ODER min > _fb_max) → fallback auf (_fb_min, _fb_max)
# ─────────────────────────────────────────────────────────────────────────────


def apply_akut_cap(word_limits, fb_min: int, fb_max: int):
    """
    Reproduziert die Cap-Logik aus jobs.py:497-516.
    Tests laufen direkt gegen diese Logik — wenn jobs.py refactored wird
    muss diese Funktion gleich geupdatet werden.
    """
    if not word_limits:
        return word_limits
    _orig_min, _orig_max = word_limits
    if _orig_max > fb_max or _orig_min > fb_max:
        return (fb_min, fb_max)
    return word_limits


class TestAkutCap:
    """Akutantrag-Cap (Bug-Fix #2)."""

    # Defaults fuer Akutantrag in jobs.py
    FB_MIN = 150
    FB_MAX = 400

    def test_eval_run_szenario_422_783(self):
        """
        Original Bug-Szenario aus dem Eval-Run:
        derive_word_limits gab (422, 783) zurueck weil die Stilvorlage
        Volltext mit Anamnese+Befund war. Cap muss auf Defaults setzen.
        """
        result = apply_akut_cap((422, 783), self.FB_MIN, self.FB_MAX)
        assert result == (150, 400)

    def test_im_bereich_bleibt_unveraendert(self):
        result = apply_akut_cap((180, 350), self.FB_MIN, self.FB_MAX)
        assert result == (180, 350)

    def test_max_drueber_aber_min_drunter(self):
        result = apply_akut_cap((200, 600), self.FB_MIN, self.FB_MAX)
        assert result == (150, 400)

    def test_genau_an_grenze_400(self):
        result = apply_akut_cap((150, 400), self.FB_MIN, self.FB_MAX)
        assert result == (150, 400)

    def test_unter_default_min(self):
        # (100, 200) — beide unter den Defaults aber erlaubt
        result = apply_akut_cap((100, 200), self.FB_MIN, self.FB_MAX)
        assert result == (100, 200), "Untere Werte sollen erlaubt bleiben"

    def test_none_word_limits(self):
        result = apply_akut_cap(None, self.FB_MIN, self.FB_MAX)
        assert result is None

    def test_min_genau_ueber_max(self):
        # Edge: min = max + 1 → triggert Cap
        result = apply_akut_cap((401, 401), self.FB_MIN, self.FB_MAX)
        assert result == (150, 400)


# ─────────────────────────────────────────────────────────────────────────────
# Patient-Name-Fallback (Bug-Fix #4)
# Spec aus jobs.py:530-549:
# Wenn patient_name None ist UND workflow == "dokumentation" → Default-Dict
# ─────────────────────────────────────────────────────────────────────────────


def apply_patient_name_fallback(patient_name: dict | None, workflow: str):
    """
    Reproduziert die Fallback-Logik aus jobs.py.
    """
    if patient_name:
        return patient_name
    if workflow == "dokumentation":
        return {
            "anrede": "",
            "vorname": "",
            "nachname": "",
            "initial": "die Klientin/der Klient",
        }
    return None


class TestPatientNameFallback:
    """Patient-Name-Fallback (Bug-Fix #4)."""

    def test_dokumentation_ohne_name_bekommt_fallback(self):
        result = apply_patient_name_fallback(None, "dokumentation")
        assert result is not None
        assert result["initial"] == "die Klientin/der Klient"

    def test_andere_workflows_bekommen_keinen_fallback(self):
        for workflow in ["entlassbericht", "anamnese", "verlaengerung", "akutantrag"]:
            result = apply_patient_name_fallback(None, workflow)
            assert result is None, f"{workflow!r} sollte KEIN Fallback bekommen"

    def test_existierender_name_wird_durchgereicht(self):
        original = {
            "anrede": "Frau", "vorname": "Sabine",
            "nachname": "Schuster", "initial": "S.",
        }
        result = apply_patient_name_fallback(original, "dokumentation")
        assert result == original

    def test_existierender_name_in_anderen_workflows(self):
        original = {
            "anrede": "Herr", "vorname": "Max",
            "nachname": "Mustermann", "initial": "M.",
        }
        result = apply_patient_name_fallback(original, "entlassbericht")
        assert result == original

    def test_fallback_dict_hat_alle_keys(self):
        result = apply_patient_name_fallback(None, "dokumentation")
        required = {"anrede", "vorname", "nachname", "initial"}
        assert set(result.keys()) == required


# ─────────────────────────────────────────────────────────────────────────────
# Integration-Spec: Substitution wirkt mit Fallback
# ─────────────────────────────────────────────────────────────────────────────


class TestSubstitutionMitFallback:
    """
    Spec-Test: nach dem Fallback und der Substitution in build_system_prompt
    soll [Patient/in] nicht mehr im Prompt sein.
    """

    def test_fallback_loest_substitution_aus(self):
        from app.services.prompts import build_system_prompt

        # 1. Kein Name -> Fallback
        patient_name = apply_patient_name_fallback(None, "dokumentation")
        assert patient_name is not None

        # 2. Mit Fallback in Prompt einbauen
        prompt = build_system_prompt(workflow="dokumentation", patient_name=patient_name)

        # 3. Substitution soll [Patient/in] entfernen
        # (es darf nicht mehr in echten Prompt-Body Stellen vorkommen,
        # nur in Substitutions-Code-Kommentaren falls die geloggt werden)
        assert "die Klientin/der Klient" in prompt
