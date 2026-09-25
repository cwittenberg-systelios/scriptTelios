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
             ("doc", "faktor_I.doc"))}


def _is_png(b64: str) -> bool:
    return base64.b64decode(b64)[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_all_vollstaendig(texte):
    a = sv.analyse(texte["hsf"], texte["ind"], texte["xml"], texte["doc"])
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
