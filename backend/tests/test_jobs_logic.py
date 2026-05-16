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
# Patient-Name-Fallback (v16 Audit: ehemaliger Bug-Fix #4 entfernt)
# Spec ab v16:
# Wenn patient_name None ist → bleibt None (egal welcher Workflow).
# Der frueher gesetzte Fallback {"initial": "die Klientin/der Klient"} war
# die Ur-Quelle des Output-Bugs - wurde via Replace-Logik in build_system_prompt
# durch alle [Patient/in]-Platzhalter ersetzt und kontaminierte den ganzen Text.
# ─────────────────────────────────────────────────────────────────────────────


def apply_patient_name_fallback(patient_name: dict | None, workflow: str):
    """
    Reproduziert die v16-Logik aus jobs.py:
    Kein Fallback mehr - patient_name kommt durch oder bleibt None.
    """
    return patient_name


class TestPatientNameFallback:
    """v16: Kein Fallback mehr fuer leere patient_name (Audit-Patch)."""

    def test_dokumentation_ohne_name_bleibt_none(self):
        """v16: Anders als frueher (Bug-Fix #4) wird KEIN Fallback gesetzt."""
        result = apply_patient_name_fallback(None, "dokumentation")
        assert result is None

    def test_andere_workflows_bleiben_none(self):
        for workflow in ["entlassbericht", "anamnese", "verlaengerung", "akutantrag", "folgeverlaengerung"]:
            result = apply_patient_name_fallback(None, workflow)
            assert result is None, f"{workflow!r}: kein Fallback"

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

    def test_keine_kontamination_mehr_durch_fallback_string(self):
        """v16-Audit: Der frueher genutzte 'die Klientin/der Klient'-String
        darf nirgendwo mehr als Default eingefuegt werden."""
        for workflow in ["dokumentation", "anamnese", "verlaengerung",
                         "folgeverlaengerung", "akutantrag", "entlassbericht"]:
            result = apply_patient_name_fallback(None, workflow)
            assert result is None or "klient" not in (result.get("initial") or "").lower()


# ─────────────────────────────────────────────────────────────────────────────
# Integration-Spec: v16-Verhalten ohne Fallback
# ─────────────────────────────────────────────────────────────────────────────


class TestSubstitutionMitFallback:
    """
    v16-Spec: Ohne Fallback bleibt patient_name None und keine Platzhalter
    werden gegen Muell ersetzt. Der frueher kontaminierende String
    "die Klientin/der Klient" darf NICHT mehr im finalen Prompt auftauchen.

    (Der Klassenname stammt aus pre-v16-Zeiten und ist absichtlich nicht
    umbenannt, damit Pytest-Selektoren in der CI nicht brechen.)
    """

    def test_kein_fallback_kein_kontaminations_string(self):
        from app.services.prompts import build_system_prompt

        # 1. Kein Name -> kein Fallback (v16)
        patient_name = apply_patient_name_fallback(None, "dokumentation")
        assert patient_name is None

        # 2. Prompt ohne patient_name bauen
        prompt = build_system_prompt(workflow="dokumentation", patient_name=None)

        # 3. Der v15-Kontaminations-String darf nicht im Body auftauchen
        assert "die Klientin/der Klient" not in prompt
