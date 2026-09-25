"""v19.43 - pod_bundle.sh: Frontend nur bei geaendertem Quell-Inhalt bauen
(nicht nach Zeitstempel), Bundle nur bei Aenderung zum Worker ausliefern."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "scripts" / "pod_bundle.sh"


@pytest.fixture()
def tree(tmp_path):
    fe, be = tmp_path / "frontend", tmp_path / "backend"
    for d in (fe / "src", fe / "node_modules", be / "static", be / "app" / "services", be / "app" / "core", be / "scripts"):
        d.mkdir(parents=True)
    for f in (fe / "klinische-dokumentation.jsx", fe / "src" / "interview-chat.jsx", fe / "package.json",
              fe / "vite.config.js", fe / "index.html", be / "app" / "services" / "prompts.py",
              be / "app" / "core" / "interview_sets.py"):
        f.write_text("x")
    (fe / "node_modules" / ".package-lock.json").write_text("{}")
    # Fake-npm: "run build" schreibt das Bundle (Inhalt = Quelle) und zaehlt Aufrufe
    npm = tmp_path / "npm"
    npm.write_text(f'#!/bin/bash\necho "$@" >> {tmp_path}/npm.log\n'
                   f'[ "$1" = run ] && cat {fe}/src/interview-chat.jsx > {be}/static/systelios.js\nexit 0\n')
    npm.chmod(0o755)
    # Fake-Deploy: protokolliert den Aufruf
    dep = tmp_path / "deploy.sh"
    dep.write_text(f'#!/bin/bash\necho "$SYSTELIOS_PROXY_BASE" >> {tmp_path}/deploy.log\necho "OK - Worker liefert"\n')
    return tmp_path, fe, be


def run(tree, cmd, **env):
    tmp, fe, be = tree
    e = {k: v for k, v in os.environ.items() if k not in ("SYSTELIOS_PROXY_BASE", "BUNDLE_UPLOAD_SECRET", "BUNDLE_AUTO_DEPLOY")}
    e.update({"FRONTEND_DIR": str(fe), "BACKEND_DIR": str(be), "NPM": str(tmp / "npm"),
              "DEPLOY_SCRIPT": str(tmp / "deploy.sh"), **env})
    return subprocess.run(["bash", str(SCRIPT), cmd], capture_output=True, text=True, env=e)


def builds(tree):
    log = tree[0] / "npm.log"
    return log.read_text().count("run build") if log.exists() else 0


class TestBuild:
    def test_erst_bauen_dann_nicht_mehr(self, tree):
        assert "gebaut" in run(tree, "build").stdout and builds(tree) == 1
        out = run(tree, "build").stdout
        assert "kein Rebuild" in out and builds(tree) == 1

    def test_zeitstempel_allein_loest_nichts_aus(self, tree):
        _, fe, _ = tree
        run(tree, "build")
        f = fe / "src" / "interview-chat.jsx"
        t = time.time() + 3600
        os.utime(f, (t, t))                            # neuer, aber gleicher Inhalt
        assert "kein Rebuild" in run(tree, "build").stdout and builds(tree) == 1

    def test_inhalt_aendert_sich(self, tree):
        _, fe, be = tree
        run(tree, "build")
        (fe / "src" / "interview-chat.jsx").write_text("neu")
        assert "Quellen geaendert" in run(tree, "build").stdout and builds(tree) == 2
        (be / "app" / "services" / "prompts.py").write_text("neu")
        run(tree, "build")
        assert builds(tree) == 3

    def test_generierte_prompt_defaults_zaehlen_nicht(self, tree):
        _, fe, _ = tree
        run(tree, "build")
        (fe / "src" / "prompt-defaults.jsx").write_text("generiert")
        run(tree, "build")
        assert builds(tree) == 1

    def test_bundle_fehlt(self, tree):
        _, _, be = tree
        run(tree, "build")
        (be / "static" / "systelios.js").unlink()
        run(tree, "build")
        assert builds(tree) == 2


def _meta(tree, sha):
    d = tree[0] / "worker" / "systelios.js"
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta").write_text(json.dumps({"sha256": sha}))
    return f"file://{tree[0] / 'worker'}"


def deploys(tree):
    log = tree[0] / "deploy.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


class TestDeploy:
    def test_ohne_konfiguration_nichts(self, tree):
        run(tree, "build")
        assert "nicht gesetzt" in run(tree, "deploy").stdout and deploys(tree) == 0

    def test_abschaltbar(self, tree):
        run(tree, "build")
        out = run(tree, "deploy", SYSTELIOS_PROXY_BASE="file:///x", BUNDLE_UPLOAD_SECRET="s", BUNDLE_AUTO_DEPLOY="false").stdout
        assert "abgeschaltet" in out and deploys(tree) == 0

    def test_nur_bei_aenderung(self, tree):
        _, fe, _ = tree
        run(tree, "build")
        base = _meta(tree, "alt")
        env = {"SYSTELIOS_PROXY_BASE": base, "BUNDLE_UPLOAD_SECRET": "s"}
        assert "ausgeliefert" in run(tree, "deploy", **env).stdout and deploys(tree) == 1
        assert "unveraendert" in run(tree, "deploy", **env).stdout and deploys(tree) == 1
        # Mac liefert etwas anderes aus -> Pod ueberschreibt NICHT (sein Bundle ist gleich)
        _meta(tree, "vom-mac")
        run(tree, "deploy", **env)
        assert deploys(tree) == 1
        # neuer Code auf dem Pod -> neues Bundle -> ausliefern
        (fe / "src" / "interview-chat.jsx").write_text("neu")
        run(tree, "build")
        run(tree, "deploy", **env)
        assert deploys(tree) == 2

    def test_worker_hat_es_schon(self, tree):
        import hashlib
        _, _, be = tree
        run(tree, "build")
        sha = hashlib.sha256((be / "static" / "systelios.js").read_bytes()).hexdigest()
        out = run(tree, "deploy", SYSTELIOS_PROXY_BASE=_meta(tree, sha), BUNDLE_UPLOAD_SECRET="s").stdout
        assert "schon" in out and deploys(tree) == 0

    def test_fehler_nie_fatal(self, tree):
        tmp, _, _ = tree
        run(tree, "build")
        (tmp / "deploy.sh").write_text("#!/bin/bash\necho kaputt >&2\nexit 1\n")
        r = run(tree, "deploy", SYSTELIOS_PROXY_BASE=_meta(tree, "x"), BUNDLE_UPLOAD_SECRET="s")
        assert r.returncode == 0 and "fehlgeschlagen" in r.stdout and "kaputt" in r.stdout
        assert "Worker hat" not in r.stdout


def test_runpod_start_nutzt_skript():
    s = (BACKEND / "runpod-start.sh").read_text()
    assert 'pod_bundle.sh" build' in s and 'pod_bundle.sh" deploy' in s
    assert "-newer" not in s[s.index("# 5. Frontend bauen"):s.index("# 5b.")]
    assert subprocess.run(["bash", "-n", str(BACKEND / "runpod-start.sh")]).returncode == 0
