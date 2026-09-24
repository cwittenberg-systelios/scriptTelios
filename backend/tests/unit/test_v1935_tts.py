"""v19.35 - Server-Vorlesen zum Testen: TTS-Dienst (Fake-Engine), HTTP-Handler,
Backend-Proxy-Endpoints, perf-Zeilen, Setup-Skripte."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from app.core.config import settings

BACKEND = Path(__file__).resolve().parents[2]


def _load_server():
    spec = importlib.util.spec_from_file_location("tts_server", BACKEND / "tts_service" / "tts_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ts = _load_server()


class FakeEngine(ts.Engine):
    key = "piper"
    label = "Piper (Thorsten)"

    def __init__(self, ok=True, reason=""):
        super().__init__()
        self.ok, self.reason, self.calls, self.loads = ok, reason, [], 0

    def check(self):
        return self.ok, self.reason

    def _load(self):
        self.loads += 1

    def _synth(self, text, params=None):
        self.calls.append(text)
        return ts.pcm16_to_wav(b"\x00\x00" * 22050, 22050)   # 1 s Stille


def _service(**kw):
    eng = FakeEngine(**kw)
    off = FakeEngine(ok=False, reason="abgeschaltet (TTS_CHATTERBOX_ENABLED)")
    off.key, off.label = "chatterbox", "Chatterbox (CPU, Test)"
    return ts.TTSService(engines={"piper": eng, "chatterbox": off}, cache_size=2), eng


class TestService:
    def test_synthese_cache_und_einmal_laden(self):
        svc, eng = _service()
        wav, dt, cached = svc.synthesize("Hallo du.", "piper")
        assert not cached and ts.wav_duration_s(wav) == 1.0 and eng.loads == 1
        _, dt2, cached2 = svc.synthesize("Hallo du.", "piper")
        assert cached2 and dt2 == 0.0 and eng.calls == ["Hallo du."]
        svc.synthesize("Zwei.", "piper")
        svc.synthesize("Drei.", "piper")          # Cache-Groesse 2 -> "Hallo du." faellt raus
        svc.synthesize("Hallo du.", "piper")
        assert eng.calls.count("Hallo du.") == 2 and eng.loads == 1

    def test_validierung(self):
        svc, _ = _service()
        with pytest.raises(ValueError):
            svc.synthesize("  ", "piper")
        with pytest.raises(ValueError):
            svc.synthesize("x" * 601, "piper")
        with pytest.raises(ValueError):
            svc.synthesize("Hallo.", "espeak")
        with pytest.raises(LookupError):
            svc.synthesize("Hallo.", "chatterbox")

    def test_engine_liste(self):
        svc, _ = _service()
        lst = {e["key"]: e for e in svc.list_engines()}
        assert lst["piper"]["available"] is True
        assert lst["chatterbox"]["available"] is False and "TTS_CHATTERBOX" in lst["chatterbox"]["reason"]

    def test_echte_engines_ohne_pakete_nicht_verfuegbar(self, monkeypatch):
        monkeypatch.delenv("TTS_CHATTERBOX_ENABLED", raising=False)
        ok, reason = ts.ChatterboxEngine().check()
        assert ok is False and "TTS_CHATTERBOX_ENABLED" in reason
        monkeypatch.setenv("TTS_PIPER_VOICE", "/gibt/es/nicht.onnx")
        ok, reason = ts.PiperEngine().check()
        assert ok is False


@pytest.fixture()
def tts_http():
    svc, eng = _service()
    srv = ts.ThreadingHTTPServer(("127.0.0.1", 0), ts.make_handler(svc))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", eng
    finally:
        srv.shutdown()
        srv.server_close()


def _post(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class TestHttp:
    def test_engines_und_synthese(self, tts_http):
        base, _ = tts_http
        with urllib.request.urlopen(base + "/engines", timeout=5) as r:
            assert {e["key"] for e in json.loads(r.read())["engines"]} == {"piper", "chatterbox"}
        code, headers, body = _post(base + "/synthesize", {"text": "Hallo.", "engine": "piper"})
        assert code == 200 and headers["Content-Type"] == "audio/wav" and body[:4] == b"RIFF"
        assert float(headers["X-TTS-Audio-S"]) == 1.0 and headers["X-TTS-Cached"] == "0"

    def test_fehler(self, tts_http):
        base, _ = tts_http
        assert _post(base + "/synthesize", {"text": "", "engine": "piper"})[0] == 422
        assert _post(base + "/synthesize", {"text": "Hallo.", "engine": "chatterbox"})[0] == 503


# ── Backend-Proxy ────────────────────────────────────────────────────────────

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.core.auth import get_current_user
    from app.main import app
    app.dependency_overrides[get_current_user] = lambda: "test-user"
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)


class TestProxy:
    def test_aus_nur_browser_verfuegbar(self, client, monkeypatch):
        monkeypatch.setattr(settings, "TTS_ENABLED", False)
        eng = {e["key"]: e for e in client.get("/api/interview/tts/engines").json()["engines"]}
        assert eng["browser"]["available"] is True
        assert eng["piper"]["available"] is False and eng["chatterbox"]["available"] is False
        r = client.post("/api/interview/tts", json={"text": "Hallo.", "engine": "piper"})
        assert r.status_code == 503

    def test_an_mit_dienst(self, client, monkeypatch, tts_http):
        base, eng = tts_http
        monkeypatch.setattr(settings, "TTS_ENABLED", True)
        monkeypatch.setattr(settings, "TTS_SERVICE_URL", base)
        events = []
        monkeypatch.setattr("app.services.job_queue.log_perf_event", lambda kind, f: events.append((kind, f)))
        lst = {e["key"]: e for e in client.get("/api/interview/tts/engines").json()["engines"]}
        assert lst["piper"]["available"] is True and lst["chatterbox"]["available"] is False
        r = client.post("/api/interview/tts", json={"text": "Wie ging es ihr?", "engine": "piper", "session_id": "t1"})
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
        kind, f = events[-1]
        assert kind == "interview_tts" and f["engine"] == "piper" and f["chars"] == 16 and f["audio_s"] == 1.0
        assert "text" not in f and "Wie ging" not in json.dumps(f)       # kein Text im Log
        assert client.post("/api/interview/tts", json={"text": "x", "engine": "chatterbox"}).status_code == 503
        assert client.post("/api/interview/tts", json={"text": "x", "engine": "espeak"}).status_code == 422

    def test_dienst_nicht_erreichbar(self, client, monkeypatch):
        monkeypatch.setattr(settings, "TTS_ENABLED", True)
        monkeypatch.setattr(settings, "TTS_SERVICE_URL", "http://127.0.0.1:9")
        lst = {e["key"]: e for e in client.get("/api/interview/tts/engines").json()["engines"]}
        assert lst["piper"]["available"] is False and "nicht erreichbar" in lst["piper"]["reason"]
        assert client.post("/api/interview/tts", json={"text": "Hallo.", "engine": "piper"}).status_code == 503


class TestSkripte:
    def test_setup_und_start(self):
        assert subprocess.run(["bash", "-n", str(BACKEND / "scripts" / "setup_tts.sh")]).returncode == 0
        start = (BACKEND / "runpod-start.sh").read_text()
        assert "tts_service/tts_server.py" in start and "venv-tts" in start
        # v19.37.2: Dienst startet VOR dem Backend
        assert start.index("tts_service/tts_server.py") < start.index("# 7. Backend starten")

    def test_proxy_wird_ignoriert(self):
        src = (BACKEND / "app" / "api" / "interview.py").read_text()
        tts = src[src.index("# ── v19.35: Server-Vorlesen"):]
        assert tts.count("trust_env=False") == 2


class TestReport:
    def test_tts_stats(self):
        spec = importlib.util.spec_from_file_location("perf_report", BACKEND / "scripts" / "perf_report.py")
        rep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rep)
        rows = [{"kind": "interview_tts", "engine": "piper", "synth_s": 0.4, "audio_s": 2.0, "cached": False},
                {"kind": "interview_tts", "engine": "piper", "synth_s": 0.0, "audio_s": 2.0, "cached": True},
                {"kind": "interview_tts", "engine": "chatterbox", "synth_s": 6.0, "audio_s": 3.0, "cached": False}]
        t = rep.compute_interview_stats(rows)["tts"]
        assert t["piper"]["saetze"] == 1 and t["piper"]["cache_treffer"] == 1 and t["piper"]["rtf_median"] == 0.2
        assert t["chatterbox"]["rtf_median"] == 2.0
