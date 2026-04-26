"""
Tests fuer die v14 -> v15 Patches.

Schwerpunkte:
- Cleanup 1: NAMENSREGEL-Konsolidierung (genau einmal pro Prompt)
- Opt 4: Transcript/Verlaufsdoku/etc. werden dedupliziert
- /api/testrun-Endpoint
"""
import pytest
from unittest.mock import patch, MagicMock


# ── Cleanup 1: NAMENSREGEL nur EINMAL pro User-Content ────────────────────────

class TestNamensregelKonsolidierung:
    """Die Datenschutz-Namensregel darf nicht mehr 5x pro User-Content erscheinen."""

    def test_namensregel_genau_einmal_in_user_content(self):
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="entlassbericht",
            verlaufsdoku_text="Test-Verlauf.",
            antragsvorlage_text="Test-Antrag.",
            patient_name={"anrede": "Frau", "vorname": "Maria",
                          "nachname": "Schmidt", "initial": "S."},
        )
        # Die Header-Zeile darf genau einmal vorkommen
        count = u.count("DATENSCHUTZ – NAMENSFORMAT")
        assert count == 1, (
            f"NAMENSREGEL muesste 1x vorkommen, ist aber {count}x in:\n{u[:500]}"
        )

    @pytest.mark.parametrize("wf", [
        "dokumentation", "anamnese", "verlaengerung",
        "folgeverlaengerung", "akutantrag", "entlassbericht",
    ])
    def test_namensregel_in_jedem_workflow_genau_einmal(self, wf):
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow=wf,
            transcript="Test.",
            patient_name={"anrede": "Frau", "vorname": "M.",
                          "nachname": "Schmidt", "initial": "S."},
        )
        count = u.count("DATENSCHUTZ – NAMENSFORMAT")
        assert count == 1, f"Workflow {wf}: NAMENSREGEL {count}x statt 1x"

    def test_namensregel_konstante_existiert(self):
        from app.services.prompts import NAMENSREGEL
        assert "DATENSCHUTZ" in NAMENSREGEL
        assert "Initialen" in NAMENSREGEL or "ersten Buchstaben" in NAMENSREGEL


# ── Opt 4: Transcript-Deduplikation ───────────────────────────────────────────

class TestTranscriptDeduplikation:
    """Quelltexte werden vor dem Bau des User-Contents dedupliziert."""

    def test_transcript_doppelter_absatz_entfernt(self):
        from app.services.prompts import build_user_content
        # Whisper-Hallucination: derselbe Satz mehrfach
        transcript = (
            "Der Patient berichtet von Schlafstoerungen.\n\n"
            "Der Patient berichtet von Schlafstoerungen.\n\n"
            "Er hat seit Monaten Albtraeume.\n\n"
            "Der Patient berichtet von Schlafstoerungen."
        )
        u = build_user_content(workflow="dokumentation", transcript=transcript)
        # Der Satz darf nur einmal vorkommen
        count = u.count("Der Patient berichtet von Schlafstoerungen.")
        assert count == 1, f"Duplikat nicht entfernt: {count}x in:\n{u}"
        # Andere Inhalte bleiben erhalten
        assert "Er hat seit Monaten Albtraeume." in u

    def test_verlaufsdoku_dedupliziert(self):
        from app.services.prompts import build_user_content
        verlauf = (
            "Sitzung 1: Aufnahmegespraech.\n\n"
            "Sitzung 1: Aufnahmegespraech.\n\n"  # PDF-Header-Duplikat
            "Sitzung 2: Vertiefung."
        )
        u = build_user_content(
            workflow="entlassbericht",
            verlaufsdoku_text=verlauf,
        )
        assert u.count("Sitzung 1: Aufnahmegespraech.") == 1

    def test_dedup_bricht_bei_fehler_nicht_alles(self):
        """Wenn deduplicate_paragraphs einen Fehler wirft, faellt der
        User-Content trotzdem zusammen (Original-Text wird verwendet)."""
        from app.services.prompts import build_user_content

        with patch("app.services.llm.deduplicate_paragraphs",
                   side_effect=RuntimeError("simuliert")):
            u = build_user_content(
                workflow="dokumentation",
                transcript="Original-Transcript ohne Duplikate.",
            )
            assert "Original-Transcript ohne Duplikate." in u

    def test_dedup_bei_leeren_quelltexten_kein_crash(self):
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="dokumentation",
            transcript="",
            selbstauskunft_text=None,
            verlaufsdoku_text="   ",
        )
        # Prompt wurde gebaut ohne TypeError oder AttributeError
        assert isinstance(u, str)


# ── /api/testrun ──────────────────────────────────────────────────────────────

class TestTestRunEndpoint:
    """Test fuer den neuen /api/testrun-Endpunkt."""

    def test_endpoint_default_schliesst_test_eval_aus(self):
        """Default-Run muss --ignore=tests/test_eval.py enthalten."""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        with patch("app.api.testrun._run_pytest_blocking") as mock_run:
            mock_run.return_value = {"exitCode": 0, "output": "ok"}
            r = client.post("/api/testrun", json={})

        assert r.status_code == 200
        cmd = mock_run.call_args[0][0]
        assert "--ignore=tests/test_eval.py" in cmd, (
            f"Default-Run muesste test_eval ausschliessen, cmd war: {cmd}"
        )

    def test_endpoint_include_eval_inkludiert_test_eval(self):
        """Mit include_eval=True wird test_eval mitgelaufen."""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        with patch("app.api.testrun._run_pytest_blocking") as mock_run:
            mock_run.return_value = {"exitCode": 0, "output": "ok"}
            r = client.post("/api/testrun", json={"include_eval": True})

        assert r.status_code == 200
        cmd = mock_run.call_args[0][0]
        assert "--ignore=tests/test_eval.py" not in cmd, (
            f"include_eval=True muesste test_eval inkludieren, cmd war: {cmd}"
        )

    def test_endpoint_explizite_paths_uebersteuern_default(self):
        """Wenn paths explizit angegeben: --ignore wird nicht hinzugefuegt
        (der Aufrufer entscheidet was er testen will)."""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        with patch("app.api.testrun._run_pytest_blocking") as mock_run:
            mock_run.return_value = {"exitCode": 0, "output": "ok"}
            r = client.post("/api/testrun", json={"paths": ["tests/test_eval.py"]})

        assert r.status_code == 200
        cmd = mock_run.call_args[0][0]
        assert "--ignore=tests/test_eval.py" not in cmd
        assert "tests/test_eval.py" in cmd

    def test_endpoint_existiert_und_response_struktur(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        with patch("app.api.testrun._run_pytest_blocking") as mock_run:
            mock_run.return_value = {
                "exitCode": 0,
                "output": "============================= 5 passed in 1.0s =================================",
            }
            r = client.post("/api/testrun", json={})

        assert r.status_code == 200
        data = r.json()
        assert "exitCode" in data
        assert "output" in data
        assert isinstance(data["exitCode"], int)
        assert isinstance(data["output"], str)

    def test_endpoint_mit_selector(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        with patch("app.api.testrun._run_pytest_blocking") as mock_run:
            mock_run.return_value = {"exitCode": 0, "output": "1 passed"}
            r = client.post("/api/testrun", json={"selector": "test_prompts_v15"})

        assert r.status_code == 200
        # pytest -k test_prompts_v15 muss im Kommando sein
        cmd = mock_run.call_args[0][0]
        assert "-k" in cmd
        assert "test_prompts_v15" in cmd

    def test_endpoint_lehnt_path_traversal_ab(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        r = client.post("/api/testrun", json={"paths": ["../../../etc/passwd"]})
        assert r.status_code == 400

    def test_endpoint_lehnt_absolute_pfade_ab(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        r = client.post("/api/testrun", json={"paths": ["/etc/passwd"]})
        assert r.status_code == 400

    def test_endpoint_lehnt_shell_metazeichen_im_selector_ab(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        for bad in ["test; rm -rf /", "test`whoami`", "test$(id)", "test|cat"]:
            r = client.post("/api/testrun", json={"selector": bad})
            assert r.status_code == 400, f"Selector {bad!r} muesste abgelehnt werden"

    def test_endpoint_get_variante(self):
        """GET /api/testrun ohne Body fuer Browser-Aufruf."""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        with patch("app.api.testrun._run_pytest_blocking") as mock_run:
            mock_run.return_value = {"exitCode": 0, "output": "ok"}
            r = client.get("/api/testrun")

        assert r.status_code == 200

    def test_timeout_wird_als_exitcode_minus1_gemeldet(self):
        """Timeout-Behandlung."""
        import subprocess
        from app.api.testrun import _run_pytest_blocking

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 600)):
            result = _run_pytest_blocking(["pytest"], 600)

        assert result["exitCode"] == -1
        assert "TIMEOUT" in result["output"]

    def test_output_truncation(self):
        """Riesiger Output wird abgeschnitten."""
        from app.api.testrun import _truncate_output, _MAX_OUTPUT_BYTES
        big = "x" * (_MAX_OUTPUT_BYTES + 1000)
        result = _truncate_output(big)
        assert "GEKUERZT" in result
        assert len(result.encode("utf-8")) < _MAX_OUTPUT_BYTES + 200


# ── Smoke-Tests v15 ───────────────────────────────────────────────────────────

class TestSmokeAllWorkflows:
    """Alle Workflows muessen weiter valide System-Prompts und User-Contents bauen."""

    @pytest.mark.parametrize("wf", [
        "dokumentation", "anamnese", "verlaengerung",
        "folgeverlaengerung", "akutantrag", "entlassbericht",
    ])
    def test_user_content_baut_ohne_klient_klient(self, wf):
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow=wf,
            transcript="Test-Transkript.",
            patient_name={"anrede": "Frau", "vorname": "Maria",
                          "nachname": "Schmidt", "initial": "S."},
        )
        assert "die Klientin/der Klient" not in u
        # Aktueller Patient muss korrekt benannt sein
        assert "Frau S." in u
