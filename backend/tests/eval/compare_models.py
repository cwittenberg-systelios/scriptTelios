#!/usr/bin/env python3
"""Side-by-Side-Auswertung eines Modellvergleichs (nach run_model_eval.sh).

Liest die per-Modell-Output-Verzeichnisse unter --root (je ein Unterordner pro
Modell-Slug, darin {workflow}/{case_id}.txt + {case_id}.eval.txt) und erzeugt:

  1. eine kompakte Terminal-Uebersicht: pro Modell ein Aggregat (Mittel-Score,
     Summe Issues) + pro Case die Scores nebeneinander - so sieht man auf einen
     Blick, WO sich die Modelle unterscheiden.
  2. einen Markdown-Report (default: <root>/model_comparison.md) mit den VOLLEN
     Outputs jedes Modells pro Case untereinander - zum Lesen der deutschen Prosa.

Der Eval-Score ist nur fuer Regressions-Erkennung gedacht (bricht ein Modell
Quellentreue/Sektionen?), nicht fuer Fein-Ranking. Das eigentliche Signal ist die
Prosa im Markdown.

Nutzung:
  python -m tests.eval.compare_models --root /workspace/eval_results/model_cmp
  python -m tests.eval.compare_models --root <dir> --out /tmp/vergleich.md
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

_SKIP_SUFFIXES = (".eval.txt", ".ref.txt", ".style.json")
_N_PASSED_RE = re.compile(r"(\d+)\s+Checks bestanden")
_HEAD_RE = re.compile(r"^\[(PASS|FAIL)\].*?\((\d+)w\)")


def _parse_eval_txt(path: Path) -> dict:
    """-> {pass: bool, n_passed: int, issues: [str], words: int|None, score: float}."""
    out = {"pass": None, "n_passed": 0, "issues": [], "words": None, "score": None}
    if not path.exists():
        return out
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for ln in lines:
        m = _HEAD_RE.match(ln.strip())
        if m:
            out["pass"] = (m.group(1) == "PASS")
            out["words"] = int(m.group(2))
        m2 = _N_PASSED_RE.search(ln)
        if m2:
            out["n_passed"] = int(m2.group(1))
        s = ln.strip()
        if s.startswith("- "):
            out["issues"].append(s[2:].strip())
    n_iss = len(out["issues"])
    denom = out["n_passed"] + n_iss
    out["score"] = (out["n_passed"] / denom) if denom else 1.0
    return out


def _collect(root: Path) -> tuple[list[str], dict]:
    """-> (slugs, cases) mit cases[(workflow, case_id)][slug] = {text, **eval}."""
    slugs = sorted(d.name for d in root.iterdir() if d.is_dir())
    cases: dict = {}
    for slug in slugs:
        for txt in sorted((root / slug).rglob("*.txt")):
            if txt.name.endswith(_SKIP_SUFFIXES):
                continue
            workflow = txt.parent.name
            case_id = txt.stem
            ev = _parse_eval_txt(txt.with_name(case_id + ".eval.txt"))
            ev["text"] = txt.read_text(encoding="utf-8", errors="replace").strip()
            cases.setdefault((workflow, case_id), {})[slug] = ev
    return slugs, cases


def _fmt_pct(x: float | None) -> str:
    return f"{x*100:4.0f}%" if x is not None else "  - "


def _terminal_summary(slugs: list[str], cases: dict) -> None:
    width = max((len(s) for s in slugs), default=8)
    # Aggregat pro Modell
    print("\n" + "=" * 70)
    print("  MODELL-AGGREGAT  (Score = bestanden / (bestanden + Issues))")
    print("=" * 70)
    print(f"  {'Modell':{width}}   Ø-Score   Σ-Issues   Cases")
    for slug in slugs:
        scores = [c[slug]["score"] for c in cases.values() if slug in c]
        issues = sum(len(c[slug]["issues"]) for c in cases.values() if slug in c)
        n = len(scores)
        avg = sum(scores) / n if n else None
        print(f"  {slug:{width}}   {_fmt_pct(avg)}     {issues:>5}     {n:>4}")

    # Pro Case nebeneinander
    print("\n" + "=" * 70)
    print("  PRO CASE  (Score je Modell - wo unterscheiden sie sich?)")
    print("=" * 70)
    header = f"  {'Case':40}" + "".join(f"{s[:width]:>{width+2}}" for s in slugs)
    print(header)
    for (wf, cid) in sorted(cases.keys()):
        row = f"  {wf + '/' + cid:40.40}"
        for slug in slugs:
            row += f"{_fmt_pct(cases[(wf, cid)].get(slug, {}).get('score')):>{width+2}}"
        print(row)
    print("")


def _markdown_report(slugs: list[str], cases: dict, out_path: Path) -> None:
    L: list[str] = []
    L.append("# Modellvergleich – Outputs Side-by-Side\n")
    L.append(f"Modelle: {', '.join('`'+s+'`' for s in slugs)}\n")
    L.append("> Der Eval-Score erkennt Regressionen (Quellentreue/Sektionen). Das "
             "eigentliche Signal ist die **Prosa** unten – lies 2–3 Cases pro Modell "
             "nebeneinander und beurteile Lesbarkeit, deutsche Fluenz und Treue.\n")

    # Score-Tabelle
    L.append("## Score-Übersicht\n")
    L.append("| Case | " + " | ".join(slugs) + " |")
    L.append("|" + "---|" * (len(slugs) + 1))
    for (wf, cid) in sorted(cases.keys()):
        cells = []
        for slug in slugs:
            e = cases[(wf, cid)].get(slug)
            cells.append(_fmt_pct(e["score"]).strip() if e else "–")
        L.append(f"| {wf}/{cid} | " + " | ".join(cells) + " |")
    L.append("")

    # Volle Outputs pro Case
    for (wf, cid) in sorted(cases.keys()):
        L.append(f"\n---\n\n## {wf} / {cid}\n")
        for slug in slugs:
            e = cases[(wf, cid)].get(slug)
            if not e:
                L.append(f"### `{slug}` — (kein Output)\n")
                continue
            status = "PASS" if e["pass"] else ("FAIL" if e["pass"] is False else "?")
            head = f"### `{slug}` — {status}, Score {_fmt_pct(e['score']).strip()}"
            if e["words"] is not None:
                head += f", {e['words']}w"
            L.append(head + "\n")
            if e["issues"]:
                L.append("Issues: " + "; ".join(e["issues"]) + "\n")
            L.append(e["text"] + "\n")

    out_path.write_text("\n".join(L), encoding="utf-8")
    print(f"Markdown-Report: {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True,
                    help="Wurzelverzeichnis mit je einem Unterordner pro Modell-Slug")
    ap.add_argument("--out", default=None,
                    help="Pfad fuer den Markdown-Report (default: <root>/model_comparison.md)")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        raise SystemExit(f"--root ist kein Verzeichnis: {root}")
    slugs, cases = _collect(root)
    if not slugs:
        raise SystemExit(f"Keine Modell-Unterordner in {root} gefunden.")
    if not cases:
        raise SystemExit(f"Keine Output-Dateien (.txt) in {root}/*/ gefunden.")

    _terminal_summary(slugs, cases)
    out_path = Path(args.out) if args.out else (root / "model_comparison.md")
    _markdown_report(slugs, cases, out_path)


if __name__ == "__main__":
    main()
