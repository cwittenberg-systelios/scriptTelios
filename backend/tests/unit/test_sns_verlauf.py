"""
Tests SNS-Verlaufsauswertung (v19.41) - deterministischer Kern.

Fixture: tests/fixtures/sns_verlauf (synthetisch, Generator make_fixture.py,
Sollwerte eingefroren in sollwerte.json). Pilot-Sollwerte (echte Daten) nur
lokal ueber SNS_PILOT_DIR (skipif).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from app.services import sns_verlauf as sv

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sns_verlauf"


@pytest.fixture(scope="module")
def fx():
    return {
        "hsf": (FIX / "hsf.csv").read_text(encoding="utf-8"),
        "ind": (FIX / "individuell.csv").read_text(encoding="utf-8"),
        "xml": (FIX / "individuell.xml").read_text(encoding="utf-8"),
        "xml_res": (FIX / "individuell_iii_ressource.xml").read_text(encoding="utf-8"),
        "xlsx": (FIX / "userexport.xlsx").read_bytes(),
        "soll": json.loads((FIX / "sollwerte.json").read_text(encoding="utf-8")),
    }


@pytest.fixture(scope="module")
def analyse(fx):
    """Produktivpfad: Userexport + Fragebogen-XML."""
    return sv.analyse_userexport(fx["xlsx"], fx["xml"])


# ── Parser ───────────────────────────────────────────────────────────────────

class TestParser:
    def test_csv_basics(self, fx):
        s = sv.parse_sns_csv(fx["hsf"])
        assert s.username == "FX12345IND"
        assert s.questionnaire == "HSF kurz Basis"
        assert len(s.items) == 19
        assert s.values.shape == (40, 19)
        assert s.imputed == [dt.date(2026, 3, 22)]
        # x-Zeile -> NaN, aber uebertragene Werte bleiben in imputed_values
        assert np.isnan(s.values[20]).all()
        assert len(s.imputed_values[dt.date(2026, 3, 22)]) == 19
        assert dt.date(2026, 3, 22) not in s.comments
        assert "Jonas" in s.comments[dt.date(2026, 3, 6)]

    def test_csv_keep_imputed(self, fx):
        s = sv.parse_sns_csv(fx["hsf"], keep_imputed=True)
        assert not np.isnan(s.values[20]).any()
        assert (s.values[20] == s.values[19]).all()

    def test_csv_rejects_non_sns(self):
        with pytest.raises(ValueError):
            sv.parse_sns_csv("a;b;c\n1;2;3\n")

    def test_csv_bom_and_decimal_comma(self):
        txt = ("﻿sep=;\nUsername:;U\nQuestionnaire:;Q\n"
               'DATE:;FILLING DATE:;QUESTIONNAIRE COMMENT:;"A";"B"\n'
               "2026-01-01 20:00:00.0;2026-01-01 20:01:00.0;;12,5;\n")
        s = sv.parse_sns_csv(txt)
        assert s.values[0, 0] == 12.5 and math.isnan(s.values[0, 1])

    def test_xml_hsf_basis(self):
        fb = sv.load_hsf_basis()
        assert fb.name == "HSF kurz Basis"
        assert len(fb.faktoren) == 9 and len(fb.fragen) == 19
        assert fb.fragen[4].faktoren == [2]          # Symptome -> Faktor III
        assert fb.fragen[6].skala == (1.0, 100.0)    # min value='1' ab Item 7

    def test_xml_individuell(self, fx):
        fb = sv.parse_sns_questionnaire_xml(fx["xml"])
        assert sorted(fb.faktoren) == [0, 1, 2, 3, 4, 5]
        assert [q.faktoren[0] for q in fb.fragen] == [0, 1, 2, 3, 4]
        assert not fb.fragen[2].change_poles
        fb2 = sv.parse_sns_questionnaire_xml(fx["xml_res"])
        assert fb2.fragen[2].change_poles

    def test_xml_invalid(self):
        with pytest.raises(ValueError):
            sv.parse_sns_questionnaire_xml("<nope>")

    def test_match_truncated_items(self, fx):
        s = sv.parse_sns_csv(fx["ind"])
        fb = sv.parse_sns_questionnaire_xml(fx["xml"])
        assert s.items[0].endswith("...")
        assert sv.match_csv_items(s.items, fb) == {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}

    def test_hsf_kurznamen_all_matched(self, fx):
        s = sv.parse_sns_csv(fx["hsf"])
        kurz = [sv.hsf_kurzname(t, i) for i, t in enumerate(s.items)]
        assert not any(k.startswith("HSF-Item") for k in kurz)
        assert kurz[4] == "Symptombelastung" and kurz[18] == "Fokus"
        assert len(set(kurz)) == 19

    def test_sns_factor_z(self):
        v = np.array([[10.0, 50], [20, 60], [30, 70], [40, 80]])
        z = sv.sns_factor_z(v, np.array([1.0, 0.5]))
        assert abs(z.mean()) < 1e-12 and abs(z.std(ddof=1) - 1) < 1e-12
        assert (sv.sns_factor_z(np.ones((3, 2)), np.ones(2)) == 0).all()


class TestUserexport:
    def test_parse_blaetter(self, fx):
        sheets = sv.parse_sns_userexport(fx["xlsx"])
        assert [s.questionnaire for s in sheets] == ["HSF kurz Basis", "Fixture individueller Fragebogen"]
        hsf, ind = sheets
        assert hsf.username == "FX12345IND" and len(hsf.items) == 19 and hsf.items[0] == "Item 1"
        # x-Tag + uebertragener Tag nach dem letzten Messtag
        assert hsf.imputed == [dt.date(2026, 3, 22), dt.date(2026, 4, 11)]
        assert np.isnan(hsf.values[hsf.dates.index(dt.date(2026, 3, 22))]).all()
        assert len(hsf.imputed_values[dt.date(2026, 3, 22)]) == 19
        assert "Jonas" in hsf.comments[dt.date(2026, 3, 6)]
        assert "Podest" in ind.comments[dt.date(2026, 3, 10)] and len(ind.comments) == 5

    def test_gleich_wie_csv(self, fx, analyse):
        b = sv.analyse(fx["hsf"], fx["ind"], fx["xml"])
        assert b.fakten["dk"] == analyse.fakten["dk"]
        assert b.fakten["uebergaenge"] == analyse.fakten["uebergaenge"]
        strip = lambda fs: [{k: v for k, v in f.items() if k not in ("items", "item_titel")} for f in fs]  # noqa: E731
        assert strip(b.fakten["ism"]["faktoren"]) == strip(analyse.fakten["ism"]["faktoren"])

    def test_titel_per_position(self, analyse):
        assert analyse.items[0].titel.startswith("In der Klinik fühle ich mich sicher")
        ind = [it for it in analyse.items if it.bogen == "ind"]
        assert ind[2].titel.startswith("Heute haben Ängste") and ind[2].faktor_ids == [2]

    def test_itemzahl_passt_nicht(self, fx):
        xml4 = fx["xml"].replace(fx["xml"][fx["xml"].rindex("<question "):fx["xml"].rindex("</questions>")], "")
        with pytest.raises(ValueError, match="5 Items.*4"):
            sv.analyse_userexport(fx["xlsx"], xml4)

    def test_blattwahl(self, fx):
        sheets = sv.parse_sns_userexport(fx["xlsx"])
        hsf, ind = sv.select_sheets(sheets, "Fixture individueller Fragebogen")
        assert hsf.questionnaire == "HSF kurz Basis" and ind is sheets[1]
        hsf, ind = sv.select_sheets(sheets, "anderer Name")        # einziges weiteres Blatt
        assert ind is sheets[1]
        with pytest.raises(ValueError, match="HSF"):
            sv.select_sheets(sheets[1:], None)
        drei = sheets + [sv.SnsSeries("u", "Dritter Bogen", ["Item 1"], sheets[1].dates, sheets[1].values[:, :1])]
        with pytest.raises(ValueError, match="mehrere"):
            sv.select_sheets(drei, "gibt es nicht")

    def test_kein_xlsx(self):
        with pytest.raises(ValueError, match="xlsx"):
            sv.parse_sns_userexport(b"kein excel")


# ── DK & Statistik ───────────────────────────────────────────────────────────

class TestDK:
    def test_fluctuation_extremes(self):
        assert sv.fluctuation(np.array([50.0] * 7)) == 0.0
        alt = np.array([0.0, 100, 0, 100, 0, 100, 0])
        assert abs(sv.fluctuation(alt) - 1.0) < 1e-9
        # Skalenbreite (min=1): Normierung auf 99*(m-1)
        assert sv.fluctuation(alt, 1, 100) > sv.fluctuation(alt, 0, 100)

    def test_turning_points_sns_regel(self):
        # Plateau am Fensteranfang ist kein Umkehrpunkt, Plateauende vor Richtungswechsel schon
        assert sv.turning_points(np.array([100, 100, 72, 100, 100, 100, 100.0])) == [0, 2, 6]
        assert sv.turning_points(np.array([99, 100, 100, 72, 100, 100, 100.0])) == [0, 2, 3, 6]
        assert sv.turning_points(np.array([75, 73, 69, 79, 75, 71, 38.0])) == [0, 2, 3, 6]
        assert sv.turning_points(np.array([81, 90, 100, 100, 100, 100, 100.0])) == [0, 6]

    def test_sns_referenzwerte(self):
        # aus dem SNS-Komplexitaetsexport (Kalibrierung 25.09.2026, Fenster 7, Skala 0-100)
        for w, lo, soll in (([80, 100, 100, 100, 100, 100, 100], 0, 0.00053),
                            ([100, 80, 100, 100, 100, 100, 100], 0, 0.00381),
                            ([100, 100, 72, 100, 100, 100, 100], 1, 0.00441),   # Item mit Skala 1-100
                            ([100, 100, 72, 100, 100, 100, 100], 0, 0.00433),
                            ([75, 73, 69, 79, 75, 71, 38], 0, 0.01036),
                            ([73, 69, 79, 75, 71, 38, 75], 0, 0.02512),
                            ([63, 38, 100, 34, 75, 74, 62], 0, 0.18544)):
            y = np.array(w, float)
            assert abs(sv.fluctuation(y, lo, 100) * sv.distribution(y, lo, 100) - soll) < 2e-5, w

    def test_distribution_extremes(self):
        assert abs(sv.distribution(np.linspace(0, 100, 7)) - 1.0) < 1e-9
        assert sv.distribution(np.array([50.0] * 7)) < 0.05

    def test_dk_window_und_luecken(self):
        x = np.array([10, 90, 10, 90, 10, 90, 10, 90, 50, 50, 50, 50, 50, 50, 50], float)
        dk = sv.dynamic_complexity(x, 7)
        assert np.isnan(dk[:6]).all()
        assert dk[7] > dk[-1] >= 0
        # SNS-Logik: fehlender Tag entfaellt, das Fenster laeuft ueber die Messfolge weiter
        x[9] = np.nan
        dk2 = sv.dynamic_complexity(x, 7)
        assert np.isnan(dk2[9]) and np.isfinite(dk2[10])
        assert np.isfinite(dk2).sum() == 15 - 6 - 1
        # label='start' = SNS-Export-Datum (erster Fenstertag)
        dks = sv.dynamic_complexity(x, 7, label="start")
        assert np.isfinite(dks[0]) and np.isnan(dks[-1])

    def test_dk_critical_ci(self):
        dk = np.array([np.nan] * 6 + [0.1, 0.11, 0.09, 0.1, 0.1, 0.1, 0.3, 0.1])
        crit = sv.dk_critical_ci(dk)
        assert crit[12] and not crit[13] and not crit[7]

    def test_kendall_and_spearman(self):
        y = np.arange(10, dtype=float)
        tau, p = sv.kendall_tau_trend(y)
        assert abs(tau - 1.0) < 1e-9 and p < 0.01
        assert abs(sv.spearman(y, y ** 2) - 1.0) < 1e-9
        assert math.isnan(sv.kendall_tau_trend(np.array([1.0, 1.0, 1.0, 1.0]))[0])


class TestDetektion:
    def test_transitions(self):
        comp = np.array([40.0] * 15 + [75.0] * 15)
        u = sv.detect_transitions(comp)
        assert len(u) == 1 and u[0]["index"] == 15 and u[0]["typ"] == "ordnungsuebergang"
        comp2 = np.array([40.0] * 15 + [52.0] * 15)
        assert sv.detect_transitions(comp2)[0]["typ"] == "niveauverschiebung"
        assert sv.detect_transitions(np.array([40.0] * 30)) == []

    def test_recovery_episodes(self):
        comp = np.array([60.0] * 6 + [45.0, 48.0, 58.0] + [60.0] * 6)
        eps = sv.recovery_episodes(comp)
        assert len(eps) == 1 and eps[0]["start"] == 6 and eps[0]["dauer"] == 2

    def test_longest_run(self):
        m = np.array([0, 1, 1, 0, 1, 1, 1, 0], bool)
        assert sv.longest_run(m) == (4, 7)
        assert sv.longest_run(m, stop=4) == (1, 3)


# ── Gesamtanalyse gegen eingefrorene Sollwerte ──────────────────────────────

class TestSollwerte:
    def test_zeitraum_und_luecken(self, analyse, fx):
        f = analyse.fakten
        assert f["zeitraum"] == fx["soll"]["zeitraum"]
        assert f["luecken"] == fx["soll"]["luecken"]

    def test_uebergang(self, analyse, fx):
        f = analyse.fakten
        assert f["ordnungsuebergang"] == fx["soll"]["ordnungsuebergang"] == "2026-03-19"
        u = f["uebergaenge"]
        assert [x["datum"] for x in u] == [x["datum"] for x in fx["soll"]["uebergaenge"]]
        assert abs(u[0]["shift"] - fx["soll"]["uebergaenge"][0]["shift"]) < 0.5
        assert u[0]["vorlaeufer"]["kritisch"] is True
        assert u[0]["vorlaeufer"]["datum"] < u[0]["datum"]

    def test_phasen(self, analyse, fx):
        ph = [{k: p[k] for k in ("label", "start", "ende", "typ")} for p in analyse.fakten["phasen"]]
        assert ph == fx["soll"]["phasen"]
        assert [p["typ"] for p in ph] == ["ankommen", "krise", "uebergang", "niveauverschiebung"]

    def test_dk_und_resonanz(self, analyse, fx):
        dk = analyse.fakten["dk"]
        assert dk["dk_mittel_max"]["datum"] == fx["soll"]["dk_mittel_max"]["datum"]
        assert abs(dk["dk_mittel_max"]["wert"] - fx["soll"]["dk_mittel_max"]["wert"]) < 0.005
        assert dk["resonanz_max"]["datum"] == fx["soll"]["resonanz_max"]["datum"]
        krit = [(k["datum"], k["anzahl"]) for k in dk["kritische_tage"]]
        assert krit == [tuple(x) for x in fx["soll"]["kritische_tage"]]
        # kritische Haeufung liegt in der Krise/um den Uebergang, nicht in der Stabilisierung
        assert all(d < "2026-03-25" for d, _ in krit)

    def test_einbrueche(self, analyse, fx):
        assert analyse.fakten["einbrueche"] == fx["soll"]["einbrueche"]

    def test_konstante_und_flags(self, analyse, fx):
        f = analyse.fakten
        assert f["hsf"]["konstante_items"] == ["Sicherheit Klinik", "Augenhöhe"]
        assert f["flags"] == fx["soll"]["flags"]
        assert "KONSTANTE_ITEMS" in f["flags"] and "LUECKEN" in f["flags"]
        assert "POLUNG_FRAGLICH" not in f["flags"]

    def test_ism_kennwerte(self, analyse, fx):
        ism = analyse.fakten["ism"]
        s = fx["soll"]
        assert ism["quelle"] == "xml"
        assert "faktorexport" not in ism
        assert ism["unbesetzt"] == ["VI"] == s["ism_unbesetzt"]
        assert ism["tragende_faktoren"] == s["ism_tragend"]
        assert ism["tragende_faktoren"][0] == "IV"
        assert ism["anker_faktor"] == "II" == s["ism_anker"]
        assert ism["langsamster_faktor"] == "V" == s["ism_langsam"]
        for f in ism["faktoren"]:
            soll = s["ism_sprung"][f["roemisch"]]
            if soll is None:
                assert f["sprung_uebergang"] is None
            else:
                assert abs(f["sprung_uebergang"] - soll) < 0.5
        assert ism["faktor_I_negative_serie"]["tage"] >= 3

    def test_polung_check(self, analyse, fx):
        pc = analyse.fakten["polung_check"]
        assert len(pc) == 5 and all(not c["fraglich"] and not c["korrigiert"] for c in pc)
        assert all(c["r"] > 0.5 for c in pc)

    def test_recurrence(self, analyse, fx):
        r = analyse.fakten["recurrence"]
        for k in ("block_anfang", "block_ende", "anfang_ende"):
            assert abs(r[k] - fx["soll"]["recurrence"][k]) < 0.2
        assert r["anfang_ende"] > max(r["block_anfang"], r["block_ende"])

    def test_komposit_trend(self, analyse, fx):
        k = analyse.fakten["hsf"]["komposit"]
        assert k["ende"] > k["anfang"] + 30
        assert k["tau"] > 0.5
        assert len(k["items"]) == 12

    def test_hsf_faktoren_aus_xml(self, analyse):
        fk = analyse.fakten["hsf"]["faktoren"]
        assert len(fk) == 9
        sym = next(f for f in fk if f["id"] == 2)
        assert sym["polung"] == "−" and sym["tau"] < -0.3
        assert next(f for f in fk if f["id"] == 8)["polung"] == "neutral"

    def test_fakten_json_serialisierbar(self, analyse):
        s = json.dumps(analyse.fakten, ensure_ascii=False)
        assert "NaN" not in s and "Infinity" not in s


# ── Varianten ────────────────────────────────────────────────────────────────

class TestVarianten:
    def test_ohne_individuellen_bogen(self, fx):
        a = sv.analyse(fx["hsf"])
        assert a.fakten["ism"] is None
        assert "KEIN_INDIVIDUELLER_BOGEN" in a.fakten["flags"]
        assert a.fakten["ordnungsuebergang"] == "2026-03-19"
        assert len(a.fakten["phasen"]) >= 3          # Krisenphase ueber Komposit-Median

    def test_ohne_xml(self, fx):
        a = sv.analyse(fx["hsf"], fx["ind"])
        assert a.fakten["ism"]["quelle"] is None
        assert "ISM_OHNE_ZUORDNUNG" in a.fakten["flags"]
        assert len(a.fakten["ind_items"]) == 5

    def test_polung_automatisch_korrigiert(self, fx):
        # XML sagt changePoles=true (ressourcenseitig), die Daten sind aber belastungsseitig
        # -> r mit dem Komposit klar negativ -> automatisch umgepolt (E3=A)
        a = sv.analyse(fx["hsf"], fx["ind"], fx["xml_res"])
        pc = next(c for c in a.fakten["polung_check"] if c["item"].startswith("III"))
        assert pc["korrigiert"] is True and pc["r_vor_korrektur"] < -0.5 and pc["r"] > 0.5
        assert "POLUNG_KORRIGIERT" in a.fakten["flags"] and "POLUNG_FRAGLICH" not in a.fakten["flags"]
        assert any("automatisch umgepolt" in h for h in a.fakten["hinweise"])
        it = next(i for i in a.fakten["ism"]["items"] if i["faktor"] == "III")
        assert it["polung_korrigiert"] and "korrigiert" in it["polung"]
        # Ergebnis identisch mit der korrekt gepolten Variante
        b = sv.analyse(fx["hsf"], fx["ind"], fx["xml"])
        assert a.fakten["ism"]["faktoren"] == b.fakten["ism"]["faktoren"]

    def test_polung_nur_warnung(self, fx):
        p = sv.AnalyseParams(polung_korrektur_grenze=-0.99)
        a = sv.analyse(fx["hsf"], fx["ind"], fx["xml_res"], params=p)
        assert "POLUNG_FRAGLICH" in a.fakten["flags"] and "POLUNG_KORRIGIERT" not in a.fakten["flags"]

    def test_diary_entries(self, analyse):
        e = sv.diary_entries(analyse)
        assert len(e) == 39
        d = next(x for x in e if x["datum"] == "2026-03-10")
        assert d["tagebuch"] and "Podest" in d["kommentar"]


# ── Pilot (nur lokal) ────────────────────────────────────────────────────────

PILOT = os.environ.get("SNS_PILOT_DIR")


def _pilot_files():
    d = Path(PILOT) if PILOT else None
    return d if d and (d / "userexport.xlsx").exists() and (d / "individuell.xml").exists() else None


@pytest.mark.skipif(not _pilot_files(), reason="SNS_PILOT_DIR nicht gesetzt (echte Pilotdaten nur lokal)")
def test_pilot_sollwerte():
    """Pilot WJ28718IND: Userexport + SNS-XML (Produktivpfad)."""
    d = _pilot_files()
    a = sv.analyse_userexport((d / "userexport.xlsx").read_bytes(),
                              (d / "individuell.xml").read_text(encoding="utf-8"))
    f = a.fakten
    assert f["zeitraum"]["messtage_hsf"] == 38 and f["zeitraum"]["tage"] == 39
    assert f["luecken"]["hsf_x"] == ["2026-09-12"]
    assert f["ordnungsuebergang"] == "2026-09-03"
    assert [(u["datum"], u["shift"]) for u in f["uebergaenge"]] == [
        ("2026-08-21", 17.8), ("2026-09-03", 30.4), ("2026-09-13", 12.5), ("2026-09-20", 12.8)]
    assert f["uebergaenge"][1]["vorlaeufer"]["datum"] == "2026-08-31"
    assert f["dk"]["dk_mittel_max"]["datum"] == "2026-08-31"
    assert abs(f["dk"]["dk_mittel_max"]["wert"] - 0.110) < 0.003      # SNS-Formel (25.09.2026)
    assert f["hsf"]["konstante_items"] == ["Sicherheit Klinik", "Augenhöhe"]
    assert [(e["datum"], e["dauer"]) for e in f["einbrueche"]] == [
        ("2026-08-28", 2), ("2026-08-31", 1), ("2026-09-09", 1), ("2026-09-11", 2), ("2026-09-16", 1), ("2026-09-18", 2)]
    r = f["recurrence"]
    assert abs(r["block_anfang"] - 5.9) < 0.3 and abs(r["anfang_ende"] - 8.5) < 0.3
    sym = next(x for x in f["hsf"]["faktoren"] if x["id"] == 2)
    assert abs(sym["tau"] + 0.53) < 0.02
    # individueller Bogen: Item III ressourcenseitig formuliert, im XML nicht umgepolt -> korrigiert
    ism = f["ism"]
    assert "POLUNG_KORRIGIERT" in f["flags"]
    assert [c["korrigiert"] for c in f["polung_check"]] == [False, True, False, False, False]
    assert ism["unbesetzt"] == ["VI"] and ism["anker_faktor"] == "II" and ism["langsamster_faktor"] == "V"
    assert ism["tragende_faktoren"][0] == "IV"
    assert abs(next(x for x in ism["faktoren"] if x["roemisch"] == "IV")["sprung_uebergang"] - 50.8) < 0.5
    assert ism["faktor_I_negative_serie"] == {"start": "2026-08-28", "ende": "2026-09-02", "tage": 6}
    e = sv.diary_entries(a)
    assert len(e) == 38 and sum(1 for x in e if x["kommentar"]) == 30


@pytest.mark.skipif(not (_pilot_files() and (_pilot_files() / "KomplexBasisHSF.pdf").exists()
                         and (_pilot_files() / "hsf.csv").exists()),
                    reason="SNS-Komplexitaetsexport (PDF) nicht vorhanden")
def test_pilot_kalibrierung_gegen_sns():
    """DK exakt wie der SNS-Export (Fenster 7, Itemskala, Messfolge ohne x-Tage)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "kal", Path(__file__).resolve().parents[2] / "scripts" / "sns_kalibrierung.py")
    kal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kal)
    d = _pilot_files()
    rep = kal.kalibriere(d / "hsf.csv", d / "KomplexBasisHSF.pdf", 7, 1.645, None)
    assert rep["gesamt"]["akzeptiert"], rep["gesamt"]
    assert rep["gesamt"]["rel_fehler_max"] < 0.002
    if (d / "individuell.csv").exists() and (d / "KomplexIndiv.pdf").exists():
        rep2 = kal.kalibriere(d / "individuell.csv", d / "KomplexIndiv.pdf", 7, 1.645, None)
        assert rep2["gesamt"]["akzeptiert"], rep2["gesamt"]
