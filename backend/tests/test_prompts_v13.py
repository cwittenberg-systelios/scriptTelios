"""
Tests fuer die v12 -> v13 Patches in prompts.py und extraction.py.

Schwerpunkte:
1. parse_explicit_patient_name lehnt generische Strings ab
   ("die Klientin/der Klient", "Patient/in", "Frau X." etc.)
2. STRUCTURAL_WORKFLOWS-Konstante deckt beide Code-Pfade ab
3. PATIENTENNAME-Block wird bei muellbehafteten Werten unterdrueckt
4. AKTUELLER PATIENT-Zeile im User-Content wird bei muellbehafteten Werten unterdrueckt
5. Replace-Logik fuer Platzhalter laeuft nur mit plausiblen Werten
"""
import pytest


# ── Bug 1a: parse_explicit_patient_name Blacklist ────────────────────────────

class TestParseExplicitPatientNameBlacklist:
    """Generische Bezeichnungen duerfen nicht als Eigenname akzeptiert werden."""

    @pytest.mark.parametrize("input_str", [
        "die Klientin/der Klient",
        "die Klientin / der Klient",
        "die Klientin",
        "der Klient",
        "Klientin",
        "Klient",
        "Klient.",
        "Patient",
        "Patientin",
        "Patient/in",
        "die Patientin/der Patient",
        "Frau X.",
        "Herr Y.",
        "Frau X",
        "NN",
        "n.n.",
        "Anonym",
        "Unbekannt",
    ])
    def test_generische_strings_werden_abgelehnt(self, input_str):
        from app.services.extraction import parse_explicit_patient_name
        result = parse_explicit_patient_name(input_str)
        assert result is None, f"Generischer String {input_str!r} darf nicht als Name akzeptiert werden"

    @pytest.mark.parametrize("input_str,expected_initial", [
        ("Frau M.",            "M."),
        ("Herr S.",            "S."),
        ("Andreas Reif",       "R."),
        ("Maria Schmidt",      "S."),
        ("Frau Müller",        "M."),
        ("Herr Müller-Lüdenscheidt", "M."),  # Doppelname
    ])
    def test_echte_namen_werden_akzeptiert(self, input_str, expected_initial):
        from app.services.extraction import parse_explicit_patient_name
        result = parse_explicit_patient_name(input_str)
        assert result is not None
        assert result["initial"] == expected_initial

    def test_leerer_string_None(self):
        from app.services.extraction import parse_explicit_patient_name
        assert parse_explicit_patient_name("") is None
        assert parse_explicit_patient_name("   ") is None
        assert parse_explicit_patient_name(None) is None

    def test_zu_langer_nachname_None(self):
        from app.services.extraction import parse_explicit_patient_name
        # >30 Zeichen Nachname = Hinweistext, kein Eigenname
        long_str = "X" * 35
        assert parse_explicit_patient_name(long_str) is None


# ── Bug 2: STRUCTURAL_WORKFLOWS-Konstante ────────────────────────────────────

class TestStructuralWorkflowsConstant:
    """STRUCTURAL_WORKFLOWS muss konsistent in allen Code-Pfaden verwendet werden."""

    def test_konstante_existiert_und_enthaelt_korrekte_workflows(self):
        from app.services.prompts import STRUCTURAL_WORKFLOWS
        assert "anamnese" in STRUCTURAL_WORKFLOWS
        assert "verlaengerung" in STRUCTURAL_WORKFLOWS
        assert "folgeverlaengerung" in STRUCTURAL_WORKFLOWS
        assert "akutantrag" in STRUCTURAL_WORKFLOWS
        assert "entlassbericht" in STRUCTURAL_WORKFLOWS
        # P1 (dokumentation) ist NICHT strukturell
        assert "dokumentation" not in STRUCTURAL_WORKFLOWS

    def test_alle_strukturellen_workflows_haben_base_prompt(self):
        from app.services.prompts import STRUCTURAL_WORKFLOWS, BASE_PROMPTS
        for wf in STRUCTURAL_WORKFLOWS:
            # akutantrag hat einen separaten BASE_PROMPT_AKUTANTRAG, ist aber
            # nicht zwingend in BASE_PROMPTS - das ist OK.
            assert wf in BASE_PROMPTS or wf == "akutantrag"


# ── Bug 1b: PATIENTENNAME-Block im System-Prompt ─────────────────────────────

class TestPatientennameBlockSystemPrompt:
    """Der PATIENTENNAME-Block darf nicht ausgegeben werden, wenn Daten Müll sind."""

    def test_block_ausgegeben_bei_plausiblem_namen(self):
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="dokumentation",
            patient_name={"anrede": "Frau", "vorname": "Maria",
                          "nachname": "Schmidt", "initial": "S."},
        )
        assert "PATIENTENNAME" in p
        assert "Frau Maria Schmidt" in p
        assert "Frau S." in p

    def test_block_unterdrueckt_bei_klient_im_nachnamen(self):
        from app.services.prompts import build_system_prompt
        # Falls trotz Filter doch ein Hinweistext ankommt: PATIENTENNAME-Block
        # darf NICHT mit dem Müll gerendert werden.
        p = build_system_prompt(
            workflow="dokumentation",
            patient_name={"anrede": "", "vorname": "die Klientin/der",
                          "nachname": "Klient", "initial": "K."},
        )
        # Das gesamte Konstrukt "Der aktuelle Patient ist ... die Klientin/der Klient"
        # darf nicht im Prompt stehen
        assert "die Klientin/der Klient" not in p

    def test_block_unterdrueckt_bei_leerem_nachnamen(self):
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="dokumentation",
            patient_name={"anrede": "", "vorname": "", "nachname": "", "initial": "X."},
        )
        # Geisterzeile "Der aktuelle Patient ist   ." darf nicht entstehen
        assert "Der aktuelle Patient ist   ." not in p
        assert "Der aktuelle Patient ist  ." not in p

    def test_block_unterdrueckt_bei_extrem_langem_nachnamen(self):
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="dokumentation",
            patient_name={"anrede": "", "vorname": "",
                          "nachname": "X" * 100, "initial": "X."},
        )
        assert "X" * 100 not in p


# ── Bug 1c: Replace-Logik mit Sanity-Check ───────────────────────────────────

class TestReplaceLogikSanityCheck:
    """Die [Patient/in]-Replace-Logik darf nicht mit Müll-Werten laufen."""

    def test_kein_klient_im_replace(self):
        from app.services.prompts import build_system_prompt
        # Wenn full_ref "Klientin" enthielte, wuerde es ueberall im Glossar
        # "[Name]" durch "Klientin" ersetzt - das darf NICHT passieren.
        p = build_system_prompt(
            workflow="dokumentation",
            patient_name={"anrede": "", "vorname": "",
                          "nachname": "Klient", "initial": "Klient"},
        )
        # Glossar enthielt "[Name]" - dies darf NICHT durch "Klient" ersetzt sein
        # (weil das Modell dann "Mithilfe des Therapiekonzepts gelang es Klient ..." schreibt)
        # Indirekter Test: kein Vorkommen des Müll-Strings im fertigen Prompt
        assert "gelang es Klient die intrapsychischen" not in p

    def test_replace_funktioniert_mit_plausibler_initiale(self):
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="dokumentation",
            patient_name={"anrede": "Frau", "vorname": "Maria",
                          "nachname": "Schmidt", "initial": "S."},
        )
        # [Patient/in] im Few-Shot soll durch "Frau S." ersetzt sein
        assert "[Patient/in]" not in p or "Frau S." in p


# ── Bug 1d: AKTUELLER PATIENT im User-Content P1 ─────────────────────────────

class TestAktuellerPatientUserContent:
    """Im build_user_content darf kein Müll als Namenskuerzel ausgegeben werden."""

    def test_namenskuerzel_unterdrueckt_bei_klient(self):
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="dokumentation",
            transcript="Test-Transkript.",
            patient_name={"anrede": "", "vorname": "",
                          "nachname": "die Klientin/der Klient",
                          "initial": "die Klientin/der Klient"},
        )
        assert "Namenskuerzel 'die Klientin/der Klient'" not in u

    def test_namenskuerzel_unterdrueckt_bei_zu_langer_initiale(self):
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="dokumentation",
            transcript="Test.",
            patient_name={"anrede": "", "vorname": "",
                          "nachname": "Lang", "initial": "Lang.lang.lang"},
        )
        assert "Lang.lang.lang" not in u

    def test_aktuelle_patient_block_bei_plausiblem_namen(self):
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="dokumentation",
            transcript="Test.",
            patient_name={"anrede": "Frau", "vorname": "", "nachname": "M.", "initial": "M."},
        )
        assert "AKTUELLER PATIENT: Frau M." in u
