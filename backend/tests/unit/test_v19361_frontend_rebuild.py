"""v19.36.1 - runpod-start.sh erkennt Aenderungen in frontend/src (und den
Quellen von prompt-defaults.jsx) als Grund fuer einen Frontend-Rebuild."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

START = Path(__file__).resolve().parents[2] / "runpod-start.sh"


def _block() -> str:
    s = START.read_text()
    a = s.index("# Pruefen ob Bundle aktueller als Quellcode ist")
    b = s.index('if [ "$NEEDS_BUILD" = "true" ]; then', a)
    return s[a:b]


@pytest.fixture()
def tree(tmp_path):
    fe, be = tmp_path / "frontend", tmp_path / "backend"
    for d in (fe / "src", be / "static", be / "app" / "services", be / "app" / "core", be / "scripts"):
        d.mkdir(parents=True)
    files = [fe / "klinische-dokumentation.jsx", fe / "src" / "interview-chat.jsx", fe / "package.json",
             fe / "vite.config.js", be / "app" / "services" / "prompts.py", be / "app" / "core" / "interview_sets.py"]
    for f in files:
        f.write_text("x")
    old = time.time() - 3600
    for f in files:
        os.utime(f, (old, old))
    bundle = be / "static" / "systelios.js"
    bundle.write_text("bundle")
    return fe, be


def _run(fe: Path, be: Path) -> str:
    script = f'GO=""; OK=""; FRONTEND_DIR="{fe}"; BACKEND_DIR="{be}"; BUNDLE="{be}/static/systelios.js"\n' \
             + _block() + '\necho "NEEDS_BUILD=$NEEDS_BUILD"'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout


def test_aktuell_kein_build(tree):
    assert "NEEDS_BUILD=false" in _run(*tree)


def test_aenderung_in_src_loest_build_aus(tree):
    fe, be = tree
    (fe / "src" / "interview-chat.jsx").write_text("neu")
    out = _run(fe, be)
    assert "NEEDS_BUILD=true" in out and "src/interview-chat.jsx" in out


def test_prompts_py_loest_build_aus(tree):
    fe, be = tree
    (be / "app" / "services" / "prompts.py").write_text("neu")
    assert "NEEDS_BUILD=true" in _run(fe, be)


def test_ohne_bundle_build(tree):
    fe, be = tree
    (be / "static" / "systelios.js").unlink()
    assert "NEEDS_BUILD=true" in _run(fe, be)
