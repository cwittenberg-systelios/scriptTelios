"""v19.34 - Vorrang laufender Interviews vor neuen Jobs (eine GPU):
Reservierung, Ablauf, Wartegrenze, Job-Gate, Endpoints."""
from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.services import interview_lease as il


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    il.reset()
    monkeypatch.setattr(settings, "GPU_PROFILE", "single")
    monkeypatch.setattr(settings, "INTERVIEW_PRIORITY", True)
    monkeypatch.setattr(settings, "INTERVIEW_LEASE_IDLE_S", 300)
    monkeypatch.setattr(settings, "INTERVIEW_MAX_JOB_WAIT_S", 600)
    yield
    il.reset()


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TestReservierung:
    def test_touch_release(self):
        assert not il.is_active()
        assert il.touch("s1", "u") is True
        assert il.is_active() and il.active_count() == 1
        il.touch("s2", "v")
        assert il.active_count() == 2
        assert il.release("s1") is True and il.release("s1") is False
        assert il.active_count() == 1

    def test_ohne_session_oder_abgeschaltet(self, monkeypatch):
        assert il.touch(None) is False and il.touch("") is False
        monkeypatch.setattr(settings, "GPU_PROFILE", "dual")
        assert il.touch("s1") is False and not il.is_active()
        monkeypatch.setattr(settings, "GPU_PROFILE", "single")
        monkeypatch.setattr(settings, "INTERVIEW_PRIORITY", False)
        assert il.touch("s1") is False

    def test_ablauf_nach_inaktivitaet_und_verlaengerung(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(il.time, "monotonic", clock)
        il.touch("s1")
        clock.t += 299
        assert il.is_active()
        il.touch("s1")                 # Aktivitaet verlaengert
        clock.t += 299
        assert il.is_active()
        clock.t += 2
        assert not il.is_active()      # 5 min ohne Aktivitaet -> weg


class TestWarten:
    async def _run(self, clock, **kw):
        async def fake_sleep(s):
            clock.t += s
            hook = kw.pop("_hook", None)
            if hook:
                hook(clock.t)
        return await il.wait_until_free(sleep=fake_sleep, clock=clock, poll_s=5, **kw)

    async def test_frei_sofort(self):
        assert await il.wait_until_free() == 0.0

    async def test_hoechstens_max_wait(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(il.time, "monotonic", lambda: 0.0)   # Reservierung verfaellt nicht
        il.touch("s1")
        rest = []
        waited = await self._run(clock, on_wait=rest.append)
        assert waited == 600
        assert rest[0] == 10 and rest[-1] == 1

    async def test_endet_bei_freigabe(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(il.time, "monotonic", lambda: 0.0)
        il.touch("s1")

        async def fake_sleep(s):
            clock.t += s
            if clock.t - 1000 >= 30:
                il.release("s1")
        waited = await il.wait_until_free(sleep=fake_sleep, clock=clock, poll_s=5)
        assert waited == 30

    async def test_abbruch_des_jobs(self, monkeypatch):
        monkeypatch.setattr(il.time, "monotonic", lambda: 0.0)
        il.touch("s1")
        clock = _Clock()

        async def fake_sleep(s):
            clock.t += s
        waited = await il.wait_until_free(is_cancelled=lambda: clock.t > 1010, sleep=fake_sleep, clock=clock)
        assert waited == 15


class TestJobGate:
    async def test_job_wartet_bei_aktivem_interview(self, monkeypatch):
        from app.services.job_queue import JobQueue, JobStatus
        il.touch("s1")
        seen = {}

        async def fake_wait(**kw):
            kw["on_wait"](7)
            seen["detail"] = job.progress_detail
            seen["waiting"] = job.interview_waiting
            return 42.0
        monkeypatch.setattr(il, "wait_until_free", fake_wait)
        q = JobQueue()
        job = q.create_job("dokumentation", "Test")

        async def _ok():
            return {"text": "Ergebnis", "model_used": "ollama/test"}
        await q.run_job(job, _ok())
        assert job.status == JobStatus.DONE and job.result_text == "Ergebnis"
        assert job.interview_wait_s == 42.0 and job.interview_waiting is False
        assert seen["waiting"] is True and "höchstens noch 7 Min" in seen["detail"]

    async def test_ohne_interview_kein_warten(self, monkeypatch):
        from app.services.job_queue import JobQueue

        async def boom(**kw):
            raise AssertionError("darf nicht warten")
        monkeypatch.setattr(il, "wait_until_free", boom)
        q = JobQueue()
        job = q.create_job("dokumentation", "Test")

        async def _ok():
            return {"text": "x"}
        await q.run_job(job, _ok())
        assert job.interview_wait_s is None

    def test_wartender_job_zaehlt_nicht_als_laufend(self, monkeypatch):
        from app.services import job_queue as jq
        job = jq.job_queue.create_job("dokumentation", "Test")
        job.status = jq.JobStatus.RUNNING.value
        try:
            n = jq.running_job_count()
            job.interview_waiting = True
            assert jq.running_job_count() == n - 1
        finally:
            job.status = jq.JobStatus.DONE.value


# ── Endpoints ────────────────────────────────────────────────────────────────

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


def _events(text):
    return [json.loads(line[6:]) for line in text.split("\n") if line.startswith("data: ")]


def _mock_chat(monkeypatch, data):
    async def fake(system, messages, **kw):
        yield ("delta", data["sage"])
        yield ("done", {"text": json.dumps(data), "structured_data": data, "structured_parse_error": False,
                        "model_used": "gemma4:31b", "token_count": 5, "duration_s": 0.1, "sage": data["sage"], "perf": {}})
    monkeypatch.setattr("app.services.llm_chat.generate_chat_stream", fake)

    async def fake_model(requested, wf):
        return "gemma4:31b"
    monkeypatch.setattr("app.services.llm.ensure_generation_model", fake_model)


class TestEndpoints:
    def test_lease_touch_release(self, client):
        r = client.post("/api/interview/lease", json={"session_id": "s9", "action": "touch"})
        assert r.status_code == 200 and r.json()["active"] == 1 and r.json()["enabled"] is True
        r = client.post("/api/interview/lease", json={"session_id": "s9", "action": "release"})
        assert r.json()["active"] == 0
        assert client.post("/api/interview/lease", json={"session_id": "s9", "action": "boom"}).status_code == 422

    def test_chat_reserviert_meldet_status_und_gibt_bei_fertig_frei(self, client, monkeypatch):
        _mock_chat(monkeypatch, {"sage": "Worum ging es?", "abgedeckt": [], "unklar": [], "thema": "anliegen", "fertig": False})
        r = client.post("/api/interview/chat/stream", json={"set": "kunst", "session_id": "c1", "historie": []})
        ev = _events(r.text)
        assert ev[0]["type"] == "status" and "jobs_running" in ev[0]
        assert il.is_active()
        # Abschluss: alle Pflichtpunkte abgedeckt -> fertig -> Freigabe
        _mock_chat(monkeypatch, {"sage": "Danke, das habe ich.", "abgedeckt": ["klient", "selbstgefaehrdung"],
                                 "unklar": [], "thema": "", "fertig": True})
        r = client.post("/api/interview/chat/stream", json={
            "set": "kunst", "session_id": "c1",
            "checkliste": {"klient": "abgedeckt", "selbstgefaehrdung": "abgedeckt"},
            "klient": {"anrede": "Frau", "initial": "L.", "gender": "w"},
            "historie": [{"rolle": "system", "text": "Noch etwas?"}, {"rolle": "behandler", "text": "Um Frau L., keine Hinweise auf Selbstgefährdung. Bitte abschließen."}],
        })
        meta = [e for e in _events(r.text) if e["type"] == "meta"][0]
        assert meta["fertig"] is True
        assert not il.is_active()

    def test_diktat_mit_session_verlaengert(self, client, monkeypatch):
        from app.services import transcription as tr

        async def fake(path):
            return {"transcript": "ok", "duration_seconds": 1.0, "word_count": 1, "perf": {}}
        monkeypatch.setattr(tr, "transcribe_dictation", fake)
        r = client.post("/api/interview/transcribe", data={"session_id": "d1"},
                        files={"audio": ("a.webm", b"xyz", "audio/webm")})
        assert r.status_code == 200 and il.is_active()
