"""Tests SNS-Grafiken (v19.41, S2): PNGs entstehen headless, Groesse und Vollstaendigkeit."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.services import sns_plots as sp
from app.services import sns_verlauf as sv

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sns_verlauf"


@pytest.fixture(scope="module")
def texte():
    return {n: (FIX / f).read_text(encoding="utf-8") for n, f in
            (("hsf", "hsf.csv"), ("ind", "individuell.csv"), ("xml", "individuell.xml"),
             )}


def _is_png(b64: str) -> bool:
    return base64.b64decode(b64)[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_all_vollstaendig(texte):
    a = sv.analyse_userexport((FIX / "userexport.xlsx").read_bytes(), texte["xml"])
    g = sp.render_all(a, [{"datum": "2026-03-15", "kurz": "Rollenspiel"}])
    assert list(g) == ["hsf_faktoren", "ism_faktoren", "dk_resonanz", "krd", "recurrence", "hantel"]
    assert [v["nr"] for v in g.values()] == [1, 2, 3, 4, 5, 6]
    for v in g.values():
        assert _is_png(v["png_b64"])
        assert len(v["png_b64"]) < 400_000 * 4 / 3


def test_render_ohne_individuellen_bogen(texte):
    a = sv.analyse(texte["hsf"])
    g = sp.render_all(a)
    assert "ism_faktoren" not in g and len(g) == 5
    assert [v["nr"] for v in g.values()] == [1, 2, 3, 4, 5]


def test_render_kurze_reihe_ohne_dk():
    # 5 Messtage: DK-Fenster nie voll -> Grafiken duerfen nicht abstuerzen
    lines = ["sep=;", "Username:;U", "Questionnaire:;HSF kurz Basis",
             "DATE:;FILLING DATE:;QUESTIONNAIRE COMMENT:;" + ";".join(f'"{t}"' for t in sv.load_hsf_basis().fragen and
                                                                          [q.titel for q in sv.load_hsf_basis().fragen])]
    for k in range(5):
        lines.append(f"2026-01-0{k + 1} 20:00:00.0;2026-01-0{k + 1} 20:00:00.0;;" + ";".join(str(40 + 3 * k) for _ in range(19)))
    a = sv.analyse("\n".join(lines))
    g = sp.render_all(a)
    assert len(g) >= 4


def test_schluesselereignisse_und_legende(texte):
    a = sv.analyse_userexport((FIX / "userexport.xlsx").read_bytes(), texte["xml"])
    u = a.fakten["ordnungsuebergang"]
    ev = [{"datum": d.isoformat(), "kategorie": "sonstiges", "kurz": f"Tag {d:%d}"} for d in a.days] + \
         [{"datum": u, "kategorie": "autonomie_erfahrung", "kurz": "Nein gesagt"}]
    sel = sp.schluesselereignisse(a, ev)
    assert 1 <= len(sel) <= sp.MAX_SCHLUESSELEREIGNISSE
    assert [e["datum"] for e in sel] == sorted(e["datum"] for e in sel)
    assert any(e["datum"] == u and e["kurz"] == "Nein gesagt" for e in sel)
    assert len({e["datum"] for e in sel}) == len(sel)
    g = sp.render_all(a, ev)
    assert g["hsf_faktoren"]["legende"][0].startswith("1 · ")
    assert "legende" not in sp.render_all(a, [])["hsf_faktoren"]


def test_lokale_gipfel():
    import numpy as np
    w = np.array([10, 50, 10, 12, 11, np.nan, 13, 40, 12, 12, 12, 12, 30, 12], float)
    assert [t for t, _ in sv.lokale_gipfel(w, min_abstand=2, quantil=90)] == [1, 7]
    assert [t for t, _ in sv.lokale_gipfel(w, min_abstand=2, quantil=90, ueber_vortage=15)] == [1, 7, 12]
