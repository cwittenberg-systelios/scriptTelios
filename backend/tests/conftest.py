"""
tests/conftest.py
─────────────────
Minimaler Wurzel-Conftest fuer das ganze Test-Baum:

  - sys.path setzt das Backend-Wurzelverzeichnis vor, damit
    `from app.services.X import Y` ueberall greift.
  - Testumgebung wird gesetzt BEVOR app.core.config geladen wird.
  - CLI-Optionen fuer das Eval-Framework werden hier registriert,
    weil pytest_addoption nur im Top-Level-conftest greifen darf.

Alles weitere (DB-Initialisierung, Ollama-Setup, gemockte LLMs etc.)
gehoert in die jeweiligen Sub-conftest.py:

  tests/unit/conftest.py        - keine autouse-DB, keine Ollama-Aufrufe
  tests/integration/conftest.py - DB-Setup, TestClient, Mocks
  tests/eval/conftest.py        - echtes LLM, ollama_vision_setup
"""
import os
import sys
from pathlib import Path

# ── sys.path: Backend-Root vor allen Test-Imports ─────────────────────────────
_BACKEND_ROOT = Path(__file__).parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# ── Testumgebung (greift vor jedem `from app...` Import) ──────────────────────
# WICHTIG: app.core.config liest diese ENVs beim ersten Import. Wenn ein Test
# vorher schon `from app.core.config import settings` macht, sind die Werte
# bereits gefroren. Daher Sub-conftests die App-Code importieren MUESSEN
# dies hier vorher tun.
os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")
os.environ.setdefault("OLLAMA_MODEL", "qwen3:32b")
os.environ.setdefault("WHISPER_MODEL", "medium")
os.environ.setdefault("WHISPER_DEVICE", "cpu")
os.environ.setdefault("WHISPER_COMPUTE_TYPE", "int8")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_systelios.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-fuer-tests")
os.environ.setdefault("DELETE_AUDIO_AFTER_TRANSCRIPTION", "false")
os.environ.setdefault("UPLOAD_DIR", "/tmp/systelios_test_uploads")
os.environ.setdefault("OUTPUT_DIR", "/tmp/systelios_test_outputs")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("LOG_FILE", "/tmp/systelios_test.log")

os.makedirs("/tmp/systelios_test_uploads", exist_ok=True)
os.makedirs("/tmp/systelios_test_outputs", exist_ok=True)


# ── Fixture-Pfade (gemeinsam fuer alle Test-Ebenen) ───────────────────────────
FIXTURES = Path(__file__).parent / "fixtures"

AUDIO_KURZ       = FIXTURES / "audio" / "gespraech_kurz.wav"
AUDIO_LANG       = FIXTURES / "audio" / "gespraech_lang.wav"
PDF_VERLAUF      = FIXTURES / "pdf" / "verlaufsbericht.pdf"
PDF_SELBST_DIG   = FIXTURES / "pdf" / "selbstauskunft_digital.pdf"
PDF_SELBST_LEER  = FIXTURES / "pdf" / "selbstauskunft_leer.pdf"
DOCX_ENTLASS_V   = FIXTURES / "docx" / "entlassbericht_vorlage.docx"
DOCX_ENTLASS_B   = FIXTURES / "docx" / "entlassbericht_beispiel.docx"
DOCX_VERL_V      = FIXTURES / "docx" / "verlaengerungsantrag_vorlage.docx"
DOCX_STILPROFIL  = FIXTURES / "docx" / "stilprofil_verlaufsnotiz.docx"
TXT_TRANSKRIPT   = FIXTURES / "txt" / "transkript_einzelgespraech.txt"
TXT_STICHPUNKTE  = FIXTURES / "txt" / "stichpunkte_verlauf.txt"
TXT_SELBST       = FIXTURES / "txt" / "selbstauskunft_text.txt"
TXT_VERLAUF      = FIXTURES / "txt" / "verlaufsdokumentation.txt"

# Echte Dateien (optional, werden uebersprungen wenn nicht vorhanden)
REAL_FILES = {
    "audio":                       FIXTURES / "audio" / "gespraech_real.mp3",
    "selbstauskunft_handschrift":  FIXTURES / "pdf"   / "selbstauskunft_handschrift.pdf",
    "entlassbericht_real":         FIXTURES / "docx"  / "entlassbericht_real.docx",
    "verlauf_real":                FIXTURES / "pdf"   / "verlauf_real.pdf",
}


def real_file(key: str):
    """Pytest-Marker, der Tests skippt wenn echte Testdatei fehlt."""
    import pytest
    path = REAL_FILES.get(key)
    if path is None or not path.exists():
        return pytest.mark.skip(reason=f"Echte Testdatei nicht vorhanden: {key}")
    return pytest.mark.skipif(False, reason="")


# ── CLI-Optionen fuer Eval-Framework ──────────────────────────────────────────
# pytest_addoption MUSS im Top-Level-conftest stehen, sonst greifen die
# Optionen nicht zuverlaessig in Sub-Verzeichnissen.

def pytest_addoption(parser):
    parser.addoption(
        "--eval-output",
        action="store",
        default=None,
        help="Verzeichnis fuer Evaluations-Ergebnisse (nur tests/eval/test_eval.py)",
    )
    parser.addoption(
        "--eval-report",
        action="store_true",
        default=False,
        help="PDF-Report nach eval-Tests generieren",
    )
    parser.addoption(
        "--transcribe",
        action="store_true",
        default=False,
        help=(
            "Transkriptionen neu erzeugen und als <audio>.transcript.txt speichern. "
            "Ohne diesen Flag wird ein vorhandenes .transcript.txt geladen."
        ),
    )
    parser.addoption(
        "--whisper-model",
        action="store",
        default=None,
        help=(
            "Whisper-Modell nur fuer diesen Testlauf wechseln (z.B. medium). "
            "Setzt es vor dem ersten Audio-Test via /api/admin/whisper-model."
        ),
    )


def pytest_sessionfinish(session, exitstatus):
    """Nach dem Test-Run: PDF-Report generieren wenn eval-Tests liefen."""
    try:
        generate_report = session.config.getoption("--eval-report", default=False)
    except (ValueError, AttributeError):
        return

    if not generate_report:
        return

    results_dir = session.config.getoption("--eval-output", default=None)
    if not results_dir:
        results_dir = os.environ.get("EVAL_RESULTS_DIR", "/workspace/eval_results")

    results_path = Path(results_dir)
    if not results_path.exists():
        return

    eval_files = list(results_path.rglob("*.eval.txt"))
    if not eval_files:
        return

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "eval_report",
            Path(__file__).parent.parent / "scripts" / "eval_report.py",
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            charts_dir = results_path / ".charts"
            charts_dir.mkdir(exist_ok=True)

            data = mod.load_eval_results(results_path)
            total = sum(len(v) for v in data["workflows"].values())
            if total > 0:
                report_path = results_path / "eval_report.pdf"
                mod.build_report(data, report_path, charts_dir)
                print(f"\n{'='*60}")
                print(f"  PDF-Report erstellt: {report_path}")
                print(f"  {total} Testfaelle in {len(data['workflows'])} Workflows")
                print(f"{'='*60}")
    except Exception as e:
        print(f"\nWarnung: PDF-Report konnte nicht erstellt werden: {e}")
