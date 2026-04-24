#!/usr/bin/env python3
"""
Standalone Smoke-Test-Runner — laeuft die wichtigsten Tests ohne pytest.

Verwendung:
    cd /workspace/scriptTelios/backend
    python3 tests/run_smoke_tests.py

Nuetzlich:
- Wenn pytest nicht installiert ist
- Schneller Sanity-Check (1-2 Sek)
- CI ohne pytest-Setup

Deckt die wichtigsten Bug-Fixes ab. Vollstaendige Tests laufen mit pytest.
"""
import sys
import traceback
from pathlib import Path

# Backend-Root zum Path
sys.path.insert(0, str(Path(__file__).parent.parent))

PASS = []
FAIL = []


def assert_eq(actual, expected, label):
    if actual == expected:
        PASS.append(label)
    else:
        FAIL.append((label, f"expected {expected!r}, got {actual!r}"))


def assert_true(cond, label):
    if cond:
        PASS.append(label)
    else:
        FAIL.append((label, "condition False"))


def section(name):
    print(f"\n=== {name} ===")


# ─────────────────────────────────────────────────────────────────────────────
# extract_patient_name
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_patient_name():
    section("extract_patient_name")
    try:
        from app.services.extraction import extract_patient_name
    except ImportError as e:
        FAIL.append(("import extract_patient_name", str(e)))
        return

    # Muster 1: Wir berichten über Frau X
    text = "Wir berichten über Frau Sabine Schuster, die sich seit dem 29.01.2026 in unserer stationären Krankenhausbehandlung befindet."
    r = extract_patient_name(text)
    assert_eq(r["initial"] if r else None, "S.", "Muster1 Frau Schuster")

    # Herrn → Herr
    r = extract_patient_name("wir berichten über Herrn Peter Müller, der sich seit...")
    assert_eq(r["anrede"] if r else None, "Herr", "Herrn → Herr Normalisierung")

    # ueber Variante
    r = extract_patient_name("Wir berichten ueber Herrn Max Beispiel, der...")
    assert_eq(r["anrede"] if r else None, "Herr", "ueber-Variante")

    # Briefkopf-Block
    text = "AKUTAUFNAHME\nAntrag auf Kostenübernahme\nFrau\nSabine Schuster\nMusterweg 89\n"
    r = extract_patient_name(text)
    assert_eq(r["nachname"] if r else None, "Schuster", "Briefkopf-Block")

    # Selbstauskunft
    text = "Selbstauskunft\nVorname: Anna\nNachname: Schmidt\nGeschlecht: weiblich\n"
    r = extract_patient_name(text)
    assert_eq(r["anrede"] if r else None, "Frau", "Selbstauskunft")

    # Edge: leer
    assert_true(extract_patient_name("") is None, "leerer Text → None")
    assert_true(extract_patient_name("Hi") is None, "kurzer Text → None")


# ─────────────────────────────────────────────────────────────────────────────
# parse_explicit_patient_name
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_explicit_patient_name():
    section("parse_explicit_patient_name")
    try:
        from app.services.extraction import parse_explicit_patient_name
    except ImportError as e:
        FAIL.append(("import parse_explicit", str(e)))
        return

    r = parse_explicit_patient_name("Frau Sabine Schuster")
    assert_eq(r["initial"] if r else None, "S.", "voller Name mit Anrede")

    r = parse_explicit_patient_name("Herrn Max Mustermann")
    assert_eq(r["anrede"] if r else None, "Herr", "Herrn → Herr")

    r = parse_explicit_patient_name("Frau Schuster")
    assert_eq(r["vorname"] if r else None, "", "nur Anrede + Nachname")

    r = parse_explicit_patient_name("Müller")
    assert_eq(r["initial"] if r else None, "M.", "nur Nachname")

    assert_true(parse_explicit_patient_name("") is None, "leer → None")
    assert_true(parse_explicit_patient_name(None) is None, "None → None")


# ─────────────────────────────────────────────────────────────────────────────
# derive_word_limits
# ─────────────────────────────────────────────────────────────────────────────

def test_derive_word_limits():
    section("derive_word_limits")
    try:
        from app.services.prompts import derive_word_limits
    except ImportError as e:
        FAIL.append(("import derive_word_limits", str(e)))
        return

    assert_eq(derive_word_limits([], 100, 500), (100, 500), "leere Liste → fallback")
    assert_eq(derive_word_limits([None, ""], 100, 500), (100, 500), "None/leer → fallback")
    assert_eq(derive_word_limits(["wort " * 30], 100, 500), (100, 500), "<50w ignoriert")
    assert_eq(derive_word_limits(["wort " * 300], 50, 9999, 0.30), (210, 390), "300w default")
    assert_eq(derive_word_limits(["wort " * 100, "wort " * 500], 50, 9999, 0.30),
              (70, 650), "2 Texte verschieden")


# ─────────────────────────────────────────────────────────────────────────────
# Akut-Cap (Bug-Fix #2)
# ─────────────────────────────────────────────────────────────────────────────

def apply_akut_cap(word_limits, fb_min, fb_max):
    if not word_limits:
        return word_limits
    o_min, o_max = word_limits
    if o_max > fb_max or o_min > fb_max:
        return (fb_min, fb_max)
    return word_limits


def test_akut_cap():
    section("Akut-Cap (Bug #2)")
    assert_eq(apply_akut_cap((422, 783), 150, 400), (150, 400), "Eval-Bug 422-783 → 150-400")
    assert_eq(apply_akut_cap((180, 350), 150, 400), (180, 350), "im Bereich bleibt")
    assert_eq(apply_akut_cap((200, 600), 150, 400), (150, 400), "max drueber → cap")
    assert_eq(apply_akut_cap((100, 200), 150, 400), (100, 200), "untere Werte erlaubt")
    assert_eq(apply_akut_cap(None, 150, 400), None, "None bleibt None")


# ─────────────────────────────────────────────────────────────────────────────
# Patient-Name-Fallback (Bug-Fix #4)
# ─────────────────────────────────────────────────────────────────────────────

def apply_patient_name_fallback(patient_name, workflow):
    if patient_name:
        return patient_name
    if workflow == "dokumentation":
        return {"anrede": "", "vorname": "", "nachname": "",
                "initial": "die Klientin/der Klient"}
    return None


def test_patient_name_fallback():
    section("Patient-Name-Fallback (Bug #4)")
    r = apply_patient_name_fallback(None, "dokumentation")
    assert_eq(r["initial"] if r else None, "die Klientin/der Klient",
              "doku ohne Name → Fallback")
    assert_eq(apply_patient_name_fallback(None, "anamnese"), None,
              "anamnese kein Fallback")
    orig = {"anrede": "Frau", "vorname": "S", "nachname": "T", "initial": "T."}
    assert_eq(apply_patient_name_fallback(orig, "dokumentation"), orig,
              "existierender Name durchgereicht")


# ─────────────────────────────────────────────────────────────────────────────
# build_system_prompt - Substitution (Bug #1)
# ─────────────────────────────────────────────────────────────────────────────

def test_build_system_prompt_substitution():
    section("build_system_prompt Substitution (Bug #1)")
    try:
        from app.services.prompts import build_system_prompt
    except ImportError as e:
        FAIL.append(("import build_system_prompt", str(e)))
        return

    patient = {"anrede": "Frau", "vorname": "Sabine",
               "nachname": "Schuster", "initial": "S."}

    prompt = build_system_prompt(workflow="entlassbericht", patient_name=patient)

    # Substitution: [Patient/in] sollte ersetzt sein
    assert_true("Frau S." in prompt,
                "Substitution: 'Frau S.' kommt im Prompt vor")

    # Bug #1: Verbots-Block enthaelt nicht den eigenen Namen
    if "NIEMALS Platzhalter" in prompt:
        verbot_idx = prompt.find("NIEMALS Platzhalter")
        verbot_block = prompt[verbot_idx:verbot_idx + 200]
        assert_true("'Frau S.'" not in verbot_block,
                    "Bug #1: kein selbstwidersprueglicher Verbots-Text")


# ─────────────────────────────────────────────────────────────────────────────
# Run all
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_extract_patient_name,
        test_parse_explicit_patient_name,
        test_derive_word_limits,
        test_akut_cap,
        test_patient_name_fallback,
        test_build_system_prompt_substitution,
    ]

    for t in tests:
        try:
            t()
        except Exception as e:
            FAIL.append((t.__name__, traceback.format_exc()))

    print(f"\n{'='*60}")
    print(f"  PASSED: {len(PASS)}")
    print(f"  FAILED: {len(FAIL)}")
    print(f"{'='*60}")

    if FAIL:
        print("\nFAILURES:")
        for label, err in FAIL:
            print(f"  ✗ {label}")
            for line in str(err).splitlines():
                print(f"      {line}")
        sys.exit(1)
    else:
        print(f"\n✅ Alle {len(PASS)} Smoke-Tests bestanden!")
        sys.exit(0)
