"""v19.36.2 - gemma4 ist Vision-Modell; kein Auto-Pull von llava in Evals."""
from __future__ import annotations

from pathlib import Path

from app.core.config import Settings

TESTS = Path(__file__).resolve().parents[1]


def test_default_vision_model_ist_gemma():
    assert Settings.model_fields["VISION_MODEL"].default == "gemma4:31b"


def test_eval_conftest_zieht_nichts_nach():
    s = (TESTS / "eval" / "conftest.py").read_text()
    assert '"pull"' not in s and "subprocess.run" not in s
