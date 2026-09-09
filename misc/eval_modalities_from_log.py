#!/usr/bin/env python3
"""
eval_modalities_from_log.py — v19.20 M6: Modalitaets-Abdeckung im Entlassbericht.

Liest prompts.log-Dateien, paart EB-Calls mit Output und misst je Job:
Erwaehnungen von Gruppe/Einzel/Nonverbal im Input vs. Output, ob ein eigener
Absatz (>= 2 Erwaehnungen in einem Absatz) existiert, Output-Woerter.
Basis-Kalibrierung 13.08.-09.09.: Gruppen-Absatz fehlte in 10/19.

Aufruf:  python3 misc/eval_modalities_from_log.py /workspace/prompts.log*
"""
import re
import sys

STEMS = {"Gruppe": ("gruppe",), "Einzel": ("einzelgespr", "einzeltherap", "einzelsitzung", "im einzel"),
         "Nonverbal": ("kunst", "musik", "körper", "nonverbal")}


def parse(paths):
    blocks = []
    for path in paths:
        txt = open(path, errors="replace").read()
        for b in re.split(r"\n(?=\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", txt):
            m = re.search(r"JOB: (\w+)\s+\|\s+WORKFLOW: (\S+)\s+\|\s+CALL: (\S+)(.*)", b)
            if not m:
                continue
            jid, wf, call, rest = m.groups()
            parts = b.split("=" * 80)
            blocks.append((b[:19], jid, wf, call, "(OUTPUT)" in rest,
                           parts[2] if len(parts) >= 3 else b))
    return blocks


def count(text, stems):
    low = text.lower()
    return sum(low.count(s) for s in stems)


def has_para(text, stems):
    return any(count(p, stems) >= 2 for p in text.split("\n\n"))


def main(paths):
    blocks = parse(paths)
    outs = {(j, c): t for (_, j, _, c, o, t) in blocks if o}
    print(f"{'ts':19s} {'job':8s} {'words':5s} | " + " | ".join(f"{k:>14s}" for k in STEMS))
    print(" " * 36 + "| " + " | ".join(f"{'in->out  abs':>14s}" for _ in STEMS))
    missing = {k: 0 for k in STEMS}; n = 0
    for ts, jid, wf, call, out, text in blocks:
        if wf != "entlassbericht" or call != "entlassbericht" or out:
            continue
        o = outs.get((jid, "entlassbericht"))
        if not o:
            continue
        user = text.split("--- USER ---")[-1]
        n += 1
        cells = []
        for k, stems in STEMS.items():
            ci, co, para = count(user, stems), count(o, stems), has_para(o, stems)
            if ci >= 3 and not para:
                missing[k] += 1
            cells.append(f"{ci:3d}->{co:2d} {'ja' if para else 'NEIN':>5s}")
        print(f"{ts} {jid[:8]} {len(o.split()):5d} | " + " | ".join(cells))
    if n:
        print(f"\n{n} Entlassberichte; eigener Absatz FEHLT trotz dokumentierter Modalitaet: "
              + ", ".join(f"{k} {v}/{n}" for k, v in missing.items()))


if __name__ == "__main__":
    main(sys.argv[1:] or ["/workspace/prompts.log"])
