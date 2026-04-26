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

Optional-Parameter:
    selector:   pytest -k Filter (z.B. "test_prompts_v15", "TestPatient")
                Default: alle Tests in tests/
    paths:      Liste von Test-Dateien/-Verzeichnissen (relativ zu BACKEND_ROOT)
                Default: ["tests/"]
    timeout_s:  Hartes Timeout in Sekunden. Default: 600 (10 Min).
    verbose:    "-v" Flag aktivieren. Default: True.

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
        default=600,
        ge=10,
        le=3600,
        description="Hartes Timeout in Sekunden (10-3600).",
    )
    verbose: bool = Field(default=True, description="pytest -v aktivieren")
    include_eval: bool = Field(
        default=False,
        description=(
            "Wenn True: test_eval.py wird mitgelaufen (braucht laufendes LLM, "
            "dauert 10-30 Min). Default False - nur Unit-Tests."
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


def _run_pytest_blocking(cmd: list[str], timeout_s: int) -> dict[str, Any]:
    """
    Synchroner pytest-Aufruf. Wird via run_in_threadpool ausgefuehrt damit
    der FastAPI Event-Loop nicht blockiert.
    """
    logger.info("testrun: starte pytest: %s", " ".join(shlex.quote(c) for c in cmd))
    try:
        result = subprocess.run(
            cmd,
            cwd=str(_BACKEND_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,  # check=False: wir behandeln returncode selbst
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
        {}                                  # Default: Unit-Tests (OHNE test_eval.py)
        {"include_eval": true}              # Auch test_eval.py mitlaufen lassen
        {"selector": "test_prompts_v15"}    # Nur passende Tests
        {"paths": ["tests/test_eval.py"]}   # Explizit nur Eval

    Antwort:
        {
          "exitCode": 0,
          "output":   "============================= test session starts ..."
        }
    """
    # Pfade validieren
    if req.paths:
        # Explizite Pfade vom Aufrufer haben Vorrang
        paths = _validate_paths(req.paths)
    else:
        # Default: Unit-Tests, ohne test_eval.py
        # test_eval.py braucht laufendes LLM und dauert 10-30 Min,
        # ist also nicht sinnvoll als Standard-Smoke-Test.
        paths = ["tests/"]
        ignore_args = []
        if not req.include_eval:
            ignore_args = ["--ignore=tests/test_eval.py"]
    # Selector validieren
    selector = _validate_selector(req.selector) if req.selector else None

    # pytest-Kommando bauen
    cmd = [sys.executable, "-m", "pytest"]
    if req.verbose:
        cmd.append("-v")
    if selector:
        cmd.extend(["-k", selector])
    if not req.paths and not req.include_eval:
        cmd.append("--ignore=tests/test_eval.py")
    cmd.extend(paths)

    # Im Threadpool ausfuehren (kein Event-Loop-Blocking)
    result = await run_in_threadpool(_run_pytest_blocking, cmd, req.timeout_s)

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
