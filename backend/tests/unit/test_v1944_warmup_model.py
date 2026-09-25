"""v19.44 - runpod-start.sh: WARMUP_MODEL waehlt das beim Start vorgewaermte
Modell (sonst OLLAMA_MODEL)."""
from __future__ import annotations

import subprocess
from pathlib import Path

START = Path(__file__).resolve().parents[2] / "runpod-start.sh"


def _block() -> str:
    s = START.read_text()
    a = s.index('OLLAMA_MODEL=$(grep "^OLLAMA_MODEL="', s.index("# 8b."))
    b = s.index("WARM_OPTS=", a)
    return s[a:b]


def _run(tmp_path, env_line="", backend_env="OLLAMA_MODEL=mistral-small3.2\n"):
    (tmp_path / ".env").write_text(backend_env)
    script = f'BACKEND_DIR="{tmp_path}"\n{env_line}\n' + _block() + '\necho "WARM=$WARM_MODEL"'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout


def test_default_ollama_model(tmp_path):
    assert "WARM=mistral-small3.2" in _run(tmp_path)


def test_aus_umgebung(tmp_path):
    assert "WARM=gemma4:31b" in _run(tmp_path, "WARMUP_MODEL=gemma4:31b")


def test_aus_backend_env(tmp_path):
    assert "WARM=gemma4:31b" in _run(tmp_path, backend_env="OLLAMA_MODEL=mistral-small3.2\nWARMUP_MODEL=gemma4:31b\n")


def test_curl_nutzt_warm_model():
    s = START.read_text()
    assert '\\"model\\": \\"${WARM_MODEL}\\"' in s
