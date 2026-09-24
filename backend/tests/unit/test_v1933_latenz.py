"""v19.33 - Dialog-Latenz: Messung (perf), feste Kontextgroesse, Warmups
ohne Verdraengung, Interview-Warmup, GPU-Profil, perf_report-Abschnitt."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import llm
from app.services.llm_chat import generate_chat_stream

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture()
def fixed_ctx(monkeypatch):
    def _set(on: bool, cap: int = 32768):
        monkeypatch.setattr(settings, "LLM_FIXED_CTX", on)
        monkeypatch.setattr(settings, "LLM_NUM_CTX_CAP", cap)
    return _set


# ── S2: feste Kontextgroesse ─────────────────────────────────────────────────

class TestFesteKontextgroesse:
    def test_aus_ist_dynamisch(self, fixed_ctx):
        fixed_ctx(False, 32768)
        assert llm.fixed_num_ctx() is None
        assert llm._estimate_num_ctx("s", "kurz", 100) == 2048

    def test_an_liefert_cap_fuer_jede_laenge(self, fixed_ctx):
        fixed_ctx(True, 32768)
        assert llm.fixed_num_ctx() == 32768
        assert llm._estimate_num_ctx("s", "kurz", 100) == 32768
        assert llm._estimate_num_ctx("s", "x" * 50000, 2000) == 32768

    def test_warm_payload(self, fixed_ctx):
        fixed_ctx(True, 16384)
        p = llm.warm_payload("gemma4:31b")
        assert p["options"] == {"num_ctx": 16384} and p["prompt"] == "" and p["keep_alive"] == -1
        fixed_ctx(False)
        assert "options" not in llm.warm_payload("gemma4:31b")


# ── S1: perf im Chat-Stream ──────────────────────────────────────────────────

class _Resp:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Client:
    def __init__(self, lines):
        self.lines, self.payload = lines, None

    def stream(self, method, url, json=None):
        self.payload = json
        return _Resp(self.lines)


def _lines():
    return [json.dumps({"message": {"content": "Hallo"}, "done": False}),
            json.dumps({"message": {"content": ""}, "done": True, "eval_count": 7,
                        "load_duration": 12_500_000_000, "prompt_eval_count": 900,
                        "prompt_eval_duration": 800_000_000, "eval_duration": 1_400_000_000})]


class TestChatPerf:
    async def test_perf_felder_und_fester_ctx(self, monkeypatch, fixed_ctx):
        fixed_ctx(True, 32768)
        c = _Client(_lines())
        monkeypatch.setattr("app.services.llm_chat._get_ollama_client", lambda: c)
        ev = [e async for e in generate_chat_stream("S", [{"role": "user", "content": "x"}], model="gemma4:31b")]
        done = [p for k, p in ev if k == "done"][0]
        perf = done["perf"]
        assert c.payload["options"]["num_ctx"] == 32768 and perf["num_ctx"] == 32768
        assert perf["load_s"] == 12.5 and perf["prompt_s"] == 0.8 and perf["gen_s"] == 1.4
        assert perf["prompt_tokens"] == 900 and perf["gen_tokens"] == 7
        assert perf["ttft_s"] is not None and perf["total_s"] >= perf["ttft_s"]

    async def test_dynamisch_ohne_fixed(self, monkeypatch, fixed_ctx):
        fixed_ctx(False, 32768)
        c = _Client(_lines())
        monkeypatch.setattr("app.services.llm_chat._get_ollama_client", lambda: c)
        [e async for e in generate_chat_stream("S", [{"role": "user", "content": "x"}], model="gemma4:31b")]
        assert c.payload["options"]["num_ctx"] < 32768


# ── S3: Warmups verdraengen nichts ───────────────────────────────────────────

class TestWarmTarget:
    async def test_geladenes_llm_hat_vorrang(self, monkeypatch):
        async def ps():
            return ["nomic-embed-text:latest", "gemma4:31b"]
        monkeypatch.setattr(llm, "ollama_loaded_models", ps)
        monkeypatch.setattr(settings, "OLLAMA_MODEL", "mistral-small3.2")
        assert await llm.warm_target_model(None) == "gemma4:31b"

    async def test_nichts_geladen_default(self, monkeypatch):
        async def ps():
            return []
        monkeypatch.setattr(llm, "ollama_loaded_models", ps)
        monkeypatch.setattr(settings, "OLLAMA_MODEL", "mistral-small3.2")
        assert await llm.warm_target_model(None) == "mistral-small3.2"

    async def test_explizites_modell(self):
        assert await llm.warm_target_model("ollama/gemma4:31b") == "gemma4:31b"

    async def test_transkriptions_warmup_nutzt_warm_model(self, monkeypatch):
        seen = {}

        async def fake(model=None):
            seen["model"] = model
            return {"ok": True}
        monkeypatch.setattr(llm, "warm_model", fake)
        from app.services import transcription
        await transcription._ollama_warmup()
        assert seen == {"model": None}   # -> warm_target_model: geladenes LLM

    async def test_warm_model_fehler_wirft_nicht(self, monkeypatch):
        class _C:
            async def post(self, *a, **k):
                raise RuntimeError("down")
        monkeypatch.setattr(llm, "_get_ollama_client", lambda: _C())
        r = await llm.warm_model("gemma4:31b")
        assert r == {"model": "gemma4:31b", "load_s": None, "ok": False}


# ── S5: GPU-Profil ───────────────────────────────────────────────────────────

class TestGpuProfil:
    def test_profil_property(self, monkeypatch):
        monkeypatch.setattr(settings, "GPU_PROFILE", "single")
        assert settings.gpu_dual is False
        monkeypatch.setattr(settings, "GPU_PROFILE", " Dual ")
        assert settings.gpu_dual is True

    def test_resident_models_ohne_duplikate(self, monkeypatch):
        monkeypatch.setattr(settings, "WORKFLOW_MODEL", {"dokumentation": "gemma4:31b", "anamnese": "mistral-small3.2",
                                                         "entlassbericht": "gemma4:31b"})
        monkeypatch.setattr(settings, "SUMMARY_MODEL", "mistral-small3.2")
        monkeypatch.setattr(settings, "OLLAMA_MODEL", "mistral-small3.2")
        assert llm.resident_models() == ["gemma4:31b", "mistral-small3.2"]

    def test_dual_behaelt_whisper(self, monkeypatch):
        from app.services import transcription as tr
        monkeypatch.setattr(settings, "GPU_PROFILE", "dual")
        tr._model_cache["x"] = object()
        try:
            tr._free_gpu_after_transcription()
            assert "x" in tr._model_cache
            monkeypatch.setattr(settings, "GPU_PROFILE", "single")
            tr._free_gpu_after_transcription()
            assert "x" not in tr._model_cache
        finally:
            tr._model_cache.pop("x", None)

    def test_runpod_start_syntax_und_schalter(self):
        sh = BACKEND / "runpod-start.sh"
        assert subprocess.run(["bash", "-n", str(sh)]).returncode == 0
        text = sh.read_text()
        assert "OLLAMA_MAX_LOADED_MODELS=3" in text and "OLLAMA_MAX_LOADED_MODELS=1" in text
        # Profil wird VOR dem Ollama-Block ermittelt (auch wenn Ollama schon laeuft)
        assert text.index("export GPU_PROFILE") < text.index('if pgrep -f "ollama serve"')


# ── S1/S4: Endpoints ─────────────────────────────────────────────────────────

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


@pytest.fixture()
def perf_events(monkeypatch):
    events = []
    monkeypatch.setattr("app.services.job_queue.log_perf_event", lambda kind, f: events.append((kind, f)))
    return events


def _events(text):
    return [json.loads(line[6:]) for line in text.split("\n") if line.startswith("data: ")]


class TestEndpoints:
    def test_transcribe_liefert_und_loggt_perf(self, client, monkeypatch, perf_events):
        from app.services import transcription as tr

        async def fake(path):
            return {"transcript": "Um Frau L.", "duration_seconds": 2.0, "word_count": 3,
                    "perf": {"audio_s": 2.0, "whisper_load_s": 41.0, "transcribe_s": 0.6}}
        monkeypatch.setattr(tr, "transcribe_dictation", fake)
        r = client.post("/api/interview/transcribe", files={"audio": ("a.webm", b"xyz", "audio/webm")})
        assert r.status_code == 200 and r.json()["perf"]["whisper_load_s"] == 41.0
        kind, f = perf_events[-1]
        assert kind == "interview_transcribe" and f["whisper_load_s"] == 41.0 and "jobs_running" in f

    def test_chat_meta_perf_und_client_perf(self, client, monkeypatch, perf_events):
        data = {"sage": "Worum ging es?", "abgedeckt": ["klient"], "unklar": [], "thema": "anliegen", "fertig": False}

        async def fake(system, messages, **kw):
            yield ("delta", "Worum ging es?")
            yield ("done", {"text": json.dumps(data), "structured_data": data, "structured_parse_error": False,
                            "model_used": "gemma4:31b", "token_count": 5, "duration_s": 0.1, "sage": "Worum ging es?",
                            "perf": {"ttft_s": 0.4, "load_s": 0.0, "num_ctx": 32768}})
        monkeypatch.setattr("app.services.llm_chat.generate_chat_stream", fake)

        async def fake_model(requested, wf):
            return "gemma4:31b"
        monkeypatch.setattr("app.services.llm.ensure_generation_model", fake_model)
        r = client.post("/api/interview/chat/stream", json={
            "set": "kunst", "session_id": "s1",
            "historie": [{"rolle": "system", "text": "Um wen geht es?"}, {"rolle": "behandler", "text": "Um Herrn M."}],
            "client_perf": {"transcribe_ms": 1800, "prev_ttft_ms": 900},
        })
        meta = [e for e in _events(r.text) if e["type"] == "meta"][0]
        assert meta["perf"]["ttft_s"] == 0.4
        kind, f = perf_events[-1]
        assert kind == "interview_chat" and f["turn"] == 2 and f["num_ctx"] == 32768
        assert f["client_transcribe_ms"] == 1800 and f["client_prev_ttft_ms"] == 900
        assert f["session"].startswith("interview-")

    def test_warmup_startet_beides_und_kehrt_sofort_zurueck(self, client, monkeypatch):
        called = []

        async def fake_model(requested, wf):
            return "gemma4:31b"

        async def fake_warm(model=None):
            called.append(("llm", model))
            return {"ok": True}

        async def fake_whisper():
            called.append(("whisper", None))
            return 0.0
        monkeypatch.setattr("app.services.llm.ensure_generation_model", fake_model)
        monkeypatch.setattr("app.services.llm.warm_model", fake_warm)
        monkeypatch.setattr("app.services.transcription.warm_whisper", fake_whisper)
        r = client.post("/api/interview/warmup")
        assert r.status_code == 200 and r.json() == {"started": True, "model": "gemma4:31b"}
        import time
        for _ in range(50):
            if len(called) == 2:
                break
            time.sleep(0.02)
        assert sorted(called) == [("llm", "gemma4:31b"), ("whisper", None)]


# ── perf_report: Interview-Abschnitt ─────────────────────────────────────────

def _load_report():
    spec = importlib.util.spec_from_file_location("perf_report", BACKEND / "scripts" / "perf_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPerfReport:
    def test_interview_stats(self):
        rep = _load_report()
        rows = [
            {"ts": "2026-09-24T06:06:00+00:00", "kind": "interview_chat", "session": "interview-a", "user": "u1",
             "ttft_s": 90.0, "total_s": 95.0, "load_s": 80.0, "jobs_running": 1},
            {"ts": "2026-09-24T06:07:00+00:00", "kind": "interview_chat", "session": "interview-a", "user": "u1",
             "ttft_s": 0.5, "total_s": 2.0, "load_s": 0.0, "jobs_running": 0},
            {"ts": "2026-09-30T08:00:00+00:00", "kind": "interview_chat", "session": "interview-b", "user": "u2",
             "ttft_s": 0.6, "total_s": 2.1, "load_s": 0.1, "jobs_running": 0},
            {"ts": "2026-09-24T06:05:00+00:00", "kind": "interview_transcribe", "transcribe_s": 1.0, "whisper_load_s": 40.0},
        ]
        iv = rep.compute_interview_stats(rows)
        assert iv["dialoge"] == 2 and iv["turns"] == 3 and iv["nutzer"] == 2
        assert iv["dialoge_pro_woche"] == {"2026-KW39": 1, "2026-KW40": 1}
        assert iv["reloads"] == 1 and iv["turns_mit_jobs"] == 1 and iv["whisper_loads"] == 1
        assert iv["ttft_s"]["median"] == 0.6

    def test_leer(self):
        assert _load_report().compute_interview_stats([]) == {}


class TestDiktatPerf:
    async def test_transcribe_dictation_liefert_perf(self, monkeypatch, tmp_path):
        import sys
        import types
        from app.services import transcription as tr
        monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=object))

        class _Seg:
            def __init__(self, t):
                self.text = t

        class _Info:
            language = "de"
        monkeypatch.setattr(tr, "_get_duration", lambda p: 3.0)
        monkeypatch.setattr(tr, "_get_model", lambda d, c: object())
        monkeypatch.setattr(tr, "_transcribe_audio_segment",
                            lambda m, p, timeout: ([_Seg("Um Frau L.")], _Info(), 5))
        f = tmp_path / "a.webm"
        f.write_bytes(b"x")
        r = await tr.transcribe_dictation(f)
        assert r["transcript"] == "Um Frau L."
        assert set(r["perf"]) == {"audio_s", "whisper_load_s", "transcribe_s"} and r["perf"]["audio_s"] == 3.0
