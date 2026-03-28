"""
scriptTelios Evaluations-Framework
===================================

Testet die Qualitaet der LLM-Generierung gegen definierte Erwartungen.
Laeuft gegen den laufenden Backend-Server (nicht gegen Mocks).

Aufruf:
    # Alle Workflows testen:
    pytest tests/test_eval.py -v --tb=short

    # Nur einen Workflow:
    pytest tests/test_eval.py -v -k "entlassbericht"

    # Mit Output-Speicherung (fuer manuelles Review):
    pytest tests/test_eval.py -v --eval-output /workspace/eval_results/

Voraussetzungen:
    - Backend laeuft auf localhost:8000
    - Ollama laeuft mit dem konfigurierten Modell
    - Verlaufsdoku-PDFs liegen in tests/fixtures/eval/ (optional)
"""
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx
import pytest

logger = logging.getLogger(__name__)

# ── Konfiguration ────────────────────────────────────────────────────────────

BACKEND_URL = os.environ.get("EVAL_BACKEND_URL", "http://localhost:8000")
FIXTURES_PATH = Path(__file__).parent / "fixtures" / "eval" / "fixtures.json"
EVAL_DATA_DIR = Path(os.environ.get("EVAL_DATA_DIR", "/workspace/eval_data"))
TIMEOUT = 300  # 5 Minuten pro Generierung (lang wegen GPU-Kaltstart)


def pytest_addoption(parser):
    """CLI-Option fuer Output-Verzeichnis."""
    parser.addoption(
        "--eval-output",
        action="store",
        default=None,
        help="Verzeichnis fuer Evaluations-Ergebnisse (optional)",
    )


# ── Fixtures laden ───────────────────────────────────────────────────────────

def _load_fixtures() -> dict:
    with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


FIXTURES = _load_fixtures()


def _all_test_cases():
    """Generiert (workflow, test_case) Tupel fuer Parametrisierung."""
    cases = []
    for workflow in ["entlassbericht", "verlaengerung", "anamnese", "dokumentation"]:
        for tc in FIXTURES.get(workflow, []):
            cases.append((workflow, tc))
    return cases


# ── API-Helfer ───────────────────────────────────────────────────────────────

async def _generate(
    workflow: str,
    prompt: str,
    diagnosen: list[str] | None = None,
    input_files: dict | None = None,
) -> dict:
    """
    Sendet einen Generierungs-Job und wartet auf das Ergebnis.

    input_files: optionales Dict mit Datei-Feldern, z.B.
      {"vorbefunde": "/workspace/eval_data/EB-FrauM/verlauf.pdf",
       "selbstauskunft": "/workspace/eval_data/Anamnese-FrauT/selbstauskunft.pdf"}
    """
    form_data = {
        "workflow": workflow,
        "prompt": prompt,
    }
    if diagnosen:
        form_data["diagnosen"] = ",".join(diagnosen)

    # Dateien vorbereiten (optional)
    files_to_upload = {}
    if input_files:
        for field_name, file_path in input_files.items():
            p = Path(file_path)
            # Relative Pfade gegen EVAL_DATA_DIR aufloesen
            if not p.is_absolute():
                p = EVAL_DATA_DIR / p
            if p.exists():
                files_to_upload[field_name] = (p.name, open(p, "rb"))
                logger.info("Eval-Input: %s = %s", field_name, p)
            else:
                logger.warning("Eval-Input nicht gefunden: %s (uebersprungen)", p)

    try:
        async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=30.0) as client:
            # Job erstellen – mit oder ohne Dateien
            r = await client.post(
                "/api/jobs/generate",
                data=form_data,
                files=files_to_upload or None,
            )
            r.raise_for_status()
            job_id = r.json()["job_id"]

            # Pollen bis fertig
            t0 = time.time()
            while time.time() - t0 < TIMEOUT:
                r = await client.get(f"/api/jobs/{job_id}")
                r.raise_for_status()
                job = r.json()

                if job["status"] == "done":
                    return job
                if job["status"] == "error":
                    raise RuntimeError(f"Job fehlgeschlagen: {job.get('error_msg', '?')}")
                if job["status"] == "cancelled":
                    raise RuntimeError("Job wurde abgebrochen")

                await _async_sleep(3)

            raise TimeoutError(f"Job {job_id} nicht in {TIMEOUT}s fertig geworden")
    finally:
        # Datei-Handles schliessen
        for _name, (_, fh) in files_to_upload.items():
            fh.close()


async def _async_sleep(seconds: float):
    import asyncio
    await asyncio.sleep(seconds)


# ── Evaluations-Checks ──────────────────────────────────────────────────────

class EvalResult:
    """Sammelt Evaluations-Ergebnisse fuer einen Testfall."""

    def __init__(self, workflow: str, test_id: str, text: str):
        self.workflow = workflow
        self.test_id = test_id
        self.text = text
        self.word_count = len(text.split())
        self.issues: list[str] = []
        self.passed: list[str] = []

    def check_word_count(self, min_words: int, max_words: int):
        if self.word_count < min_words:
            self.issues.append(f"Zu kurz: {self.word_count}w < {min_words}w Minimum")
        elif self.word_count > max_words:
            self.issues.append(f"Zu lang: {self.word_count}w > {max_words}w Maximum")
        else:
            self.passed.append(f"Wortanzahl OK: {self.word_count}w ({min_words}-{max_words})")

    def check_required_keywords(self, keywords: list[str]):
        for kw in keywords:
            if kw.lower() not in self.text.lower():
                self.issues.append(f"Keyword fehlt: '{kw}'")
            else:
                self.passed.append(f"Keyword vorhanden: '{kw}'")

    def check_forbidden_patterns(self, patterns: list[str]):
        for pat in patterns:
            if pat in self.text:
                self.issues.append(f"Verbotenes Pattern gefunden: '{pat}'")
            else:
                self.passed.append(f"Pattern nicht vorhanden: '{pat}'")

    def check_required_sections(self, sections: list[str]):
        for section in sections:
            if section.lower() not in self.text.lower():
                self.issues.append(f"Sektion fehlt: '{section}'")
            else:
                self.passed.append(f"Sektion vorhanden: '{section}'")

    def check_forbidden_names(self, names: list[str]):
        for name in names:
            if name in self.text:
                self.issues.append(f"DATENSCHUTZ: Name '{name}' im Text gefunden!")
            else:
                self.passed.append(f"Datenschutz OK: '{name}' nicht im Text")

    def check_hallucinations(self, hallucinations: list[str]):
        for h in hallucinations:
            if h.lower() in self.text.lower():
                self.issues.append(f"HALLUZINATION: '{h}' gefunden – nicht in Quelldaten!")
            else:
                self.passed.append(f"Keine Halluzination: '{h}'")

    def check_befund_separator(self, separator: str):
        if separator in self.text:
            self.passed.append(f"Befund-Separator '{separator}' vorhanden")
        else:
            self.issues.append(f"Befund-Separator '{separator}' fehlt – Anamnese/Befund nicht getrennt")

    def check_no_think_blocks(self):
        if "</think>" in self.text or "<think>" in self.text:
            self.issues.append("Think-Block im Output gefunden!")
        else:
            self.passed.append("Kein Think-Block im Output")

    def summary(self) -> str:
        status = "PASS" if not self.issues else "FAIL"
        lines = [
            f"[{status}] {self.workflow}/{self.test_id} ({self.word_count}w)",
            f"  ✓ {len(self.passed)} Checks bestanden",
        ]
        if self.issues:
            lines.append(f"  ✗ {len(self.issues)} Probleme:")
            for issue in self.issues:
                lines.append(f"    - {issue}")
        return "\n".join(lines)

    @property
    def score(self) -> float:
        """Score 0.0-1.0 basierend auf bestandenen Checks."""
        total = len(self.passed) + len(self.issues)
        return len(self.passed) / total if total > 0 else 0.0


# ── Pytest-Tests ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("workflow,test_case", _all_test_cases(),
                         ids=[f"{w}-{tc['id']}" for w, tc in _all_test_cases()])
async def test_eval_workflow(workflow, test_case, request):
    """
    Generiert Text fuer einen Testfall und prueft die Qualitaet.

    Erwartet einen laufenden Backend-Server auf EVAL_BACKEND_URL.
    Ueberspringt automatisch wenn Server nicht erreichbar.
    """
    # Server-Erreichbarkeit pruefen
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{BACKEND_URL}/api/health")
            if r.status_code != 200:
                pytest.skip(f"Backend nicht healthy: {r.status_code}")
    except httpx.ConnectError:
        pytest.skip(f"Backend nicht erreichbar: {BACKEND_URL}")

    # Generieren
    prompt = test_case["prompt"]
    diagnosen = test_case.get("diagnosen")
    input_files = test_case.get("input_files")

    # Pruefen ob Input-Dateien vorhanden sind (optional)
    if input_files:
        missing = []
        for field, path in input_files.items():
            p = Path(path) if Path(path).is_absolute() else EVAL_DATA_DIR / path
            if not p.exists():
                missing.append(f"{field}: {p}")
        if missing:
            logger.warning(
                "Eval-Input-Dateien nicht gefunden (Test laeuft ohne):\n  %s",
                "\n  ".join(missing),
            )
            input_files = None  # Ohne Dateien weitermachen

    try:
        job = await _generate(workflow, prompt, diagnosen, input_files)
    except (RuntimeError, TimeoutError) as e:
        pytest.fail(f"Generierung fehlgeschlagen: {e}")

    text = job.get("result_text", "")
    if not text:
        pytest.fail("Leerer Output")

    # Evaluieren
    expected = test_case["expected"]
    ev = EvalResult(workflow, test_case["id"], text)

    ev.check_no_think_blocks()

    if "min_words" in expected:
        ev.check_word_count(expected["min_words"], expected.get("max_words", 9999))

    if "required_keywords" in expected:
        ev.check_required_keywords(expected["required_keywords"])

    if "forbidden_patterns" in expected:
        ev.check_forbidden_patterns(expected["forbidden_patterns"])

    if "required_sections" in expected:
        ev.check_required_sections(expected["required_sections"])

    if "must_contain_sections" in expected:
        ev.check_required_sections(expected["must_contain_sections"])

    if "forbidden_names" in expected:
        ev.check_forbidden_names(expected["forbidden_names"])

    if "must_not_hallucinate" in expected:
        ev.check_hallucinations(expected["must_not_hallucinate"])

    if "befund_separator" in expected:
        ev.check_befund_separator(expected["befund_separator"])

    # Ergebnis loggen
    print(f"\n{ev.summary()}")

    # Optional: Ergebnis speichern
    output_dir = request.config.getoption("--eval-output")
    if output_dir:
        out_path = Path(output_dir) / workflow
        out_path.mkdir(parents=True, exist_ok=True)
        result_file = out_path / f"{test_case['id']}.txt"
        result_file.write_text(text, encoding="utf-8")
        summary_file = out_path / f"{test_case['id']}.eval.txt"
        summary_file.write_text(ev.summary(), encoding="utf-8")

    # Test failt wenn es kritische Issues gibt
    critical = [i for i in ev.issues if "DATENSCHUTZ" in i or "HALLUZINATION" in i]
    if critical:
        pytest.fail(f"Kritische Probleme:\n" + "\n".join(f"  - {i}" for i in critical))

    # Warnungen fuer nicht-kritische Issues
    if ev.issues:
        for issue in ev.issues:
            logger.warning("[%s/%s] %s", workflow, test_case["id"], issue)

    # Score mindestens 70%
    assert ev.score >= 0.7, (
        f"Score zu niedrig: {ev.score:.0%} ({len(ev.passed)}/{len(ev.passed)+len(ev.issues)} Checks)\n"
        f"{ev.summary()}"
    )
