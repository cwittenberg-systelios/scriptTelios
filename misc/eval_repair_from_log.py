#!/usr/bin/env python3
"""
eval_repair_from_log.py — v19.19 R4: Repair-Eval direkt aus prompts.log.

Liest eine oder mehrere prompts.log-Dateien, paart jeden Repair-Call mit
seinem Output und misst:
  - Aehnlichkeit Output/Original (No-op: sim >= 0.995 oder sim >= 0.97 bei Wortdelta <= 1 %)
  - Wortzahl-Entwicklung (Schrumpfung bei Ergaenzungs-Hinweisen)
  - ob ein repair_retry-Call stattfand (v19.19 R1)

Keine Patiententexte im Repo: das Skript laeuft auf dem Pod gegen die
echten Logs. Basis-Kalibrierung (13.08.-09.09.): 3/8 identisch, 1/8 auf
65 % geschrumpft.

Aufruf:  python3 misc/eval_repair_from_log.py /workspace/prompts.log*
"""
import difflib
import re
import sys

ADD_RE = re.compile(r"ergänz|ergaenz|hinzufüg|aufnehm|erweiter|mehr", re.I)


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
            content = parts[2] if len(parts) >= 3 else b
            blocks.append((b[:19], jid, wf, call, "(OUTPUT)" in rest, content))
    return blocks


def main(paths):
    blocks = parse(paths)
    outs = {(j, c): t for (_, j, _, c, o, t) in blocks if o}
    rows = []
    for ts, jid, wf, call, out, text in blocks:
        if call != "repair" or out:
            continue
        m = re.search(r">>>ORIGINAL-TEXT<<<\n(.*?)\n>>>/ORIGINAL-TEXT<<<", text, re.S)
        h = re.search(r">>>NUTZERHINWEIS<<<\n(.*?)\n>>>/NUTZERHINWEIS<<<", text, re.S)
        o = outs.get((jid, "repair"))
        if not (m and o):
            continue
        orig, rep = m.group(1).strip(), o.strip()
        sim = difflib.SequenceMatcher(None, " ".join(orig.split()), " ".join(rep.split()),
                                      autojunk=False).ratio()
        ow, nw = len(orig.split()), len(rep.split())
        hint = (h.group(1).strip() if h else "")
        wants_add = bool(hint and ADD_RE.search(hint))
        retried = (jid, "repair_retry") in outs
        delta = abs(nw - ow) / ow if ow else 0.0
        noop = sim >= 0.995 or (sim >= 0.97 and delta <= 0.01)
        verdict = ("NO-OP" if noop else
                   "SHRUNK" if wants_add and nw < ow * 0.9 else "ok")
        rows.append((ts, jid[:8], wf, f"{sim:.2f}", f"{ow}->{nw}",
                     "retry" if retried else "-", verdict, hint[:60].replace("\n", " ")))
    print(f"{'ts':19s} {'job':8s} {'workflow':18s} {'sim':5s} {'words':10s} {'R1':5s} {'verdict':7s} hint")
    for r in rows:
        print("  ".join(str(x) for x in r))
    n = len(rows)
    if n:
        noop = sum(r[6] == "NO-OP" for r in rows)
        shr = sum(r[6] == "SHRUNK" for r in rows)
        print(f"\n{n} Repairs: {noop} No-op ({noop/n:.0%}), {shr} geschrumpft ({shr/n:.0%})")


if __name__ == "__main__":
    main(sys.argv[1:] or ["/workspace/prompts.log"])
