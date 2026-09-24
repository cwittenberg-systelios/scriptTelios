"""
tests/eval/conftest.py
──────────────────────
Conftest fuer das Evaluations-Framework.

Diese Tests sprechen mit dem laufenden Backend + Ollama. Sie brauchen:
  - Erreichbares Ollama (sonst session-Abbruch oder Soft-Skip)
  - Optional: Vision-Modell (VISION_MODEL, seit v19.36 gemma4:31b) fuer OCR-Stufe 3
  - Optional: Wechselbares Whisper-Modell

Im Gegensatz zu unit/ und integration/ haben wir hier echte Netz-Aufrufe.
"""
import os
import urllib.request

import pytest


def pytest_addoption(parser):
    """Registriert die CLI-Flags, die test_eval.py via request.config.getoption
    liest und die run_model_eval.sh uebergibt.

    2026-07-03: Diese Registrierung fehlte auf dem aktuellen Branch - test_eval.py
    RUFT die Optionen ab (--qa, --eval-output, --qa-mode, --summary-mode,
    --transcribe), ohne dass sie je definiert waren. Folge: pytest brach mit
    'unrecognized arguments: --qa --eval-output ...' ab und der Modellvergleich
    schrieb leere Ordner. Default-Werte exakt wie in den getoption()-Aufrufen.
    """
    group = parser.getgroup("scriptTelios-eval", "scriptTelios Evaluations-Flags")
    group.addoption(
        "--eval-output", action="store", default=None,
        help="Zielverzeichnis fuer Eval-Outputs (<id>.txt/.eval.txt/.style.json). "
             "Ohne Angabe: EVAL_RESULTS_DIR.",
    )
    group.addoption(
        "--qa", action="store_true", default=False,
        help="Schreibt zusaetzlich <id>.qa.json mit quality_check-Ergebnissen.",
    )
    group.addoption(
        "--qa-mode", action="store", default="auto",
        choices=["auto", "critical_only", "all_issues"],
        help="Filtert die im QA-Modus beruecksichtigten Issues. "
             "auto=Default-Verhalten; critical_only/all_issues fuer A/B-Vergleich.",
    )
    group.addoption(
        "--summary-mode", action="store", default="auto",
        choices=["auto", "require_stage1", "require_no_stage1"],
        help="Validiert das Stage-1-Verdichtungsverhalten (v19.2). "
             "require_stage1 erzwingt Verdichtung, require_no_stage1 verbietet sie.",
    )
    group.addoption(
        "--transcribe", action="store_true", default=False,
        help="Erzwingt echte Whisper-Transkription statt vorgecachter Transkripte.",
    )


@pytest.fixture(scope="session", autouse=True)
def ollama_vision_setup():
    """
    Prueft einmal pro Session, ob das Vision-Modell (VISION_MODEL) in Ollama
    liegt, und warnt sonst. v19.36.2: KEIN automatischer Pull mehr - frueher
    zog jeder Eval-Lauf 'llava' (4,7 GB) nach, auch auf fast voller Platte
    (runpod-start.sh verzichtet aus genau diesem Grund auf Auto-Pull).
    Seit v19.36 ist gemma4:31b das Vision-Modell (eval_vision_ocr.py).
    """
    import json

    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    vision = os.environ.get("VISION_MODEL", "gemma4:31b")
    try:
        with urllib.request.urlopen(f"{ollama_host}/api/tags", timeout=3) as r:
            names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return
    if not any(vision in n for n in names):
        print(f"\n[WARN] Vision-Modell '{vision}' nicht in Ollama - OCR-Stufe 3 wird in Evals "
              f"fehlschlagen. Kein Auto-Pull (Plattenschutz); bei Bedarf manuell: ollama pull {vision}")


@pytest.fixture(scope="session", autouse=True)
def _configure_whisper_model_for_session(request):
    """
    Wenn --whisper-model gesetzt ist, wechselt das Backend-Modell vor den Tests
    und stellt es nach Abschluss wieder her. Backend muss laufen.
    """
    override_model = request.config.getoption("--whisper-model", default=None)
    if not override_model:
        yield
        return

    import httpx

    backend_url = os.environ.get("EVAL_BACKEND_URL", "http://localhost:8000")
    shared_secret = os.environ.get("CONFLUENCE_SHARED_SECRET", "")
    headers = {"X-Admin-Token": shared_secret} if shared_secret else {}
    previous = None

    try:
        with httpx.Client(base_url=backend_url, timeout=10.0) as client:
            r = client.get("/api/admin/whisper-model")
            if r.status_code == 200:
                previous = r.json().get("whisper_model")
                print(f"\n[WHISPER] Aktuelles Modell: {previous}", flush=True)

            r = client.post(
                "/api/admin/whisper-model",
                params={"model": override_model},
                headers=headers,
            )
            if r.status_code != 200:
                print(
                    f"\n[WHISPER] WARNUNG: Modellwechsel fehlgeschlagen "
                    f"({r.status_code} {r.text}) - Tests laufen mit {previous}",
                    flush=True,
                )
                previous = None
            else:
                print(
                    f"[WHISPER] Gewechselt auf {override_model} fuer diesen Testlauf",
                    flush=True,
                )
    except Exception as e:
        print(f"\n[WHISPER] WARNUNG: Admin-Endpoint nicht erreichbar ({e})", flush=True)
        previous = None

    yield

    if previous and previous != override_model:
        try:
            with httpx.Client(base_url=backend_url, timeout=10.0) as client:
                client.post(
                    "/api/admin/whisper-model",
                    params={"model": previous},
                    headers=headers,
                )
            print(f"\n[WHISPER] Zurueck auf {previous}", flush=True)
        except Exception as e:
            print(f"\n[WHISPER] Zurueckstellen fehlgeschlagen: {e}", flush=True)
