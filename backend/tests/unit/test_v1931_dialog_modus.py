"""v19.31 - Dialog-Modus: Streaming-Chat, SageExtractor, Leitplanken,
Gespraech als Quelle, SSE-Endpoint."""
from __future__ import annotations

import json

import pytest

from app.core.interview_sets import KLIENT_KEY, SELBSTGEFAEHRDUNG_KEY, get_set
from app.services.interview_chat import (
    STATUS_ABGEDECKT, STATUS_UNKLAR, TURN_SCHEMA, ChatConfig, ChatState, Turn,
    apply_turn, build_system_prompt, pflicht_erfuellt, plan_turn,
)
from app.services.interview_protokoll import (
    InterviewProtokollError, gespraech_plaintext, parse_gespraech, render_gespraech,
)
from app.services.llm_chat import SageExtractor, generate_chat_stream


# ── SageExtractor ─────────────────────────────────────────────────────────────

class TestSageExtractor:
    def _run(self, chunks):
        ex = SageExtractor()
        out = "".join(ex.feed(c) for c in chunks)
        return out, ex

    def test_einfach(self):
        out, ex = self._run(['{"sage": "Hallo du.", "abgedeckt": []}'])
        assert out == "Hallo du." and ex.done

    def test_chunkgrenzen_und_escapes(self):
        chunks = ['{"sa', 'ge":"Wie ', 'ging es Herrn \\"M', '.\\"? \\u00c4h\\nja"', ', "fertig": false}']
        out, ex = self._run(chunks)
        assert out == 'Wie ging es Herrn "M."? Äh\nja' and ex.done

    def test_anderes_feld_zuerst_ignoriert(self):
        out, _ = self._run(['{"abgedeckt": ["klient"], "sage": "Danke."}'])
        assert out == "Danke."

    def test_nicht_json_liefert_nichts(self):
        out, ex = self._run(["Hallo ohne JSON"])
        assert out == "" and not ex.done


# ── generate_chat_stream (gemocktes Ollama) ───────────────────────────────────

class _FakeResp:
    def __init__(self, lines, status=200):
        self._lines = lines
        self.status_code = status

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b'{"error":"boom"}'

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, lines, status=200):
        self.lines = lines
        self.status = status
        self.payload = None

    def stream(self, method, url, json=None):
        self.payload = json
        return _FakeResp(self.lines, self.status)


@pytest.fixture()
def fake_client(monkeypatch):
    holder = {}

    def _install(lines, status=200):
        c = _FakeClient(lines, status)
        monkeypatch.setattr("app.services.llm_chat._get_ollama_client", lambda: c)
        holder["c"] = c
        return c
    return _install


class TestChatStream:
    async def test_streamt_sage_und_parst_json(self, fake_client):
        pieces = ['{"sage": "Wor', 'um ging es?", "abgedeckt": ["klient"], "unklar": [],', ' "thema": "anliegen", "fertig": false}']
        lines = [json.dumps({"message": {"content": p}, "done": False}) for p in pieces]
        lines.append(json.dumps({"message": {"content": ""}, "done": True, "eval_count": 42}))
        c = fake_client(lines)
        events = [e async for e in generate_chat_stream("SYS", [{"role": "user", "content": "hi"}],
                                                        model="gemma4:31b", response_format=TURN_SCHEMA)]
        deltas = "".join(p for k, p in events if k == "delta")
        assert deltas == "Worum ging es?"
        done = [p for k, p in events if k == "done"][0]
        assert done["structured_data"]["thema"] == "anliegen"
        assert done["sage"] == "Worum ging es?" and done["token_count"] == 42
        assert c.payload["stream"] is True and c.payload["format"] == TURN_SCHEMA
        assert c.payload["messages"][0] == {"role": "system", "content": "SYS"}

    async def test_ohne_schema_rohtext(self, fake_client):
        fake_client([json.dumps({"message": {"content": "Hal"}, "done": False}),
                     json.dumps({"message": {"content": "lo"}, "done": True})])
        events = [e async for e in generate_chat_stream("S", [{"role": "user", "content": "x"}])]
        assert "".join(p for k, p in events if k == "delta") == "Hallo"

    async def test_http_fehler(self, fake_client):
        fake_client([], status=500)
        events = [e async for e in generate_chat_stream("S", [{"role": "user", "content": "x"}])]
        assert events[-1][0] == "error"

    async def test_ollama_error_zeile(self, fake_client):
        fake_client([json.dumps({"error": "model not found"})])
        events = [e async for e in generate_chat_stream("S", [{"role": "user", "content": "x"}])]
        assert events[-1] == ("error", "Ollama: model not found")


# ── Leitplanken ───────────────────────────────────────────────────────────────

def _state(*turns, checkliste=None, **kw):
    return ChatState(set_key="kunst", historie=[Turn(r, t) for r, t in turns],
                     checkliste=dict(checkliste or {}), **kw)


class TestPlanTurn:
    def test_erster_turn_ohne_regie_prompt_enthaelt_auftrag_und_liste(self):
        st = _state()
        p = plan_turn(st)
        assert p.regie is None
        assert "AUFTRAG DES BEHANDLERS" in p.system_prompt and "(klient) [offen] [PFLICHT]" in p.system_prompt
        assert p.messages[-1]["role"] == "user"

    def test_klient_wird_aus_historie_erkannt(self):
        st = _state(("system", "Um wen geht es?"), ("behandler", "Um Herrn Müller."))
        p = plan_turn(st)
        assert st.klient == {"anrede": "Herr", "initial": "M.", "gender": "m"}
        assert st.checkliste[KLIENT_KEY] == STATUS_ABGEDECKT
        assert "KLIENT/IN: Herr M." in p.system_prompt

    def test_trigger_regie(self):
        st = _state(("system", "Wie war es?"), ("behandler", "Er hat lebensmüde Gedanken geäußert."),
                    klient={"anrede": "Herr", "initial": "M.", "gender": "m"})
        p = plan_turn(st)
        assert p.regie_typ == "trigger" and "Herr M." in p.regie and "absprachefähig" in p.regie
        assert st.trigger_stufe == 1

    def test_abschluss_nur_mit_pflicht(self):
        alle = {f.key: STATUS_ABGEDECKT for f in get_set("kunst").fragen}
        st = _state(("system", "?"), ("behandler", "Um Frau K. Alles gut."), checkliste=alle)
        p = plan_turn(st)
        assert p.regie_typ == "pflicht" and SELBSTGEFAEHRDUNG_KEY in p.regie
        st2 = _state(("system", "?"), ("behandler", "Um Frau K. Keine Hinweise auf Selbstgefährdung."), checkliste=alle)
        assert plan_turn(st2).regie_typ == "abschluss"

    def test_budget_regie(self):
        st = _state(("system", "?"), ("behandler", "Um Frau K."),
                    rueckfragen_je_thema={"beobachtung": 4})
        p = plan_turn(st, ChatConfig(max_rueckfragen_thema=4))
        assert p.regie_typ == "budget" and "beobachtung" in p.regie
        st2 = _state(("system", "?"), ("behandler", "Um Frau K."), rueckfragen_je_thema={"a": 24})
        assert plan_turn(st2, ChatConfig(max_rueckfragen_gesamt=24)).regie_typ == "budget"

    def test_verdichtung_ab_20_turns(self):
        turns = [("system" if i % 2 == 0 else "behandler", f"Turn {i}") for i in range(30)]
        p = plan_turn(_state(*turns), ChatConfig(verdichten_ab_turns=20))
        assert "BISHERIGER GESPRAECHSVERLAUF" in p.messages[0]["content"]
        assert len(p.messages) == 2 + 12

    def test_max_turns_erzwingt_abschluss(self):
        turns = [("system" if i % 2 == 0 else "behandler", "x") for i in range(60)]
        assert plan_turn(_state(*turns), ChatConfig(max_turns=60)).regie_typ == "abschluss"


class TestApplyTurn:
    def test_checkliste_monoton_und_thema(self):
        st = _state(("system", "Um wen?"), ("behandler", "Frau K."))
        p = plan_turn(st)
        meta = apply_turn(st, {"sage": "Worum ging es?", "abgedeckt": ["klient", "unbekannt"], "unklar": ["anliegen"],
                               "thema": "anliegen", "fertig": False}, "Worum ging es?", p)
        assert st.checkliste[KLIENT_KEY] == STATUS_ABGEDECKT and st.checkliste["anliegen"] == STATUS_UNKLAR
        assert "unbekannt" not in st.checkliste and meta["thema"] == "anliegen"
        assert st.historie[-1].rolle == "system" and st.historie[-1].thema == "anliegen"
        # unklar darf abgedeckt nicht zuruecksetzen
        p2 = plan_turn(st)
        apply_turn(st, {"sage": "x", "abgedeckt": [], "unklar": ["klient"], "thema": "", "fertig": False}, "x", p2)
        assert st.checkliste[KLIENT_KEY] == STATUS_ABGEDECKT

    def test_fertig_verweigert_ohne_pflicht(self):
        st = _state(("system", "?"), ("behandler", "Um Frau K. Thema Scham."))
        p = plan_turn(st)
        meta = apply_turn(st, {"sage": "Danke, das war's.", "abgedeckt": [], "unklar": [], "thema": "", "fertig": True},
                          "Danke, das war's.", p)
        assert meta["fertig"] is False and meta["fertig_verweigert"] is True
        assert meta["pflicht"][SELBSTGEFAEHRDUNG_KEY] is False

    def test_fertig_akzeptiert_mit_pflicht(self):
        st = _state(("system", "?"), ("behandler", "Um Frau K. Keine Hinweise auf Selbstgefährdung."))
        p = plan_turn(st)
        meta = apply_turn(st, {"sage": "Danke.", "abgedeckt": [], "unklar": [], "thema": "", "fertig": True}, "Danke.", p)
        assert meta["fertig"] is True and st.fertig

    def test_budget_zaehlt_nachfragen_zum_selben_thema(self):
        st = _state(("system", "Welche Methode?"), ("behandler", "Malen."))
        st.historie[0].thema = "methode"
        p = plan_turn(st)
        apply_turn(st, {"sage": "Und mit welchem Material?", "abgedeckt": [], "unklar": ["methode"], "thema": "methode", "fertig": False},
                   "Und mit welchem Material?", p)
        assert st.rueckfragen_je_thema == {"methode": 1}

    def test_redundanz_erkannt(self):
        st = _state(("system", "Welche Methode oder welches Material habt ihr in der Stunde verwendet?"), ("behandler", "Malen."))
        p = plan_turn(st)
        meta = apply_turn(st, {"sage": "Welche Methode oder welches Material habt ihr in der Stunde verwendet?", "abgedeckt": [], "unklar": [], "thema": "methode", "fertig": False},
                          "Welche Methode oder welches Material habt ihr in der Stunde verwendet?", p)
        assert meta["redundant"] is True

    def test_pflicht_erfuellt_marker(self):
        st = _state(("behandler", "Um Frau K."))
        assert pflicht_erfuellt(st) == {KLIENT_KEY: True, SELBSTGEFAEHRDUNG_KEY: False}
        st2 = _state(("behandler", "Keine Krise, nichts."))
        assert pflicht_erfuellt(st2)[SELBSTGEFAEHRDUNG_KEY] is True

    def test_system_prompt_regeln(self):
        s = build_system_prompt(_state(), ChatConfig(), "Tu dies.")
        assert "duzt" in s and "GENAU EINE Frage" in s and "REGIE-ANWEISUNG FUER DIESEN TURN: Tu dies." in s


# ── Gespraech als Quelle ──────────────────────────────────────────────────────

def _gespraech(**over):
    g = {"set": "kunst", "set_label": "Kunsttherapie", "session_id": "s1",
         "historie": [
             {"rolle": "system", "text": "Um wen geht es?"},
             {"rolle": "behandler", "text": "Um Frau Kaiser, Anna."},
             {"rolle": "system", "text": "Worum ging es?"},
             {"rolle": "behandler", "text": "Scham nach dem Streit. Keine Hinweise auf Selbstgefährdung."},
         ]}
    g.update(over)
    return g


class TestGespraech:
    def test_parse_und_render(self):
        g = parse_gespraech(json.dumps(_gespraech()))
        text = render_gespraech(g)
        assert text.startswith("Verfahren / Fragen-Set: Kunsttherapie")
        assert "[Interviewer]: Um wen geht es?" in text and "[Behandler]: Um Frau Kaiser, Anna." in text
        plain = gespraech_plaintext(g)
        assert "Um wen geht es?" not in plain and "Scham nach dem Streit" in plain
        assert g.klient_info() == {"anrede": "Frau", "initial": "K.", "gender": "w"}

    def test_klient_aus_feld_gewinnt(self):
        g = parse_gespraech(json.dumps(_gespraech(klient={"anrede": "Herr", "initial": "Z."})))
        assert g.klient_info()["initial"] == "Z." and g.klient_info()["gender"] == "m"

    def test_ohne_behandler_text_422(self):
        with pytest.raises(InterviewProtokollError, match="keine Antworten"):
            parse_gespraech(json.dumps(_gespraech(historie=[{"rolle": "system", "text": "Hi"}])))

    def test_ohne_suizid_aussage_422(self):
        h = [{"rolle": "behandler", "text": "Um Frau K. Thema Scham."}]
        with pytest.raises(InterviewProtokollError, match="Selbstgefährdung"):
            parse_gespraech(json.dumps(_gespraech(historie=h)))

    def test_none(self):
        assert parse_gespraech(None) is None and parse_gespraech("  ") is None

    def test_prompt_regel_fuer_gespraech(self):
        from app.services.prompts import INTERVIEW_MODUS_REGELN
        assert "Fragen des Interviewers sind KEINE Inhalte" in INTERVIEW_MODUS_REGELN


# ── Endpoint ──────────────────────────────────────────────────────────────────

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


class TestChatEndpoint:
    def _mock_stream(self, monkeypatch, pieces, data):
        async def fake(system, messages, **kw):
            for p in pieces:
                yield ("delta", p)
            yield ("done", {"text": json.dumps(data), "structured_data": data, "structured_parse_error": False,
                            "model_used": "m", "token_count": 5, "duration_s": 0.1, "sage": "".join(pieces)})
        monkeypatch.setattr("app.services.llm_chat.generate_chat_stream", fake)

        async def fake_model(requested, wf):
            return "gemma4:31b"
        monkeypatch.setattr("app.services.llm.ensure_generation_model", fake_model)

    def test_turn_stream(self, client, monkeypatch):
        self._mock_stream(monkeypatch, ["Worum ", "ging es?"],
                          {"sage": "Worum ging es?", "abgedeckt": ["klient"], "unklar": [], "thema": "anliegen", "fertig": False})
        r = client.post("/api/interview/chat/stream", json={
            "set": "kunst", "session_id": "abc123",
            "historie": [{"rolle": "system", "text": "Um wen geht es?"}, {"rolle": "behandler", "text": "Um Herrn Müller."}],
        })
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        ev = _events(r.text)
        assert [e["type"] for e in ev] == ["delta", "delta", "meta", "done"]
        meta = ev[2]
        assert meta["sage"] == "Worum ging es?" and meta["checkliste"]["klient"] == "abgedeckt"
        assert meta["klient"]["initial"] == "M." and meta["fertig"] is False and meta["regie"] is None

    def test_turn_trigger_regie_und_fertig_verweigert(self, client, monkeypatch):
        self._mock_stream(monkeypatch, ["Gab es Pläne?"],
                          {"sage": "Gab es Pläne?", "abgedeckt": [], "unklar": [], "thema": "beobachtung", "fertig": True})
        r = client.post("/api/interview/chat/stream", json={
            "set": "kunst", "klient": {"anrede": "Frau", "initial": "K.", "gender": "w"},
            "historie": [{"rolle": "system", "text": "?"}, {"rolle": "behandler", "text": "Sie hat Suizidgedanken geäußert."}],
        })
        meta = [e for e in _events(r.text) if e["type"] == "meta"][0]
        assert meta["regie_typ"] == "trigger" and "Frau K." in meta["regie"]
        assert meta["fertig"] is False and meta["fertig_verweigert"] is True and meta["trigger_stufe"] == 1

    def test_unbekanntes_set_422(self, client):
        r = client.post("/api/interview/chat/stream", json={"set": "nope", "historie": []})
        assert r.status_code == 422

    def test_stream_error_event(self, client, monkeypatch):
        async def fake(system, messages, **kw):
            yield ("error", "Ollama down")
        monkeypatch.setattr("app.services.llm_chat.generate_chat_stream", fake)

        async def fake_model(requested, wf):
            return "gemma4:31b"
        monkeypatch.setattr("app.services.llm.ensure_generation_model", fake_model)
        r = client.post("/api/interview/chat/stream", json={"set": "kunst", "historie": []})
        ev = _events(r.text)
        assert ev[-1]["type"] == "error" and "Ollama down" in ev[-1]["error_msg"]

    def test_generate_mit_gespraech(self, client, monkeypatch):
        import app.api.jobs as jobs_api

        async def fake_model(requested, wf):
            return "gemma4:31b"
        monkeypatch.setattr("app.services.llm.ensure_generation_model", fake_model)
        created = {}

        class _Job:
            job_id = "j"
        monkeypatch.setattr(jobs_api.job_queue, "create_job", lambda **kw: created.update(kw) or _Job())
        monkeypatch.setattr(jobs_api.job_queue, "run_job", lambda *a, **k: None)
        r = client.post("/api/jobs/generate", data={
            "workflow": "dokumentation", "workflow_instructions": "Doku.",
            "interview_gespraech": json.dumps(_gespraech()),
        })
        assert r.status_code == 200, r.text
        assert created["patient_kuerzel"] == "Frau K." and "Gespräch: Kunsttherapie" in created["description"]

    def test_generate_beide_quellen_422(self, client, monkeypatch):
        async def fake_model(requested, wf):
            return "gemma4:31b"
        monkeypatch.setattr("app.services.llm.ensure_generation_model", fake_model)
        s = get_set("kunst")
        prot = {"set": "kunst", "eintraege": [{"key": f.key, "frage": f.text, "antwort": "Nein, keine." if f.key == SELBSTGEFAEHRDUNG_KEY else "x"} for f in s.fragen]}
        r = client.post("/api/jobs/generate", data={
            "workflow": "dokumentation", "workflow_instructions": "Doku.",
            "interview_gespraech": json.dumps(_gespraech()), "interview_protokoll": json.dumps(prot),
        })
        assert r.status_code == 422 and "nur eine" in r.json()["detail"]
