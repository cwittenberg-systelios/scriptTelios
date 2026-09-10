"""v19.21: QualityCheck-Registry (CHECK_REGISTRY) - Reihenfolge, Vollstaendigkeit,
run_checks(only=...)."""
import re

from app.services import quality_check as QC


def test_registry_reihenfolge_entspricht_v1920():
    names = [c.name for c in QC.CHECK_REGISTRY]
    assert names == [
        "selbstauskunft_leer", "template_placeholder", "source_truncation",
        "transcript_coverage", "input_truncated",
        "konjunktiv", "diagnosekriterien", "repair_flags", "wir_form",
        "pathologisierende_sprache", "prozessreflexion", "forbidden_names",
        "patient_initial", "gender", "think_blocks", "befund_separator",
        "length", "required_keywords", "required_sections",
        "recommended_sections", "stichpunkte", "kompositum_klebebugs",
        "source_fidelity",
    ]
    assert len(set(names)) == len(names)


def test_jeder_issue_code_gehoert_zu_einer_regel():
    constants = {v for k, v in vars(QC).items() if k.startswith("ISSUE_CODE_") and isinstance(v, str)}
    claimed = {c for chk in QC.CHECK_REGISTRY for c in chk.codes}
    # ISM-Codes leben in ism.run_ism_quality_check (Sonderpfad), nicht in der Registry
    unclaimed = {c for c in constants if c not in claimed and not c.startswith("ISM_")}
    assert unclaimed == set(), f"Codes ohne Regel: {unclaimed}"
    for code in claimed:
        assert QC.ISSUE_CODE_RE.match(code) or code.endswith("_"), code


def test_run_checks_only_und_katalog():
    ctx = QC.QCContext(text="<think>x</think> Hallo Welt.", workflow="dokumentation")
    only = QC.run_checks(ctx, only={"think_blocks"})
    assert [i.code for i in only] == [QC.ISSUE_CODE_THINK_BLOCK_LEAK]
    assert QC.run_checks(ctx, only=set()) == []
    kat = QC.list_checks()
    assert kat[0]["name"] == "selbstauskunft_leer" and "codes" in kat[0]


def test_run_quality_check_delegiert_an_registry(monkeypatch):
    calls = []
    fake = tuple(QC.QCCheck(c.name, (lambda n: (lambda ctx: (calls.append(n) or [])))(c.name), c.codes) for c in QC.CHECK_REGISTRY)
    monkeypatch.setattr(QC, "CHECK_REGISTRY", fake)
    assert QC.run_quality_check("Ein Text.", "dokumentation") == []
    assert calls == [c.name for c in QC.CHECK_REGISTRY]
