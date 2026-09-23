"""v19.24 - Interview-Dialog: Phrasen, Klient-Frage, Suizidalitaets-Trigger,
Abschluss-Check, Protokoll-Erweiterungen, Endpoints."""
from __future__ import annotations

import json

import pytest

from app.core.interview_phrasen import (
    QUITTUNGEN, QUITTUNGEN_ERNST, UEBERLEITUNGEN, quittung, ueberleitung,
)
from app.core.interview_sets import INTERVIEW_SETS, KLIENT_KEY, SELBSTGEFAEHRDUNG_KEY, get_set
from app.services.interview_abschluss import ABSCHLUSS_SCHEMA, pruefe_abschluss
from app.services.interview_dialog import KLIENT_RUECKFRAGE, TurnRequest, decide_turn
from app.services.interview_protokoll import (
    extract_klient, parse_protokoll, protokoll_plaintext, render_protokoll,
)
from app.services.interview_trigger import TRIGGER_SUIZIDALITAET, pruefe_trigger
from app.services.suizidalitaet import mentions_suizidalitaet


def _protokoll(set_key="kunst", **antworten) -> dict:
    s = get_set(set_key)
    eintraege = []
    for f in s.fragen:
        default = {"klient": "Herr M.", SELBSTGEFAEHRDUNG_KEY: "Nein, keine Hinweise."}.get(f.key, f"Antwort zu {f.key}")
        eintraege.append({"key": f.key, "frage": f.text, "antwort": antworten.get(f.key, default)})
    return {"set": set_key, "set_label": s.label, "eintraege": eintraege}


async def _no_llm(*a, **k):
    raise AssertionError("LLM darf hier nicht gerufen werden")


async def _llm_ok(*a, **k):
    return {"structured_data": {"fehlende_aspekte": [], "rueckfrage": ""}, "model_used": "m"}


# ── Phrasen ───────────────────────────────────────────────────────────────────

class TestPhrasen:
    def test_varianten_und_keine_wiederholung(self):
        for seed in range(20):
            q = quittung(seed)
            assert q in QUITTUNGEN
            assert quittung(seed, vorherige=q) != q
            u = ueberleitung(seed)
            assert u in UEBERLEITUNGEN
            assert ueberleitung(seed, vorherige=u) != u

    def test_ernste_quittung_ohne_gut_okay(self):
        for seed in range(20):
            q = quittung(seed, ernst=True)
            assert q in QUITTUNGEN_ERNST
            assert not q.lower().startswith(("gut", "okay"))


# ── Sets / Klient ─────────────────────────────────────────────────────────────

class TestKlient:
    @pytest.mark.parametrize("s", INTERVIEW_SETS, ids=lambda s: s.key)
    def test_klient_ist_erste_frage_und_pflicht(self, s):
        assert s.fragen[0].key == KLIENT_KEY and s.fragen[0].pflicht
        assert s.fragen[0].ziel_abschnitt == "meta"

    @pytest.mark.parametrize("text,erw", [
        ("Herr Müller", ("Herr", "M.", "m")),
        ("Frau K.", ("Frau", "K.", "w")),
        ("es geht um Herrn Schmidt-Lange", ("Herr", "S.", "m")),
        ("Um Frau Özdemir.", ("Frau", "Ö.", "w")),
    ])
    def test_extract_klient(self, text, erw):
        k = extract_klient(text)
        assert (k["anrede"], k["initial"], k["gender"]) == erw

    @pytest.mark.parametrize("text", ["", "K.", "die Klientin", "Herr", "der Patient M."])
    def test_extract_klient_nichts(self, text):
        assert extract_klient(text) is None

    async def test_turn_klient_erkannt(self):
        res = await decide_turn(TurnRequest("kunst", KLIENT_KEY, "Um wen?", "Frau Kaiser"), generate_fn=_no_llm)
        assert res.rueckfrage is None and res.quelle == "klient"
        assert res.klient == {"anrede": "Frau", "initial": "K.", "gender": "w", "nennung": "Frau Kaiser"}
        assert res.ueberleitung in UEBERLEITUNGEN

    async def test_turn_klient_nicht_erkannt_einmal_rueckfrage(self):
        res = await decide_turn(TurnRequest("kunst", KLIENT_KEY, "Um wen?", "keine Ahnung"), generate_fn=_no_llm)
        assert res.rueckfrage == KLIENT_RUECKFRAGE and res.rueckfrage_typ == "klient"
        assert res.quittung in QUITTUNGEN
        res2 = await decide_turn(TurnRequest("kunst", KLIENT_KEY, "Um wen?", "weiss nicht",
                                             rueckfrage_bereits=True), generate_fn=_no_llm)
        assert res2.rueckfrage is None and res2.klient is None  # D4=B: Feld bleibt Pflicht

    def test_klient_nicht_im_prompt(self):
        p = parse_protokoll(json.dumps(_protokoll()))
        text = render_protokoll(p)
        assert "Um wen geht es" not in text and "Herr M." not in text
        assert text.count("FRAGE ") == len(get_set("kunst").fragen) - 1
        assert "Herr M." not in protokoll_plaintext(p)
        assert p.klient() == {"anrede": "Herr", "initial": "M.", "gender": "m", "nennung": "Herr M."}


# ── Trigger ───────────────────────────────────────────────────────────────────

class TestTrigger:
    @pytest.mark.parametrize("text", [
        "Lebensmüde Gedanken wurden klar geäußert.",
        "Er hat von Suizidgedanken berichtet.",
        "Sie hat sich letzte Woche geritzt.",
        "keine Pläne, aber lebensmüde Gedanken geäußert",
    ])
    def test_stufe0_ausloeser(self, text):
        r = pruefe_trigger(text, anrede="Herr M.")
        assert r.trigger == TRIGGER_SUIZIDALITAET and r.stufe == 1
        assert "Herr M." in r.nachfrage and "absprachefähig" in r.nachfrage

    @pytest.mark.parametrize("text", [
        "Nein, keine Hinweise auf Suizidalität.",
        "Von akuter Suizidalität glaubhaft distanziert.",
        "Keine lebensmüden Gedanken, verneint.",
        "Wir haben über die Familie gesprochen.",
        "",
    ])
    def test_stufe0_kein_trigger(self, text):
        assert pruefe_trigger(text).nachfrage is None

    @pytest.mark.parametrize("text,erw", [
        ("Ja, konkrete Pläne, nicht absprachefähig.", True),
        ("Keine Pläne, absprachefähig.", False),
        ("Ja eindeutig absprachefähig, keine Handlungspläne.", False),
        ("Unsicher, ob er absprachefähig ist.", True),
        ("Er hat eine Methode genannt.", True),
    ])
    def test_stufe1(self, text, erw):
        r = pruefe_trigger(text, stufe=1, anrede="Herr M.")
        assert (r.nachfrage is not None) == erw
        if erw:
            assert r.stufe == 2 and "Kooperationsbedingung" in r.nachfrage

    def test_stufe2_beendet(self):
        assert pruefe_trigger("Kooperationsbedingung vereinbart.", stufe=2).nachfrage is None

    async def test_turn_trigger_vor_aspekt_und_kette(self):
        req = TurnRequest("kunst", "beobachtung", "F?", "Sie hat lebensmüde Gedanken geäußert.", anrede="Frau K.")
        res = await decide_turn(req, generate_fn=_no_llm)   # Trigger zuerst, kein LLM (C1)
        assert res.rueckfrage_typ == "trigger:suizidalitaet" and res.trigger_stufe == 1
        assert res.quittung in QUITTUNGEN_ERNST
        req2 = TurnRequest("kunst", "beobachtung", "F?", "Konkrete Pläne, nicht absprachefähig.",
                           anrede="Frau K.", trigger_stufe=1)
        res2 = await decide_turn(req2, generate_fn=_no_llm)
        assert res2.trigger_stufe == 2 and "Kooperationsbedingung" in res2.rueckfrage
        req3 = TurnRequest("kunst", "beobachtung", "F?", "Kooperationsbedingung und Nachtdienst informiert.",
                           anrede="Frau K.", trigger_stufe=2)
        res3 = await decide_turn(req3, generate_fn=_no_llm)
        assert res3.rueckfrage is None and res3.ueberleitung in UEBERLEITUNGEN

    async def test_turn_trigger_auch_bei_pflichtfrage(self):
        req = TurnRequest("kunst", SELBSTGEFAEHRDUNG_KEY, "F?", "Lebensmüde Gedanken wurden geäußert.")
        res = await decide_turn(req, generate_fn=_no_llm)
        assert res.rueckfrage_typ == "trigger:suizidalitaet" and "die Person" in res.rueckfrage

    async def test_aspekt_rueckfrage_bekommt_anrede_und_quittung(self):
        seen = {}
        async def fn(system, user, **kw):
            seen["user"] = user
            return {"structured_data": {"fehlende_aspekte": ["Kontakt"], "rueckfrage": "Wie war der Kontakt zu Herrn M.?"}, "model_used": "m"}
        res = await decide_turn(TurnRequest("kunst", "beobachtung", "F?", "Viel Rot.", anrede="Herr M.", frage_index=3), generate_fn=fn)
        assert "PERSON: Herr M." in seen["user"]
        assert res.rueckfrage_typ == "aspekt" and res.quittung in QUITTUNGEN and res.ueberleitung is None

    async def test_keine_rueckfrage_ueberleitung_nur_bei_antwort(self):
        res = await decide_turn(TurnRequest("kunst", "beobachtung", "F?", "Viel Rot, guter Kontakt."), generate_fn=_llm_ok)
        assert res.rueckfrage is None and res.ueberleitung in UEBERLEITUNGEN and res.quittung is None
        res2 = await decide_turn(TurnRequest("kunst", "vereinbarung", "F?", ""), generate_fn=_no_llm)
        assert res2.ueberleitung is None and res2.quittung is None


# ── Protokoll: Nachfragen + Abschluss ─────────────────────────────────────────

class TestProtokollV1924:
    def test_nachfragen_rendering_und_suizid_quelle(self):
        p = _protokoll(beobachtung="Lebensmüde Gedanken geäußert.")
        p["eintraege"][3]["nachfragen"] = [
            {"typ": "trigger:suizidalitaet", "frage": "Pläne? Absprachefähig?", "antwort": "Keine Pläne, absprachefähig."},
        ]
        p["abschluss"] = [
            {"typ": "widerspruch", "bezug": ["2", "5"], "frage": "Vereinbart oder nicht?", "antwort": "", "belassen": True},
            {"typ": "luecke", "bezug": ["4"], "frage": "Welche Übung?", "antwort": "Atemübung.", "belassen": False},
        ]
        parsed = parse_protokoll(json.dumps(p))
        text = render_protokoll(parsed)
        assert "NACHFRAGE (Suizidalität) 3: Pläne? Absprachefähig?" in text
        assert "ANTWORT AUF NACHFRAGE 3: Keine Pläne, absprachefähig." in text
        assert "ABSCHLUSS-CHECK" in text
        assert "Bewusst offen gelassen – NICHT ergänzen" in text
        assert "ANTWORT 2: Atemübung." in text
        plain = protokoll_plaintext(parsed)
        assert mentions_suizidalitaet(plain)          # v19.22-Konfliktcheck sieht die Antwort
        assert "Atemübung." in plain and "Vereinbart oder nicht?" not in plain

    def test_legacy_rueckfrage_und_nachfragen_reihenfolge(self):
        p = _protokoll()
        p["eintraege"][1]["rueckfrage"] = "Alt?"
        p["eintraege"][1]["rueckfrage_antwort"] = "Ja."
        p["eintraege"][1]["nachfragen"] = [{"typ": "aspekt", "frage": "Neu?", "antwort": "Auch ja."}]
        text = render_protokoll(parse_protokoll(json.dumps(p)))
        assert text.index("RÜCKFRAGE 1: Alt?") < text.index("RÜCKFRAGE 1: Neu?")

    def test_pflichtfrage_ueber_nachfrage_beantwortet(self):
        p = _protokoll(**{SELBSTGEFAEHRDUNG_KEY: ""})
        p["eintraege"][-1]["nachfragen"] = [{"typ": "aspekt", "frage": "?", "antwort": "Keine."}]
        assert parse_protokoll(json.dumps(p)).hat_selbstgefaehrdung


# ── Abschluss-Check ───────────────────────────────────────────────────────────

class TestAbschluss:
    async def test_punkte_gefiltert_und_gekappt(self):
        seen = {}
        async def fn(system, user, **kw):
            seen["user"] = user
            assert kw["response_format"] == ABSCHLUSS_SCHEMA
            return {"structured_data": {"punkte": [
                {"typ": "widerspruch", "bezug": ["2", 5], "frage": "Wurde etwas vereinbart oder nicht?"},
                {"typ": "unsinn", "bezug": [], "frage": "Typ unbekannt?"},
                {"typ": "luecke", "bezug": ["3"], "frage": ""},
                {"typ": "plausibilitaet", "bezug": ["4"], "frage": "Passt das Ergebnis zum Zustand?"},
                {"typ": "luecke", "bezug": ["1"], "frage": "Vierter Punkt?"},
            ]}, "model_used": "m"}
        p = parse_protokoll(json.dumps(_protokoll()))
        punkte, model = await pruefe_abschluss(p, generate_fn=fn)
        assert "PERSON: Herr M." in seen["user"] and "PROTOKOLL:" in seen["user"]
        assert model == "m"
        assert [x["typ"] for x in punkte] == ["widerspruch", "luecke", "plausibilitaet"]
        assert punkte[0]["bezug"] == ["2", "5"]

    async def test_llm_fehler_leer(self):
        async def fn(*a, **k):
            raise RuntimeError("down")
        p = parse_protokoll(json.dumps(_protokoll()))
        assert (await pruefe_abschluss(p, generate_fn=fn))[0] == []


# ── Endpoints ─────────────────────────────────────────────────────────────────

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


class TestEndpoints:
    def test_turn_trigger_liefert_neue_felder(self, client):
        r = client.post("/api/interview/turn", json={
            "set": "kunst", "frage_key": "inhalte", "frage_text": "F?",
            "antwort": "Suizidgedanken wurden geäußert.", "anrede": "Frau K.",
            "session_id": "abc123", "frage_index": 2,
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["rueckfrage_typ"] == "trigger:suizidalitaet" and d["trigger_stufe"] == 1
        assert "Frau K." in d["rueckfrage"] and d["quittung"] and d["ueberleitung"] is None

    def test_turn_klient(self, client):
        r = client.post("/api/interview/turn", json={
            "set": "kunst", "frage_key": KLIENT_KEY, "frage_text": "Um wen?", "antwort": "Frau Berger",
        })
        assert r.json()["klient"] == {"anrede": "Frau", "initial": "B.", "gender": "w", "nennung": "Frau Berger"}

    def test_abschluss_endpoint(self, client, monkeypatch):
        import app.api.interview as api

        async def fake(p, *, model=None):
            return [{"typ": "luecke", "bezug": ["2"], "frage": "Welche Übung genau?"}], "m"
        monkeypatch.setattr("app.services.interview_abschluss.pruefe_abschluss", fake)
        r = client.post("/api/interview/abschluss", json={"protokoll": _protokoll(), "session_id": "abc123"})
        assert r.status_code == 200, r.text
        assert r.json() == {"punkte": [{"typ": "luecke", "bezug": ["2"], "frage": "Welche Übung genau?"}], "model_used": "m"}
        assert api  # noqa

    def test_abschluss_ohne_pflichtfrage_422(self, client):
        p = _protokoll(**{SELBSTGEFAEHRDUNG_KEY: ""})
        r = client.post("/api/interview/abschluss", json={"protokoll": p})
        assert r.status_code == 422

    def test_generate_kuerzel_aus_klient(self, client, monkeypatch):
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
            "interview_protokoll": json.dumps(_protokoll()),
        })
        assert r.status_code == 200, r.text
        assert created["patient_kuerzel"] == "Herr M."
