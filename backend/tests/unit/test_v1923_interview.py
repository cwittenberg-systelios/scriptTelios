"""v19.23 - Interview-Modus der Gespraechsdokumentation.

Abgedeckt:
  - Fragen-Sets (Single Source of Truth, Manifest, Pflichtfrage in jedem Set)
  - Protokoll: Validierung, Rendering, Plaintext
  - Dialog-Turn (D1=C): Rueckfrage-Regeln mit gemocktem LLM
  - Prompts: Interview-Block im User-Content, Regeln im System-Prompt
  - Pipeline: Quellen-Gate akzeptiert das Protokoll
  - Endpoints: /interview/sets, /interview/turn, /interview/transcribe,
    /jobs/generate mit interview_protokoll (422-Pfade)
"""
from __future__ import annotations

import json

import pytest

from app.core.interview_sets import (
    ABSCHNITTE, DEFAULT_SET_KEY, INTERVIEW_SETS, SELBSTGEFAEHRDUNG_KEY,
    get_frage, get_set, to_manifest,
)
from app.services.interview_dialog import (
    RUECKFRAGE_SCHEMA, TurnRequest, decide_turn, deterministic_rueckfrage,
)
from app.services.interview_protokoll import (
    InterviewProtokollError, parse_protokoll, protokoll_plaintext, render_protokoll,
)


# ── Hilfen ────────────────────────────────────────────────────────────────────

def _protokoll(set_key="kunst", *, mit_pflicht=True, antworten=None) -> dict:
    s = get_set(set_key)
    eintraege = []
    for f in s.fragen:
        if f.key == SELBSTGEFAEHRDUNG_KEY and not mit_pflicht:
            eintraege.append({"key": f.key, "frage": f.text, "antwort": ""})
            continue
        default = ("Keine Hinweise auf Suizidalität." if f.key == SELBSTGEFAEHRDUNG_KEY
                   else "Frau K." if f.key == "klient" else f"Antwort zu {f.key}")
        eintraege.append({
            "key": f.key, "frage": f.text,
            "antwort": (antworten or {}).get(f.key, default),
        })
    return {"set": set_key, "set_label": s.label, "eintraege": eintraege}


async def _llm(fehlend, rueckfrage, *, raise_exc=None, parse_error=False):
    async def fn(system, user, **kw):
        if raise_exc:
            raise raise_exc
        assert kw.get("response_format") == RUECKFRAGE_SCHEMA
        return {
            "structured_data": None if parse_error else {"fehlende_aspekte": fehlend, "rueckfrage": rueckfrage},
            "structured_parse_error": parse_error,
            "model_used": "test-model",
        }
    return fn


# ── Sets ──────────────────────────────────────────────────────────────────────

class TestSets:
    def test_fuenf_sets_mit_eindeutigen_keys(self):
        keys = [s.key for s in INTERVIEW_SETS]  # noqa: F841
        assert keys == ["gespraech", "kunst", "musik", "koerperarbeit", "koerpertherapie"]
        assert DEFAULT_SET_KEY in keys

    @pytest.mark.parametrize("s", INTERVIEW_SETS, ids=lambda s: s.key)
    def test_jedes_set_hat_pflichtfrage_und_gueltige_abschnitte(self, s):
        fkeys = [f.key for f in s.fragen]
        assert len(fkeys) == len(set(fkeys)), "Frage-Keys muessen je Set eindeutig sein"
        pflicht = [f for f in s.fragen if f.key == SELBSTGEFAEHRDUNG_KEY]
        assert len(pflicht) == 1 and pflicht[0].pflicht
        assert s.fragen[-1].key == SELBSTGEFAEHRDUNG_KEY, "Pflichtfrage steht am Ende"
        for f in s.fragen:
            assert f.ziel_abschnitt in ABSCHNITTE
            assert f.text.strip()

    def test_gespraech_set_bildet_die_vier_abschnitte_ab(self):
        s = get_set("gespraech")
        ziele = [f.ziel_abschnitt for f in s.fragen]
        for z in ("auftragsklaerung", "inhalte", "hypothesen", "einladungen"):
            assert z in ziele

    def test_manifest_shape(self):
        m = to_manifest()
        assert m["default_set"] == DEFAULT_SET_KEY
        assert set(m["abschnitte"]) == set(ABSCHNITTE)
        assert len(m["sets"]) == len(INTERVIEW_SETS)
        f = m["sets"][0]["fragen"][0]
        assert set(f) == {"key", "text", "ziel_abschnitt", "pflicht", "pflichtaspekte", "hinweis"}
        json.dumps(m)  # serialisierbar

    def test_get_frage(self):
        assert get_frage("kunst", "methode").ziel_abschnitt == "inhalte"
        assert get_frage("kunst", "gibtsnicht") is None
        assert get_frage("nope", "methode") is None


# ── Protokoll ─────────────────────────────────────────────────────────────────

class TestProtokoll:
    def test_none_und_leer(self):
        assert parse_protokoll(None) is None
        assert parse_protokoll("   ") is None

    def test_ungueltiges_json(self):
        with pytest.raises(InterviewProtokollError, match="JSON"):
            parse_protokoll("{nicht json")

    def test_strukturfehler_nennt_feld(self):
        with pytest.raises(InterviewProtokollError, match="eintraege"):
            parse_protokoll(json.dumps({"set": "kunst"}))

    def test_ohne_antworten_abgelehnt(self):
        p = _protokoll()
        for e in p["eintraege"]:
            e["antwort"] = ""
        with pytest.raises(InterviewProtokollError, match="keine einzige Antwort"):
            parse_protokoll(json.dumps(p))

    def test_ohne_pflichtfrage_abgelehnt(self):
        with pytest.raises(InterviewProtokollError, match="Selbstgefährdung"):
            parse_protokoll(json.dumps(_protokoll(mit_pflicht=False)))

    def test_pflichtfrage_ueber_rueckfrage_beantwortet_reicht(self):
        p = _protokoll(mit_pflicht=False)
        e = next(x for x in p["eintraege"] if x["key"] == SELBSTGEFAEHRDUNG_KEY)
        e["rueckfrage"] = "Magst du kurz sagen, ob es Hinweise gab?"
        e["rueckfrage_antwort"] = "Nein, keine."
        parsed = parse_protokoll(json.dumps(p))
        assert parsed.hat_selbstgefaehrdung
        assert parsed.antwort_selbstgefaehrdung() == "Nein, keine."

    def test_render_enthaelt_zielabschnitte_und_nicht_erhoben(self):
        p = parse_protokoll(json.dumps(_protokoll(antworten={"ergebnis": ""})))
        text = render_protokoll(p)
        assert text.startswith("Verfahren / Fragen-Set: Kunsttherapie")
        assert "[Zielabschnitt: Auftragsklärung]" in text
        assert "[Zielabschnitt: Relevante Gesprächsinhalte]" in text
        assert "(nicht erhoben)" in text
        assert "RÜCKFRAGE" not in text

    def test_render_mit_rueckfrage(self):
        p = _protokoll()
        p["eintraege"][1]["rueckfrage"] = "Und das Anliegen?"
        p["eintraege"][1]["rueckfrage_antwort"] = "Umgang mit Scham."
        text = render_protokoll(parse_protokoll(json.dumps(p)))
        assert "RÜCKFRAGE 1: Und das Anliegen?" in text
        assert "ANTWORT AUF RÜCKFRAGE 1: Umgang mit Scham." in text

    def test_ziel_abschnitt_aus_frontend_gewinnt(self):
        p = _protokoll()
        p["eintraege"][1]["ziel_abschnitt"] = "einladungen"
        text = render_protokoll(parse_protokoll(json.dumps(p)))
        assert "FRAGE 1 [Zielabschnitt: Einladungen]" in text

    def test_plaintext_nur_antworten(self):
        p = parse_protokoll(json.dumps(_protokoll()))
        plain = protokoll_plaintext(p)
        assert "Antwort zu anliegen" in plain
        assert "Gab es in der Stunde Hinweise" not in plain, "Fragen duerfen keine Marker ausloesen"


# ── Dialog (D1=C) ─────────────────────────────────────────────────────────────

class TestDialog:
    def _req(self, **kw):
        base = dict(set_key="kunst", frage_key="beobachtung",
                    frage_text=get_frage("kunst", "beobachtung").text,
                    antwort="Sie hat viel Rot benutzt und wirkte angespannt.")
        base.update(kw)
        return TurnRequest(**base)

    async def test_bereits_rueckfrage_gestellt_keine_zweite(self):
        res = await decide_turn(self._req(rueckfrage_bereits=True), generate_fn=await _llm(["x"], "Frage?"))
        assert res.rueckfrage is None and res.quelle == "keine"

    async def test_leere_pflichtantwort_deterministisch_ohne_llm(self):
        async def boom(*a, **k):
            raise AssertionError("LLM darf nicht gerufen werden")
        req = self._req(frage_key=SELBSTGEFAEHRDUNG_KEY, frage_text="Gab es Hinweise?", antwort="  ")
        res = await decide_turn(req, generate_fn=boom)
        assert res.quelle == "deterministisch"
        assert res.rueckfrage == deterministic_rueckfrage("Gab es Hinweise?")
        assert "Pflichtfrage" in res.rueckfrage

    async def test_leere_nicht_pflicht_antwort_keine_rueckfrage(self):
        async def boom(*a, **k):
            raise AssertionError("LLM darf nicht gerufen werden")
        res = await decide_turn(self._req(frage_key="vereinbarung", antwort=""), generate_fn=boom)
        assert res.rueckfrage is None and res.quelle == "keine"

    async def test_frage_ohne_pflichtaspekte_keine_rueckfrage(self):
        async def boom(*a, **k):
            raise AssertionError("LLM darf nicht gerufen werden")
        res = await decide_turn(self._req(frage_key="vereinbarung", antwort="Nichts vereinbart."), generate_fn=boom)
        assert res.rueckfrage is None

    async def test_llm_nichts_fehlt(self):
        res = await decide_turn(self._req(), generate_fn=await _llm([], ""))
        assert res.rueckfrage is None and res.quelle == "llm" and res.model_used == "test-model"

    async def test_llm_aspekt_fehlt_rueckfrage(self):
        res = await decide_turn(self._req(), generate_fn=await _llm(
            ["Beziehungsgestaltung oder Kontakt"], "Wie war der Kontakt zwischen euch?"))
        assert res.rueckfrage == "Wie war der Kontakt zwischen euch?"
        assert res.fehlende_aspekte == ["Beziehungsgestaltung oder Kontakt"]

    async def test_llm_inkonsistent_fehlend_ohne_frage_keine_rueckfrage(self):
        res = await decide_turn(self._req(), generate_fn=await _llm(["x"], ""))
        assert res.rueckfrage is None

    async def test_llm_fehler_keine_rueckfrage(self):
        res = await decide_turn(self._req(), generate_fn=await _llm([], "", raise_exc=RuntimeError("ollama down")))
        assert res.rueckfrage is None and res.quelle == "llm_fehler"

    async def test_llm_parse_error_keine_rueckfrage(self):
        res = await decide_turn(self._req(), generate_fn=await _llm([], "", parse_error=True))
        assert res.rueckfrage is None and res.quelle == "llm_fehler"

    async def test_rueckfrage_wird_gekuerzt(self):
        lang = "Wort " * 120
        res = await decide_turn(self._req(), generate_fn=await _llm(["x"], lang))
        assert len(res.rueckfrage) <= 305

    async def test_frontend_aspekte_ueberstimmen_server_default(self):
        seen = {}
        async def fn(system, user, **kw):
            seen["user"] = user
            return {"structured_data": {"fehlende_aspekte": [], "rueckfrage": ""}, "model_used": "m"}
        await decide_turn(self._req(pflichtaspekte=["nur dieser Aspekt"]), generate_fn=fn)
        assert "- nur dieser Aspekt" in seen["user"]
        assert "- Beziehungsgestaltung oder Kontakt" not in seen["user"]


# ── Prompts + Pipeline ────────────────────────────────────────────────────────

class TestPromptsUndPipeline:
    def test_user_content_interview_block(self):
        from app.services.prompts import build_user_content
        u = build_user_content("dokumentation", interview_text="FRAGE 1: a\nANTWORT 1: b")
        assert "PROTOKOLL DER THERAPEUTISCHEN NACHBEFRAGUNG" in u
        assert "FRAGE 1: a" in u
        assert "TRANSKRIPT DES GESPRÄCHS" not in u

    def test_user_content_ohne_interview_unveraendert(self):
        from app.services.prompts import build_user_content
        u = build_user_content("dokumentation", transcript="[A]: Hallo")
        assert "NACHBEFRAGUNG" not in u

    def test_system_prompt_interview_regeln(self):
        from app.services.prompts import build_system_prompt
        s = build_system_prompt("dokumentation", workflow_instructions="X", interview_mode=True)
        assert "NACHBEFRAGUNG STATT TRANSKRIPT" in s
        assert "Zitiere NIEMALS wörtlich" in s
        s2 = build_system_prompt("dokumentation", workflow_instructions="X")
        assert "NACHBEFRAGUNG STATT TRANSKRIPT" not in s2
        s3 = build_system_prompt("anamnese", workflow_instructions="X", interview_mode=True)
        assert "NACHBEFRAGUNG STATT TRANSKRIPT" not in s3, "nur P1"

    def test_quellen_gate_akzeptiert_interview(self):
        from app.services.generation_pipeline import _missing_source_error
        assert _missing_source_error("dokumentation") is not None
        assert _missing_source_error("dokumentation", interview_text="FRAGE 1: x") is None

    def test_suizid_quelle_nutzt_nur_antworten(self):
        """Das Protokoll enthaelt die Pflichtfrage ('Suizidalitaet') - fuer den
        Konflikt-Check (v19.22 D2=B) duerfen nur die Antworten zaehlen."""
        from app.services.suizidalitaet import mentions_suizidalitaet
        p = parse_protokoll(json.dumps(_protokoll(antworten={SELBSTGEFAEHRDUNG_KEY: "Nein, keine."})))
        assert mentions_suizidalitaet(render_protokoll(p))
        assert not mentions_suizidalitaet(protokoll_plaintext(p))

    def test_pipeline_input_meta(self):
        from app.services.generation_pipeline import PipelineInput
        p = parse_protokoll(json.dumps(_protokoll("musik")))
        ctx = PipelineInput(workflow="dokumentation", instructions="X", model=None, interview_protokoll=p)
        meta = ctx.input_meta()
        assert meta["has_interview"] is True and meta["interview_set"] == "musik"
        assert PipelineInput(workflow="dokumentation", instructions="X", model=None).input_meta()["has_interview"] is False


# ── Endpoints ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch):
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
    def test_sets(self, client):
        r = client.get("/api/interview/sets")
        assert r.status_code == 200
        assert [s["key"] for s in r.json()["sets"]][0] == "gespraech"

    def test_turn_deterministisch(self, client):
        r = client.post("/api/interview/turn", json={
            "set": "kunst", "frage_key": SELBSTGEFAEHRDUNG_KEY,
            "frage_text": "Gab es Hinweise?", "antwort": "",
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["quelle"] == "deterministisch" and "Pflichtfrage" in d["rueckfrage"]

    def test_turn_llm_gemockt(self, client, monkeypatch):
        import app.api.interview as api
        from app.services.interview_dialog import TurnResult

        async def fake(turn, *, model=None):
            assert turn.frage_key == "beobachtung" and model is None
            return TurnResult("Wie war der Kontakt?", ["Kontakt"], "llm", "m")
        monkeypatch.setattr(api, "decide_turn", fake)
        r = client.post("/api/interview/turn", json={
            "set": "kunst", "frage_key": "beobachtung", "frage_text": "F?",
            "antwort": "Viel Rot.", "bisherige": [{"frage": "A?", "antwort": "a"}],
        })
        assert r.status_code == 200
        d = r.json()
        assert {k: d[k] for k in ("rueckfrage", "fehlende_aspekte", "quelle", "model_used")} == {
            "rueckfrage": "Wie war der Kontakt?", "fehlende_aspekte": ["Kontakt"],
            "quelle": "llm", "model_used": "m"}

    def test_transcribe_gemockt_und_tempdatei_weg(self, client, monkeypatch):
        import app.services.transcription as tr
        from app.core.files import upload_dir
        seen = {}

        async def fake(path):
            seen["exists_during"] = path.exists()
            seen["path"] = path
            return {"transcript": "Nein, keine Hinweise.", "duration_seconds": 2.0, "word_count": 3}
        monkeypatch.setattr(tr, "transcribe_dictation", fake)
        r = client.post("/api/interview/transcribe",
                        files={"audio": ("antwort.webm", b"\x1aE\xdf\xa3fake", "audio/webm")})
        assert r.status_code == 200, r.text
        assert r.json()["transcript"] == "Nein, keine Hinweise."
        assert seen["exists_during"] and not seen["path"].exists()
        assert not list(upload_dir().glob("diktat_*"))

    def test_transcribe_falsches_format(self, client):
        r = client.post("/api/interview/transcribe", files={"audio": ("x.txt", b"abc", "text/plain")})
        assert r.status_code == 422

    def test_transcribe_zu_lang(self, client, monkeypatch):
        import app.services.transcription as tr

        async def fake(path):
            raise ValueError("Diktat zu lang")
        monkeypatch.setattr(tr, "transcribe_dictation", fake)
        r = client.post("/api/interview/transcribe", files={"audio": ("a.webm", b"xyz", "audio/webm")})
        assert r.status_code == 422 and "zu lang" in r.json()["detail"]

    def _generate(self, client, monkeypatch, protokoll, workflow="dokumentation"):
        import app.api.jobs as jobs_api

        async def fake_model(requested, wf):
            return "gemma4:31b"
        monkeypatch.setattr("app.services.llm.ensure_generation_model", fake_model)
        created = {}

        class _Job:
            job_id = "j-test"
        def fake_create(**kw):
            created.update(kw)
            return _Job()
        monkeypatch.setattr(jobs_api.job_queue, "create_job", fake_create)
        monkeypatch.setattr(jobs_api.job_queue, "run_job", lambda *a, **k: None)
        data = {"workflow": workflow, "workflow_instructions": "Doku.", "patientenname": "Frau K."}
        if protokoll is not None:
            data["interview_protokoll"] = json.dumps(protokoll)
        r = client.post("/api/jobs/generate", data=data)
        return r, created

    def test_generate_ohne_pflichtfrage_422_kein_job(self, client, monkeypatch):
        r, created = self._generate(client, monkeypatch, _protokoll(mit_pflicht=False))
        assert r.status_code == 422 and "Selbstgefährdung" in r.json()["detail"]
        assert not created

    def test_generate_falscher_workflow_422(self, client, monkeypatch):
        r, _ = self._generate(client, monkeypatch, _protokoll(), workflow="anamnese")
        assert r.status_code == 422 and "dokumentation" in r.json()["detail"]

    def test_generate_mit_protokoll_legt_job_an(self, client, monkeypatch):
        r, created = self._generate(client, monkeypatch, _protokoll())
        assert r.status_code == 200, r.text
        assert r.json()["job_id"] == "j-test"
        assert "Interview: Kunsttherapie" in created["description"]
        assert created["patient_kuerzel"] == "Frau K."
