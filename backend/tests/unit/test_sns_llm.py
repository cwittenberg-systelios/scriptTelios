"""Tests SNS-LLM-Teil (v19.41, S3): Pseudonymisierung, Stage A, Zuordnung, Flags, Stage-B-Prompt."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import sns_llm as sl
from app.services import sns_verlauf as sv

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sns_verlauf"


@pytest.fixture(scope="module")
def analyse():
    t = {n: (FIX / f).read_text(encoding="utf-8") for n, f in
         (("hsf", "hsf.csv"), ("ind", "individuell.csv"), ("xml", "individuell.xml"), ("doc", "faktor_I.doc"))}
    return sv.analyse(t["hsf"], t["ind"], t["xml"], t["doc"])


class TestPseudonymisierung:
    def test_rollen_und_kontext(self):
        e = [{"datum": "2026-01-01", "tagebuch": "Telefonat mit meinem Partner Jonas, danach mit Mira geredet. "
                                                  "Frau Dr. Berger sagt, es geht voran. Meine Tochter Lea lacht.",
              "kommentar": "Jonas war sachlich."}]
        out, namen = sl.pseudonymisiere(e, vorname="Anna", kuerzel="K.")
        t = out[0]["tagebuch"]
        assert "Jonas" not in t and "Mira" not in t and "Lea" not in t
        assert "meinem Partner" in t and "Meine Tochter" in t
        assert "mit Mitklient:in geredet" in t
        assert "Frau Dr. Berger" in t
        assert "Jonas" not in out[0]["kommentar"] and "Partner" in out[0]["kommentar"]
        assert namen == ["Anna", "Jonas", "Lea", "Mira"]

    def test_vorname_wird_kuerzel(self):
        out, namen = sl.pseudonymisiere([{"datum": "d", "tagebuch": "Anna hat heute Nein gesagt.", "kommentar": ""}],
                                        vorname="Anna", kuerzel="K.")
        assert out[0]["tagebuch"].startswith("K. hat")

    def test_keine_falschpositiven(self):
        out, namen = sl.pseudonymisiere([{"datum": "d", "tagebuch": "Spaziergang mit Sonne im Wald, Tee bei Ruhe.",
                                          "kommentar": ""}])
        assert out[0]["tagebuch"] == "Spaziergang mit Sonne im Wald, Tee bei Ruhe."
        assert namen == []


class TestStageA:
    def test_parse_verwirft_fremde_zitate(self):
        batch = [{"datum": "2026-03-15", "tagebuch": "Rollenspiel in der Gruppe, ich habe Nein gesagt.", "kommentar": ""},
                 {"datum": "2026-03-16", "tagebuch": "Ruhig.", "kommentar": ""}]
        data = {"tage": [
            {"datum": "2026-03-15", "tenor": "gemischt", "ereignisse": [
                {"kategorie": "autonomie_erfahrung", "ism_faktor": 0, "kurz": "Nein gesagt", "zitat": "ich habe Nein gesagt"},
                {"kategorie": "gruppe", "ism_faktor": 5, "kurz": "erfunden", "zitat": "das steht nicht im Text"},
            ]},
        ]}
        out = sl.parse_stage_a(data, batch, besetzte_faktoren={0, 1})
        assert len(out) == 2
        assert out[0]["tenor"] == "gemischt" and len(out[0]["ereignisse"]) == 1 and out[0]["verworfen"] == 1
        assert out[0]["ereignisse"][0]["ism_faktor"] == 0
        assert out[1]["tenor"] == "neutral/plateau" and out[1]["ereignisse"] == []

    def test_parse_unbesetzter_faktor_wird_null(self):
        batch = [{"datum": "2026-03-15", "tagebuch": "Medikation angepasst.", "kommentar": ""}]
        data = {"tage": [{"datum": "2026-03-15", "tenor": "gut", "ereignisse": [
            {"kategorie": "medikation", "ism_faktor": 5, "kurz": "x", "zitat": "Medikation angepasst"}]}]}
        out = sl.parse_stage_a(data, batch, besetzte_faktoren={0})
        assert out[0]["ereignisse"][0]["ism_faktor"] is None

    async def test_run_stage_a_batches(self, analyse):
        calls = []

        async def fake_generate(system, user, **kw):
            calls.append(kw)
            dates = [ln[4:] for ln in user.splitlines() if ln.startswith("### ")]
            return {"structured_data": {"tage": [{"datum": d, "tenor": "gut", "ereignisse": []} for d in dates]}}

        entries = sv.diary_entries(analyse)
        out = await sl.run_stage_a(entries, analyse.fakten["ism"]["items"], {0, 1, 2, 3, 4},
                                   generate=fake_generate, model="m", batch_size=6)
        assert len(out) == len(entries) == 39
        assert len(calls) == 7
        assert all(kw["response_format"]["required"] == ["tage"] for kw in calls)
        assert calls[0]["temperature_override"] == sl.STAGE_A_TEMPERATURE

    def test_system_prompt_enthaelt_items(self, analyse):
        s = sl.build_stage_a_system_prompt(analyse.fakten["ism"]["items"])
        assert "Faktor 2 (III)" in s and "ism_faktor" in s
        assert "immer null" in sl.build_stage_a_system_prompt(None)


class TestZuordnung:
    def test_parse_fallback(self):
        z = sl.parse_zuordnung({"zuordnung": [{"index": 1, "faktor_id": 2, "richtung": "ressource"},
                                              {"index": 9, "faktor_id": 0, "richtung": "belastung"}]}, 3)
        assert [x["index"] for x in z] == [0, 1, 2]
        assert z[1]["faktor_id"] == 2 and z[1]["richtung"] == "ressource"
        assert z[0]["faktor_id"] == 0 and "Fallback" in z[0]["begruendung"]

    async def test_run_zuordnung_und_analyse(self):
        async def fake_generate(system, user, **kw):
            assert "faktor_id 5" in system
            return {"structured_data": {"zuordnung": [{"index": i, "faktor_id": 0, "richtung": "belastung"} for i in range(5)]}}
        ind = sv.parse_sns_csv((FIX / "individuell.csv").read_text(encoding="utf-8"))
        z = await sl.run_zuordnung(ind.items, generate=fake_generate, model=None)
        fb = sv.build_erschlossenen_fragebogen(ind.items, z)
        a = sv.analyse((FIX / "hsf.csv").read_text(encoding="utf-8"), (FIX / "individuell.csv").read_text(encoding="utf-8"),
                       ind_fb=fb)
        assert a.fakten["ism"]["quelle"] == "erschlossen"
        assert len(a.fakten["ism"]["faktoren"][0]["items"]) == 5


class TestFlagsUndFaktenblock:
    def _stage_a(self, analyse):
        u = analyse.fakten["ordnungsuebergang"]
        ende = analyse.days[-1].isoformat()
        return [
            {"datum": u, "tenor": "gut", "ereignisse": [
                {"datum": u, "kategorie": "medikation", "ism_faktor": None, "kurz": "Dosis erhöht", "zitat": "Dosis erhöht"},
                {"datum": u, "kategorie": "autonomie_erfahrung", "ism_faktor": 0, "kurz": "Nein", "zitat": "Nein gesagt"}]},
            {"datum": ende, "tenor": "gut", "ereignisse": [
                {"datum": ende, "kategorie": "somatik", "ism_faktor": None, "kurz": "Kopfschmerzen", "zitat": "Kopfschmerzen"}]},
        ]

    def test_flags(self, analyse):
        entries = sv.diary_entries(analyse)
        fl = sl.flags_nach_stage_a(analyse, self._stage_a(analyse), entries)
        assert "MEDIKATION_IM_UEBERGANGSFENSTER" in fl["flags"]
        assert "SOMATIK_NEU" in fl["flags"]
        assert "SUIZIDALITAET_IN_QUELLE" not in fl["flags"]
        assert "KONSTANTE_ITEMS" in fl["flags"]

    def test_suizid_warnung(self, analyse):
        entries = [{"datum": "2026-03-05", "tagebuch": "Heute Suizidgedanken gehabt, aber mit der Therapeutin gesprochen.",
                    "kommentar": ""}]
        fl = sl.flags_nach_stage_a(analyse, [], entries)
        assert "SUIZIDALITAET_IN_QUELLE" in fl["flags"]
        assert fl["details"]["suizidalitaet"]["suizidalitaet"] == ["2026-03-05"]

    def test_faktenblock_und_prompt(self, analyse):
        fl = sl.flags_nach_stage_a(analyse, self._stage_a(analyse), sv.diary_entries(analyse))
        fb = sl.build_faktenblock(analyse.fakten, self._stage_a(analyse), fl, "Klientin", "K.")
        assert "ORDNUNGSÜBERGANG (Hauptdatum): 19.03.2026" in fb
        assert "UNBESETZT" in fb and "E1 19.03.2026 (medikation)" in fb
        assert "MEDIKATION_IM_UEBERGANGSFENSTER" in fb
        sysp = sl.build_stage_b_system_prompt(None, (1000, 1800))
        assert sysp.count("ZIELLÄNGE") == 1
        assert "9. Einordnung" in sysp and "hypnosystemisch" in sysp  # als Verbot
        assert json.dumps(analyse.fakten)  # serialisierbar

    def test_sections_present_und_phasennamen(self, analyse):
        text = "\n".join(f"{h}\nText." for h in sl.ABSCHNITTE)
        assert sl.sections_present(text) == []
        assert sl.sections_present(text.replace("6. Rekurrenzmuster", "")) == ["6. Rekurrenzmuster"]
        t7 = ("7. Phasen\nP1 – Ankommen und Orientierung: erste Tage.\nP2: Krise der Selbstzweifel. "
              "Phase 3 (Aufbruch nach dem Nein) folgte.\n8. Anfang und Ende\n")
        ph = sl.phasen_namen_anwenden(t7, analyse.fakten["phasen"])
        assert ph[0]["name"] == "Ankommen und Orientierung"
        assert ph[1]["name"] == "Krise der Selbstzweifel"
        assert ph[2]["name"] == "Aufbruch nach dem Nein"
        assert ph[3]["name"] is None
