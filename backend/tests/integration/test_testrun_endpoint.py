"""
Tests fuer /api/testrun (vorher test_prompts_v15.py::TestTestRunEndpoint).
"""
import pytest
from unittest.mock import patch, MagicMock

class TestTestRunEndpoint:
    """Test fuer den neuen /api/testrun-Endpunkt."""

    def test_endpoint_default_schliesst_test_eval_aus(self):
        """Default-Run laeuft tests/unit + tests/integration, NICHT tests/eval."""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        with patch("app.api.testrun._run_pytest_blocking") as mock_run:
            mock_run.return_value = {"exitCode": 0, "output": "ok"}
            r = client.post("/api/testrun", json={})

        assert r.status_code == 200
        cmd = mock_run.call_args[0][0]
        # Phase 2 Refactor: default-paths sind jetzt tests/unit + tests/integration,
        # was tests/eval implizit ausschliesst. Frueher: --ignore=tests/test_eval.py
        assert "tests/unit" in cmd
        assert "tests/integration" in cmd
        assert "tests/eval" not in cmd, (
            f"Default-Run darf tests/eval nicht enthalten, cmd war: {cmd}"
        )

    def test_endpoint_include_eval_inkludiert_test_eval(self):
        """Mit include_eval=True wird tests/eval mitgelaufen."""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        with patch("app.api.testrun._run_pytest_blocking") as mock_run:
            mock_run.return_value = {"exitCode": 0, "output": "ok"}
            r = client.post("/api/testrun", json={"include_eval": True})

        assert r.status_code == 200
        cmd = mock_run.call_args[0][0]
        assert "tests/eval" in cmd, (
            f"include_eval=True muesste tests/eval inkludieren, cmd war: {cmd}"
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
            # Signatur: (cmd, timeout_s, env_overrides)
            result = _run_pytest_blocking(["pytest"], 600, {})

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
