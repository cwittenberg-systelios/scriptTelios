#!/usr/bin/env python3
"""
usage_from_log.py — Nutzungsstatistik aus prompts.log (v19.20).

Zaehlt Jobs pro Workflow, pro Tag und pro Therapeut (Header-Feld THERAPEUT,
ab v19.20; aeltere Eintraege ohne Feld erscheinen als '-').

Aufruf:  python3 misc/usage_from_log.py /workspace/prompts.log*
"""
import collections
import re
import sys

MAIN = {"dokumentation", "anamnese", "akutantrag", "verlaengerung",
        "folgeverlaengerung", "entlassbericht", "repair"}
HDR = re.compile(r"JOB: (\w+)\s+\|\s+WORKFLOW: (\S+)\s+\|\s+CALL: (\S+)"
                 r"(?:\s+\|\s+THERAPEUT: (\S+))?(.*)")


def main(paths):
    jobs = {}
    for path in paths:
        txt = open(path, errors="replace").read()
        for b in re.split(r"\n(?=\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", txt):
            m = HDR.search(b)
            if not m or "(OUTPUT)" in m.group(5):
                continue
            jid, wf, call, ther = m.group(1), m.group(2), m.group(3), m.group(4) or "-"
            if call in MAIN:
                jobs.setdefault((jid, call), (b[:10], call, ther))
    by_wf = collections.Counter(c for (_, c, _) in jobs.values())
    by_day = collections.Counter(d for (d, _, _) in jobs.values())
    by_ther = collections.Counter(t for (_, _, t) in jobs.values())
    by_ther_wf = collections.Counter((t, c) for (_, c, t) in jobs.values())
    print(f"{len(jobs)} Jobs\n\nPro Workflow:")
    for k, v in by_wf.most_common():
        print(f"  {v:4d}  {k}")
    print("\nPro Therapeut:")
    for t, v in by_ther.most_common():
        wfs = ", ".join(f"{c} {n}" for (tt, c), n in by_ther_wf.most_common() if tt == t)
        print(f"  {v:4d}  {t:16s} ({wfs})")
    print("\nPro Tag:")
    for d in sorted(by_day):
        print(f"  {d}  {by_day[d]:3d}  {'#' * by_day[d]}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["/workspace/prompts.log"])
