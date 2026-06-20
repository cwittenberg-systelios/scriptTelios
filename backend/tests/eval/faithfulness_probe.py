#!/usr/bin/env python3
"""Quellentreue-Probe fuer dokumentation-Eval-Outputs.

Misst zwei Halluzinationsarten, die der normale Eval NICHT prueft (der schaut nur
auf Wortzahl, Pflichtsektionen, Stil-Scores):

  1. AUFGESTUELPTES Verfahrens-/IFS-Vokabular: ein Begriff steht im Output, aber
     sein Stamm taucht im zugehoerigen Transkript NICHT auf -> Quellentreue-Bruch.
  2. ERFUNDENE Standard-Hausaufgaben (Notizbuch/Tagebuch/Achtsamkeitsuebung ...),
     die nicht im Transkript stehen.

Mapping dok-Case -> Transkript via fixtures.json (input_files.audio -> .transcript.txt).

Bewusst KONSERVATIV: gesucht wird ein distinktiver Wortstamm als Substring. Taucht
der Stamm irgendwo im Transkript auf, gilt der Begriff als belegt (auch wenn er dort
in anderem Kontext steht). Die Probe UNTERtreibt also eher, als dass sie ueber-
flaggt - sie meldet nur die klaren Faelle (Begriff im Output, Stamm nirgends im
Transkript). Das ist ein belastbarer Untergrenzen-Indikator fuer Halluzination.

Nutzung:
  cd /workspace/scriptTelios/backend
  /workspace/venv/bin/python -m tests.eval.faithfulness_probe \\
    --results /workspace/eval_results/context_quick/ctx_16384 \\
    --eval-data /workspace/eval_data
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# Begriffslisten + Vergleichslogik zentral aus dem Produktionscode (single
# source of truth) - identisch zu Produktions-QA und Eval-Framework.
from app.services.source_fidelity import (  # noqa: E402
    METHOD_TERMS, HOMEWORK_TERMS, find_imposed_vocab,
)


def load_case_transcripts(backend: Path, eval_data: Path) -> dict[str, Path]:
    fx = json.loads((backend / "tests/fixtures/eval/fixtures.json").read_text(encoding="utf-8"))
    mapping: dict[str, Path] = {}
    for case in fx.get("dokumentation", []):
        audio = (case.get("input_files") or {}).get("audio")
        if audio:
            mapping[case["id"]] = (eval_data / audio).with_suffix(".transcript.txt")
    return mapping


def scan(text_lo: str, tr_lo: str | None, terms):
    """-> Liste (label, used, imposed)."""
    rows = []
    for label, stem in terms:
        if stem in text_lo:
            grounded = (tr_lo is not None) and (stem in tr_lo)
            rows.append((label, True, not grounded))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", required=True, help="ctx_*-Ordner mit dokumentation/<id>.txt")
    ap.add_argument("--eval-data", required=True, help="enthaelt <Gespraech>/aufnahme.transcript.txt")
    ap.add_argument("--backend", default=".", help="Backend-Root (fuer fixtures.json), Default CWD")
    args = ap.parse_args()

    backend = Path(args.backend).resolve()
    eval_data = Path(args.eval_data)
    dok_dir = Path(args.results) / "dokumentation"

    case_tr = load_case_transcripts(backend, eval_data)
    outputs = sorted(
        p for p in dok_dir.glob("*.txt")
        if not p.name.endswith((".ref.txt", ".eval.txt"))
    )
    if not outputs:
        print(f"Keine dok-Outputs unter {dok_dir}")
        return

    total_imposed, total_homework, flagged_outputs = 0, 0, 0
    for out in outputs:
        cid = out.stem
        raw = out.read_text(encoding="utf-8")
        text_lo = raw.lower()
        wc = len(raw.split())

        tr_path = case_tr.get(cid)
        tr_lo = tr_path.read_text(encoding="utf-8").lower() if (tr_path and tr_path.exists()) else None
        note = "" if tr_lo is not None else "   [WARN: kein Transkript -> nur Hausaufgaben-Probe]"

        method = scan(text_lo, tr_lo, METHOD_TERMS)
        homework = scan(text_lo, tr_lo, HOMEWORK_TERMS)
        imposed = [l for l, _, imp in method if imp]
        hw_imp = [l for l, _, imp in homework if imp]

        print(f"\n=== {cid}  ({wc} Woerter){note} ===")
        if method:
            for label, _, imp in method:
                tag = "AUFGESTUELPT (nicht im Transkript)" if imp else "belegt"
                print(f"  Verfahren/IFS: {label:30s} {tag}")
        else:
            print("  Verfahren/IFS: keine Begriffe im Output")
        for label in hw_imp:
            print(f"  HAUSAUFGABE ERFUNDEN: {label} (nicht im Transkript)")

        total_imposed += len(imposed)
        total_homework += len(hw_imp)
        if imposed or hw_imp:
            flagged_outputs += 1

    print("\n──────── Zusammenfassung ────────")
    print(f"Outputs geprueft:                  {len(outputs)}")
    print(f"Outputs mit Halluzination:         {flagged_outputs}/{len(outputs)}")
    print(f"Aufgestuelpte Verfahrensbegriffe:  {total_imposed}")
    print(f"Erfundene Hausaufgaben:            {total_homework}")
    print("(0 / 0 = in diesen beiden Dimensionen quellentreu; konservativer Untergrenzen-Indikator)")


if __name__ == "__main__":
    main()
