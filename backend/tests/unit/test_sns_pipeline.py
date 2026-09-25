"""Tests Integration SNS-Verlauf (v19.41, S6): Registry, Dispatch, Ende-zu-Ende mit Mock-LLM, Endpoint."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core import workflows as W
from app.services import sns_llm as sl
from app.services.generation_pipeline import PipelineInput, UploadBundle
from app.services.quality_check import run_quality_check, serialize_issues
from app.services.sns_pipeline import kuerzel_und_anrede, run_sns_generation

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sns_verlauf"


def _job():
    job = MagicMock()
    job.job_id = "sns-test"
    job._cancel_requested = False
    job.set_progress = MagicMock()
    return job


def _bundle(ind=True, xml=True, doc=True):
    b = UploadBundle(sns_hsf_bytes=(FIX / "hsf.csv").read_bytes(), sns_hsf_name="hsf.csv")
    if ind:
        b.sns_ind_bytes, b.sns_ind_name = (FIX / "individuell.csv").read_bytes(), "individuell.csv"
    if xml:
        b.sns_xml_bytes, b.sns_xml_name = (FIX / "individuell.xml").read_bytes(), "individuell.xml"
    if doc:
        b.sns_doc_bytes, b.sns_doc_name = (FIX / "faktor_I.doc").read_bytes(), "faktor_I.doc"
    return b


def _fake_generate(calls):
    async def fake(system, user, **kw):
        calls.append({"system": system, "user": user, **kw})
        if kw.get("response_format") and "zuordnung" in kw["response_format"]["required"]:
            n = len([ln for ln in user.splitlines() if ln[:1].isdigit()])
            return {"structured_data": {"zuordnung": [{"index": i, "faktor_id": i % 6, "richtung": "belastung"}
                                                     for i in range(n)]}}
        if kw.get("response_format"):
            dates = [ln[4:] for ln in user.splitlines() if ln.startswith("### ")]
            tage = []
            for d in dates:
                ev = []
                if d == "2026-03-15":
                    ev = [{"kategorie": "autonomie_erfahrung", "ism_faktor": 0, "kurz": "Nein gesagt",
                           "zitat": "ich habe zum ersten Mal Nein gesagt"}]
                if d == "2026-03-18":
                    ev = [{"kategorie": "medikation", "ism_faktor": None, "kurz": "Dosis erhöht",
                           "zitat": "Dosis erhöht"}]
                tage.append({"datum": d, "tenor": "gut", "ereignisse": ev})
            return {"structured_data": {"tage": tage}}
        # Stage B
        if kw.get("on_progress"):
            kw["on_progress"](500)
        text = "\n\n".join(f"{h}\nAbsatz zu diesem Abschnitt." for h in sl.ABSCHNITTE)
        text = text.replace("7. Phasen\nAbsatz zu diesem Abschnitt.",
                            "7. Phasen\nP1 – Ankommen: Start. P2 – Krise: Zweifel. P3 – Aufbruch: Nein. P4 – Stabil: Ende.")
        text = text.replace("1. Zusammenfassung\nAbsatz zu diesem Abschnitt.",
                            "1. Zusammenfassung\nDie Klientin (Frau K.) erlebte am 19.03.2026 einen Ordnungsübergang; "
                            "Faktor VI ist unbesetzt; die Medikation wurde am 18.03.2026 angepasst, hypnosystemisch gesehen.")
        return {"text": text, "model_used": "fake", "telemetry": {"x": 1}}
    return fake


class TestRegistry:
    def test_workflow_registriert(self):
        spec = W.get("sns_verlauf")
        assert spec and spec.label == "SNS-Verlaufsauswertung" and not spec.is_structural
        assert "sns_verlauf" in W.WORKFLOW_KEYS
        assert W.word_limit_for("sns_verlauf") == (1000, 1800)
        from app.core.config import settings
        assert settings.WORKFLOW_MODEL["sns_verlauf"] == "gemma4:31b"
        from app.services.prompts import BASE_PROMPTS, WORKFLOW_INSTRUCTIONS_DEFAULT, build_system_prompt
        assert "sns_verlauf" in BASE_PROMPTS and "sns_verlauf" in WORKFLOW_INSTRUCTIONS_DEFAULT
        assert build_system_prompt("sns_verlauf", WORKFLOW_INSTRUCTIONS_DEFAULT["sns_verlauf"]).count("ZIELLÄNGE") == 1

    def test_kuerzel_und_anrede(self):
        assert kuerzel_und_anrede("Frau K.", "w") == ("Frau K.", "Klientin")
        assert kuerzel_und_anrede("Herr M.", None) == ("Herr M.", "Klient")
        assert kuerzel_und_anrede(None, None) == ("Klient:in", "Klientin")

    def test_ism_item_richtung_changepoles(self):
        from app.services.ism import IsmFragebogen, IsmItem, render_sns_xml
        fb = IsmFragebogen(begruessung="Hallo du", verabschiedung="Tschüss dir", items=[
            IsmItem(faktor_id=2, frage="Heute konnte ich mit der Angst gut umgehen.", pol_min="a", pol_max="b",
                    richtung="ressource"),
            IsmItem(faktor_id=2, frage="Heute hat die Angst mich bestimmt.", pol_min="a", pol_max="b"),
        ])
        xml = render_sns_xml(fb, "T")
        assert xml.count("changePoles='true'") == 1 and xml.count("changePoles='false'") == 1
        assert IsmItem(faktor_id=0, frage="Frage lang", pol_min="a", pol_max="b").richtung == "belastung"


class TestPipeline:
    async def test_dispatch(self, monkeypatch):
        import app.services.generation_pipeline as J
        called = {}

        async def fake_run(*, job, ctx):
            called["ctx"] = ctx
            return {"text": "{}"}
        monkeypatch.setattr("app.services.sns_pipeline.run_sns_generation", fake_run)
        ctx = PipelineInput(workflow="sns_verlauf", instructions="I", model=None, uploads=_bundle())
        out = await J.run_generation(ctx, _job())
        assert out == {"text": "{}"} and called["ctx"] is ctx
        assert ctx.input_meta()["has_sns"] is True

    async def test_ende_zu_ende_mit_xml(self):
        calls = []
        ctx = PipelineInput(workflow="sns_verlauf", instructions="", model="m", uploads=_bundle(),
                            patientenname="Frau K.", geschlecht_norm="w", sns_vorname="Anna")
        job = _job()
        out = await run_sns_generation(job=job, ctx=ctx, generate=_fake_generate(calls))
        res = json.loads(out["text"])
        assert res["kuerzel"] == "Frau K." and res["anrede"] == "Klientin"
        assert len(res["grafiken"]) == 6 and res["fakten"]["ordnungsuebergang"] == "2026-03-19"
        assert res["phasen"][0]["name"] == "Ankommen" and res["phasen"][2]["name"] == "Aufbruch"
        assert "MEDIKATION_IM_UEBERGANGSFENSTER" in res["flags"]
        assert "hypnosystemisch" not in res["text"] and "systemisch gesehen" in res["text"]
        assert "Jonas" in res["namen"] and not any("Jonas" in e["tagebuch"] for e in res["quelle"])
        assert res["zuordnung"] is None
        # 7 Stage-A-Batches + 1 Stage B, keine Zuordnung
        assert len(calls) == 8 and calls[-1].get("response_format") is None
        assert calls[-1]["max_tokens"] == 6000 and "FAKTENBLOCK" in calls[-1]["user"]
        assert out["generation_telemetry"]["sns_stage_a_events"] == 2
        # QC ueber den Dispatch
        issues = run_quality_check(out["text"], "sns_verlauf")
        codes = [i.code for i in issues]
        assert "SNS_ABSCHNITT_FEHLT" not in codes and "SNS_UEBERGANG_FEHLT" not in codes
        assert "SNS_NAME_LEAK" not in codes and "HYPNOSYSTEMISCH" not in codes
        assert serialize_issues(issues, workflow="sns_verlauf")["summary"]["checks_run"] == 16
        pcts = [c.args[0] for c in job.set_progress.call_args_list]
        assert pcts == sorted(pcts) and pcts[-1] == 97

    async def test_ende_zu_ende_ohne_xml_zuordnung_erschlossen(self):
        calls = []
        ctx = PipelineInput(workflow="sns_verlauf", instructions="", model="m", uploads=_bundle(xml=False, doc=False))
        out = await run_sns_generation(job=_job(), ctx=ctx, generate=_fake_generate(calls))
        res = json.loads(out["text"])
        assert res["fakten"]["ism"]["quelle"] == "erschlossen" and len(res["zuordnung"]) == 5
        assert "ISM_ZUORDNUNG_ERSCHLOSSEN" in res["flags"]
        assert len(calls) == 9
        assert "SNS_ZUORDNUNG_ERSCHLOSSEN" in [i.code for i in run_quality_check(out["text"], "sns_verlauf")]

    async def test_nur_hsf(self):
        calls = []
        ctx = PipelineInput(workflow="sns_verlauf", instructions="", model="m", uploads=_bundle(False, False, False))
        out = await run_sns_generation(job=_job(), ctx=ctx, generate=_fake_generate(calls))
        res = json.loads(out["text"])
        assert res["fakten"]["ism"] is None and "KEIN_INDIVIDUELLER_BOGEN" in res["flags"]
        assert len(res["grafiken"]) == 5

    async def test_fehlende_hsf(self):
        ctx = PipelineInput(workflow="sns_verlauf", instructions="", model="m")
        with pytest.raises(RuntimeError, match="HSF"):
            await run_sns_generation(job=_job(), ctx=ctx, generate=_fake_generate([]))

    async def test_abbruch(self):
        job = _job()
        job._cancel_requested = True
        ctx = PipelineInput(workflow="sns_verlauf", instructions="", model="m", uploads=_bundle())
        with pytest.raises(RuntimeError, match="__CANCELLED__"):
            await run_sns_generation(job=job, ctx=ctx, generate=_fake_generate([]))


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


class TestEndpoint:
    def test_generate_422_ohne_hsf_und_ok_mit(self, client, monkeypatch):
        import app.api.jobs as jobs_api

        async def fake_model(requested, wf):
            return "gemma4:31b"
        monkeypatch.setattr("app.services.llm.ensure_generation_model", fake_model)
        created = {}

        class _Job:
            job_id = "j"
        monkeypatch.setattr(jobs_api.job_queue, "create_job", lambda **kw: created.update(kw) or _Job())
        monkeypatch.setattr(jobs_api.job_queue, "run_job", lambda *a, **k: None)
        r = client.post("/api/jobs/generate", data={"workflow": "sns_verlauf", "workflow_instructions": "X",
                                                    "patientenname": "Frau K."})
        assert r.status_code == 422 and "HSF" in r.text
        r = client.post("/api/jobs/generate", data={"workflow": "sns_verlauf", "workflow_instructions": "X",
                                                    "patientenname": "Frau K.", "sns_vorname": "Anna"},
                        files={"sns_hsf_csv": ("hsf.csv", (FIX / "hsf.csv").read_bytes(), "text/csv")})
        assert r.status_code == 200, r.text
        assert created["workflow"] == "sns_verlauf" and created["patient_kuerzel"] == "Frau K."
        assert "SNS: hsf.csv" in created["description"]
