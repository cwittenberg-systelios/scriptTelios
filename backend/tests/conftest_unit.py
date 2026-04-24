"""
Test-Konfiguration fuer die Unit-Tests.

Diese Tests laufen ohne LLM/DB/Backend — reine Python-Funktion-Tests.
Werden mit `pytest tests/test_*_unit.py` (oder einem Subset) ausgefuehrt.

Wenn die Eval-Tests (tests/test_eval.py) auch laufen sollen, kann das
existierende conftest.py weiter verwendet werden — diese Datei hier
ergaenzt nur die Pfad-Konfiguration fuer Unit-Tests.
"""
import sys
from pathlib import Path

# Backend-Root zum sys.path hinzufuegen damit `from app.services.x import ...` geht
_BACKEND_ROOT = Path(__file__).parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
