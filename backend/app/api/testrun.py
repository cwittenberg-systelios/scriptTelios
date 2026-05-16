"""
POST /api/testrun  – Fuehrt die pytest-Suite aus und liefert Ergebnis.

Output-Format (gem. spezifizierter Anforderung):
    {
      "exitCode": 0,
      "output":   "============================= test session starts ...\n..."
    }

Sicherheits-Hinweis:
    Dieser Endpunkt fuehrt Code (pytest) im Backend-Container aus.
    NICHT oeffentlich freigeben! In Produktion durch API-Auth (K1)
    und/oder IP-Allowlist absichern.

Default-Verhalten (Phase 2 Refactor):
    POST /api/testrun {} laeuft die Verzeichnisse tests/unit + tests/integration.
    tests/eval/ wird NICHT mit-gelaufen (braucht echtes LLM, 10-30 Min).

Optional-Parameter:
    selector:     pytest -k Filter (z.B. "TestPatientName")
    paths:        Liste von Test-Dateien/-Verzeichnissen (relativ zu BACKEND_ROOT)
                  Default: ["tests/unit", "tests/integration"]
    timeout_s:    Hartes Timeout in Sekunden. Default: 300 (5 Min - Unit-Tests).
    verbose:      "-v" Flag aktivieren. Default: True.
    include_eval: Wenn True: tests/eval wird mitgelaufen (braucht Ollama).
                  Default: False.

Verhalten:
    - Synchroner Run im threadpool, blockiert nicht den Event-Loop.
    - Stdout + Stderr werden konkateniert in "output" zurueckgegeben.
    - exitCode entspricht pytest-Returncode (0=alle PASS, 1=test failure,
      2=test setup error, 5=keine Tests gefunden).
    - Timeout liefert exitCode -1 mit Hinweis im "output".
"""
from __future__ import annotations

import logging
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Konfiguration ────────────────────────────────────────────────────────────

# BACKEND_ROOT: Verzeichnis aus dem pytest startet.
# Standard: das `backend/`-Verzeichnis, in dem `pytest.ini` und `tests/` liegen.
# Wir leiten es ab vom Pfad dieser Datei: ../../.. (api → app → backend)
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

# Maximale Output-Groesse die wir zurueckgeben (Schutz vor riesigen Logs).
_MAX_OUTPUT_BYTES = 2 * 1024 * 1024  # 2 MB


# ── Default-Pfade fuer den Test-Run ──────────────────────────────────────────
#
# Mit der neuen tests/{unit,integration,eval}/-Struktur sind die Pfad-Defaults
# klar:
#   - tests/unit         - pure Logik, keine Netz-Aufrufe, kein autouse-DB
#   - tests/integration  - FastAPI TestClient + gemockte LLMs, init_test_db
#   - tests/eval         - echtes LLM, NICHT im default-run
#
# Frueher mussten einzelne Test-Dateien hier weiss-gelistet werden, weil das
# alte conftest.py einen autouse-Fixture (ollama_vision_setup) hatte das beim
# Start `ollama pull llava` ausfuehrte. Mit dem split-conftest (eval/ hat den
# Vision-Setup, unit/+integration/ nicht) entfaellt das.
_DEFAULT_TEST_PATHS = [
    "tests/unit",
    "tests/integration",
]

# Tests die echtes LLM/Ollama brauchen - werden im Default NICHT gelaufen.
_LLM_DEPENDENT_TESTS = [
    "tests/eval",
]

# Umgebungsvariablen die das pytest-Setup zwingen, Ollama nicht anzufassen.
# Greift auf:
#   - tests/eval/conftest.py::ollama_vision_setup (prueft urlopen auf OLLAMA_HOST)
#   - llm.py (clients werden via _reset_ollama_client-Fixture geleert)
_OLLAMA_DISABLE_ENV = {
    # Nicht erreichbarer Host -> ollama_vision_setup gibt sofort auf
    # statt `ollama pull llava` zu starten.
    "OLLAMA_HOST": "http://127.0.0.1:1",
    # Test-DB
    "DATABASE_URL": "sqlite+aiosqlite:///./testrun.db",
    "SECRET_KEY": "testrun-secret",
}


# ── Request/Response-Schemas ─────────────────────────────────────────────────

class TestRunRequest(BaseModel):
    """Optionale Steuerung des Testlaufs."""
    selector: Optional[str] = Field(
        default=None,
        description="pytest -k Filter (z.B. 'test_prompts_v15')",
    )
    paths: Optional[list[str]] = Field(
        default=None,
        description=(
            "Test-Dateien/-Verzeichnisse relativ zum Backend-Root. "
            "Default: alle Unit-Tests, ABER OHNE test_eval.py "
            "(Eval-Suite braucht laufendes LLM und 10-30 Min)."
        ),
    )
    timeout_s: int = Field(
        default=300,
        ge=10,
        le=3600,
        description=(
            "Hartes Timeout in Sekunden (10-3600). Default 300 (5 Min) ist "
            "ausreichend fuer Unit-Tests. Fuer include_eval=True auf >= 1800 setzen."
        ),
    )
    verbose: bool = Field(default=True, description="pytest -v aktivieren")
    include_eval: bool = Field(
        default=False,
        description=(
            "Wenn True: test_eval.py wird mitgelaufen (braucht laufendes LLM, "
            "dauert 10-30 Min und blockiert ggf. Ollama). Default False."
        ),
    )


class TestRunResponse(BaseModel):
    """Antwort-Schema entsprechend der spezifizierten Anforderung."""
    exitCode: int
    output: str


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────

def _validate_paths(paths: list[str]) -> list[str]:
    """
    Validiert dass alle Pfade INNERHALB von _BACKEND_ROOT liegen.
    Schutz gegen Path-Traversal.
    """
    valid = []
    for p in paths:
        # Keine absoluten Pfade
        if Path(p).is_absolute():
            raise HTTPException(
                status_code=400,
                detail=f"Absolute Pfade nicht erlaubt: {p}",
            )
        # Resolve und pruefen dass innerhalb BACKEND_ROOT
        resolved = (_BACKEND_ROOT / p).resolve()
        try:
            resolved.relative_to(_BACKEND_ROOT)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Pfad ausserhalb des Test-Verzeichnisses: {p}",
            )
        valid.append(str(resolved.relative_to(_BACKEND_ROOT)))
    return valid


def _validate_selector(selector: str) -> str:
    """
    Validiert dass der pytest-Selector keine Shell-Metazeichen enthaelt.
    pytest -k akzeptiert eine Boolean-Expression mit alphanumeric+underscore+space+
    'and'/'or'/'not'/Klammern.
    """
    import re
    # Erlaube: a-z A-Z 0-9 _ - leerzeichen ( ) [ ]
    if not re.fullmatch(r"[\w\s\-\(\)\[\]\.]+", selector or ""):
        raise HTTPException(
            status_code=400,
            detail=f"Ungueltige Zeichen im Selector: {selector!r}",
        )
    return selector


def _truncate_output(text: str) -> str:
    """Schneidet riesigen Output ab und markiert die Truncation."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_OUTPUT_BYTES:
        return text
    head = encoded[: _MAX_OUTPUT_BYTES // 2].decode("utf-8", errors="replace")
    tail = encoded[-_MAX_OUTPUT_BYTES // 2 :].decode("utf-8", errors="replace")
    return (
        head
        + "\n\n... [OUTPUT GEKUERZT - UEBER {} BYTES] ...\n\n".format(_MAX_OUTPUT_BYTES)
        + tail
    )


def _filter_existing_paths(paths: list[str]) -> list[str]:
    """Behaelt nur Pfade die tatsaechlich existieren (verhindert pytest-Fehler
    wenn z.B. test_postprocessing.py noch nicht eingespielt wurde)."""
    existing = []
    for p in paths:
        full = _BACKEND_ROOT / p
        if full.exists():
            existing.append(p)
        else:
            logger.info("testrun: Default-Pfad nicht vorhanden, uebersprungen: %s", p)
    return existing


def _run_pytest_blocking(cmd: list[str], timeout_s: int, env_overrides: dict[str, str]) -> dict[str, Any]:
    """
    Synchroner pytest-Aufruf. Wird via run_in_threadpool ausgefuehrt damit
    der FastAPI Event-Loop nicht blockiert.

    env_overrides: zusaetzliche Umgebungsvariablen fuer den pytest-Subprozess
    (z.B. um Ollama-Initialisierung in conftest.py zu verhindern).
    """
    import os as _os
    logger.info("testrun: starte pytest: %s", " ".join(shlex.quote(c) for c in cmd))
    # Env zusammenbauen: Original-Env + Overrides
    env = _os.environ.copy()
    env.update(env_overrides)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(_BACKEND_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=env,
        )
        return {
            "exitCode": result.returncode,
            "output": _truncate_output((result.stdout or "") + (result.stderr or "")),
        }
    except subprocess.TimeoutExpired as e:
        partial = ""
        if e.stdout:
            partial += e.stdout if isinstance(e.stdout, str) else e.stdout.decode("utf-8", "replace")
        if e.stderr:
            partial += e.stderr if isinstance(e.stderr, str) else e.stderr.decode("utf-8", "replace")
        return {
            "exitCode": -1,
            "output": (
                _truncate_output(partial)
                + f"\n\n[TIMEOUT: pytest hat das Limit von {timeout_s}s ueberschritten]"
            ),
        }
    except FileNotFoundError as e:
        return {
            "exitCode": -2,
            "output": f"[FEHLER] pytest nicht gefunden: {e}",
        }
    except Exception as e:
        logger.exception("testrun: unerwarteter Fehler")
        return {
            "exitCode": -3,
            "output": f"[FEHLER] {type(e).__name__}: {e}",
        }


# ── Route ────────────────────────────────────────────────────────────────────

@router.post("/testrun", response_model=TestRunResponse)
async def testrun(req: TestRunRequest = TestRunRequest()) -> TestRunResponse:
    """
    Fuehrt die pytest-Test-Suite aus und gibt Returncode + Output zurueck.

    Beispiel-Request:
        POST /api/testrun
        {}                                  # Default: Unit-Tests Whitelist (OHNE Ollama)
        {"include_eval": true}              # Auch test_eval.py mitlaufen lassen
        {"selector": "test_prompts_v15"}    # Nur passende Tests
        {"paths": ["tests/test_eval.py"]}   # Explizit nur Eval

    Antwort:
        {
          "exitCode": 0,
          "output":   "============================= test session starts ..."
        }
    """
    # Pfade waehlen
    if req.paths:
        # Explizite Pfade vom Aufrufer haben Vorrang
        paths = _validate_paths(req.paths)
        # Bei expliziten Pfaden: KEIN Ollama-Disable - Aufrufer entscheidet
        env_overrides = {}
    else:
        # Default: explizite Whitelist von Unit-Test-Dateien (KEIN "tests/")
        # Grund: pytest auf "tests/" laedt conftest.py mit autouse-Fixture
        # die 'ollama pull llava' triggert (Ollama wird hochgefahren).
        paths = _filter_existing_paths(_DEFAULT_TEST_PATHS)
        if req.include_eval:
            paths.extend(_filter_existing_paths(_LLM_DEPENDENT_TESTS))
            env_overrides = {}
        else:
            env_overrides = dict(_OLLAMA_DISABLE_ENV)

    if not paths:
        return TestRunResponse(
            exitCode=5,
            output="[FEHLER] Keine Test-Dateien gefunden. Whitelist: "
                   + ", ".join(_DEFAULT_TEST_PATHS),
        )

    # Selector validieren
    selector = _validate_selector(req.selector) if req.selector else None

    # pytest-Kommando bauen
    cmd = [sys.executable, "-m", "pytest"]
    if req.verbose:
        cmd.append("-v")
    if selector:
        cmd.extend(["-k", selector])
    # Nicht mehr noetig: --ignore=tests/test_eval.py - die Whitelist enthaelt
    # test_eval.py nur wenn include_eval=True
    cmd.extend(paths)

    # Im Threadpool ausfuehren (kein Event-Loop-Blocking)
    result = await run_in_threadpool(_run_pytest_blocking, cmd, req.timeout_s, env_overrides)

    logger.info(
        "testrun: pytest fertig (exitCode=%d, output_len=%d)",
        result["exitCode"], len(result["output"]),
    )
    return TestRunResponse(**result)

    logger.info(
        "testrun: pytest fertig (exitCode=%d, output_len=%d)",
        result["exitCode"], len(result["output"]),
    )
    return TestRunResponse(**result)


# Convenience: GET /testrun ohne Body fuer einfaches Aufrufen ueber Browser
@router.get("/testrun", response_model=TestRunResponse)
async def testrun_get() -> TestRunResponse:
    """GET-Variante mit Defaults (alle Tests, 600s Timeout, verbose)."""
    return await testrun(TestRunRequest())
