"""
Root-conftest der Backend-Tests (v19.21, Sprint S3d - rekonstruiert).

Historie: Diese Datei lag nicht im Repo, obwohl tests/integration/conftest.py
und tests/integration/test_suite.py aus ihr importieren. Dadurch war die
Integrations-Suite ab frischem Clone nicht startbar (ModuleNotFoundError).
Mehrere Tests (test_jobs_multi.py, test_retention_db.py) umgingen das mit
eigenen Fixtures. Diese Rekonstruktion liefert genau die Symbole, die die
bestehenden Importe erwarten - keine weiteren Fixtures.

Aufgaben:
  1. Testumgebung VOR dem ersten `import app.*` setzen (pydantic-Settings
     lesen ENV nur beim Import): SQLite statt Postgres, keine externen Dienste.
  2. Pfad-Konstanten auf tests/fixtures/** (alle Dateien liegen im Repo).
  3. REAL_FILES / real_file(): optionale, anonymisierte Echtdateien - Tests
     ueberspringen sich selbst, wenn eine Datei fehlt.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# ── 1. Testumgebung ──────────────────────────────────────────────────────────
# SQLite-Datei statt :memory:, weil app.core.database mehrere Connections
# oeffnet (Background-Tasks der JobQueue) - :memory: waere pro Connection leer.
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="systelios_test_"))
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_DB_DIR / 'test.db'}")
os.environ.setdefault("UPLOAD_DIR", str(_TEST_DB_DIR / "uploads"))
os.environ.setdefault("OUTPUT_DIR", str(_TEST_DB_DIR / "outputs"))
os.environ.setdefault("LOG_FILE", str(_TEST_DB_DIR / "systelios.log"))
os.environ.setdefault("AUDIT_LOG_PATH", str(_TEST_DB_DIR / "audit.log"))
os.environ.setdefault("OLLAMA_HOST", "http://127.0.0.1:1")   # nie erreichbar - Tests mocken
os.environ.setdefault("FEEDBACK_NOTIFY", "off")
os.environ.setdefault("DIARIZATION_ENABLED", "false")
# AUTH_ENABLED bleibt auf dem Default (True): Tests, die Endpunkte ohne
# Dependency-Override aufrufen, muessen 401 sehen (siehe test_api.py).

# ── 2. Fixture-Pfade ─────────────────────────────────────────────────────────
FIXTURES_DIR = Path(__file__).parent / "fixtures"

AUDIO_KURZ      = FIXTURES_DIR / "audio" / "gespraech_kurz.wav"
AUDIO_LANG      = FIXTURES_DIR / "audio" / "gespraech_lang.wav"
AUDIO_REAL      = FIXTURES_DIR / "audio" / "gespraech_real.mp3"

PDF_VERLAUF     = FIXTURES_DIR / "pdf" / "verlaufsbericht.pdf"
PDF_SELBST_DIG  = FIXTURES_DIR / "pdf" / "selbstauskunft_digital.pdf"
PDF_SELBST_LEER = FIXTURES_DIR / "pdf" / "selbstauskunft_leer.pdf"

DOCX_ENTLASS_V  = FIXTURES_DIR / "docx" / "entlassbericht_vorlage.docx"
DOCX_ENTLASS_B  = FIXTURES_DIR / "docx" / "entlassbericht_beispiel.docx"
DOCX_VERL_V     = FIXTURES_DIR / "docx" / "verlaengerungsantrag_vorlage.docx"
DOCX_STILPROFIL = FIXTURES_DIR / "docx" / "stilprofil_verlaufsnotiz.docx"

TXT_TRANSKRIPT  = FIXTURES_DIR / "txt" / "transkript_einzelgespraech.txt"
TXT_STICHPUNKTE = FIXTURES_DIR / "txt" / "stichpunkte_verlauf.txt"
TXT_SELBST      = FIXTURES_DIR / "txt" / "selbstauskunft_text.txt"
TXT_VERLAUF     = FIXTURES_DIR / "txt" / "verlaufsdokumentation.txt"

# ── 3. Optionale (anonymisierte) Echtdateien ─────────────────────────────────
# Siehe tests/fixtures/README.md. Tests pruefen `.exists()` selbst bzw.
# nutzen real_file(), das bei fehlender Datei den Test ueberspringt.
REAL_FILES: dict[str, Path] = {
    "gespraech":                  AUDIO_REAL,
    "selbstauskunft_handschrift": FIXTURES_DIR / "pdf" / "selbstauskunft_handschrift.pdf",
    "verlauf":                    FIXTURES_DIR / "pdf" / "verlauf_real.pdf",
    "eigenbericht_wiederaufnahme": FIXTURES_DIR / "pdf" / "sysTelios_Klinik_Eigenbericht_Wiederaufnahme.pdf",
    "entlassbericht":             FIXTURES_DIR / "docx" / "entlassbericht_real.docx",
}


def real_file(key: str) -> Path:
    """Pfad einer Echtdatei aus REAL_FILES; ueberspringt den Test wenn sie fehlt."""
    path = REAL_FILES.get(key)
    if path is None or not path.exists():
        pytest.skip(f"Echtdatei '{key}' nicht vorhanden ({path})")
    return path
