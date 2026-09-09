#!/usr/bin/env python3
"""
measure_prompt_sizes.py — v19.19 K4: Wo geht das Kontextbudget hin?

Baut pro Workflow den System-Prompt (ohne Stilbeispiele/Quellen) und listet
die groessten Bausteine. Basis fuer die Prompt-Diaet: Ziel <= 10k Zeichen
System-Prompt, damit bei 32k-Fenster >= 25k Tokens fuer Quellen + Output
bleiben.

Aufruf:  cd backend && python3 ../misc/measure_prompt_sizes.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services import prompts as P  # noqa: E402

WORKFLOWS = ["dokumentation", "anamnese", "akutantrag", "verlaengerung",
             "folgeverlaengerung", "entlassbericht"]
TOKENS = lambda n: n / 3.5

print(f"{'Workflow':20s} {'System-Prompt':>14s} {'~Tokens':>8s}  Anteil an 16k / 32k")
for wf in WORKFLOWS:
    try:
        sp = P.build_system_prompt(wf, workflow_instructions=None,
                                   source_text="Er berichtete von Sorgen.")
    except TypeError:
        sp = P.build_system_prompt(wf, workflow_instructions="")
    n = len(sp)
    print(f"{wf:20s} {n:14,d} {TOKENS(n):8,.0f}  {TOKENS(n)/16384*100:4.0f}% / {TOKENS(n)/32768*100:3.0f}%")

print("\nGroesste Konstanten (Zeichen):")
consts = []
for name in dir(P):
    v = getattr(P, name)
    if isinstance(v, str) and len(v) > 800 and name.isupper():
        consts.append((len(v), name))
    elif isinstance(v, dict) and name.isupper():
        for k, val in v.items():
            if isinstance(val, str) and len(val) > 800:
                consts.append((len(val), f"{name}[{k}]"))
for n, name in sorted(consts, reverse=True)[:25]:
    print(f"  {n:7,d}  {name}")
