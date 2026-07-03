#!/usr/bin/env python3
"""Routing-Report: Modellvergleich entlang der EINSATZ-Achse.

Motivation (Vergleichsrunde 2, Juli 2026): Die Modellentscheidung ist keine
Entweder-Oder-Frage mehr, sondern eine ROUTING-Frage:

    Kassenkommunikation  (akutantrag, verlaengerung, folgeverlaengerung)
        -> knapp, klar, sachlich, hausduktus-nah        (Kandidat: mistral)
    Klinische Dokumente  (dokumentation, anamnese, entlassbericht)
        -> hypnosystemisch, ressourcenorientiert         (Kandidat: gemma)

Dieses Skript liest dieselben per-Modell-Output-Verzeichnisse wie
compare_models.py (nach run_model_eval.sh) und aggregiert pro Modell und
Workflow-GRUPPE:

    - Score (Regressions-Floor, nur grob)
    - Issues gesamt / davon Quellentreue-Verstoesse / fehlende Sektionen
    - Woerter (Verbositaet)
    - Stil-Abweichung von der Haus-Referenz (Satzlaenge, Fachbegriffe, Wir)
    - Trunkierungs-Indikator (Text endet nicht auf Satzzeichen)
    - Schachtelsatz-Indikator (Saetze > 55 Woerter)

Nutzung (im backend-Verzeichnis):
    python -m tests.eval.routing_report --root /workspace/eval_results/model_cmp2
    python -m tests.eval.routing_report --root <dir> --out /tmp/routing.md
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

# Parser aus compare_models wiederverwenden (gleiche Datenlage, kein Drift)
try:
    from tests.eval.compare_models import _collect, _mean  # als Modul (python -m)
except ImportError:
    from compare_models import _collect, _mean             # direkt im Verzeichnis

GROUPS = {
    "Kassenkommunikation": ["akutantrag", "verlaengerung", "folgeverlaengerung"],
    "Klinische Dokumente":  ["dokumentation", "anamnese", "entlassbericht"],
}

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_FIDELITY = re.compile(r"QUELLENTREUE", re.I)
_SECTION = re.compile(r"Sektion fehlt", re.I)


def _text_quality_flags(text: str) -> dict:
    t = (text or "").rstrip()
    truncated = bool(t) and not t.endswith((".", "!", "?", "\u201c", '"', ")"))
    runons = sum(1 for s in _SENT_SPLIT.split(t) if len(s.split()) > 55)
    return {"truncated": truncated, "runons": runons}


def _style_delta(style: dict | None) -> dict:
    """Absolute Abweichung Output vs. Haus-Referenz je Metrik (None-sicher)."""
    if not style:
        return {}
    out, ref = style.get("output") or {}, style.get("reference") or {}
    delta = {}
    for k in ("avg_sentence_length", "fachbegriff_density", "wir_perspektive_ratio"):
        o, r = out.get(k), ref.get(k)
        if isinstance(o, (int, float)) and isinstance(r, (int, float)):
            delta[k] = abs(o - r)
    return delta


def build_report(root: Path) -> tuple[str, str]:
    slugs, cases = _collect(root)
    lines_term: list[str] = []
    lines_md: list[str] = ["# Routing-Report Modellvergleich", "",
                           f"Quelle: `{root}`", ""]

    for group, wfs in GROUPS.items():
        lines_term.append("")
        lines_term.append("=" * 78)
        lines_term.append(f"  {group.upper()}   ({', '.join(wfs)})")
        lines_term.append("=" * 78)
        header = (f"  {'Modell':<16}{'Ø-Score':>8}{'Issues':>7}{'Fidel.':>7}"
                  f"{'Sekt.':>6}{'Ø-Wörter':>9}{'trunk.':>7}{'>55w':>5}"
                  f"{'ΔSatz':>7}{'ΔFachb':>7}{'ΔWir':>6}")
        lines_term.append(header)
        lines_md += [f"## {group}", "",
                     "| Modell | Ø-Score | Issues | Quellentreue | Sektionen | Ø-Wörter | trunkiert | Sätze>55w | ΔSatzlänge | ΔFachbegr | ΔWir |",
                     "|---|---|---|---|---|---|---|---|---|---|---|"]

        for slug in slugs:
            scores, words, iss, fid, sec, trunc, runons = [], [], 0, 0, 0, 0, 0
            d_satz, d_fachb, d_wir = [], [], []
            n = 0
            for (wf, cid), per_model in cases.items():
                if wf not in wfs or slug not in per_model:
                    continue
                ev = per_model[slug]
                n += 1
                scores.append(ev["score"])
                if isinstance(ev.get("words"), int):
                    words.append(ev["words"])
                iss += len(ev["issues"])
                fid += sum(1 for i in ev["issues"] if _FIDELITY.search(i))
                sec += sum(1 for i in ev["issues"] if _SECTION.search(i))
                fl = _text_quality_flags(ev.get("text", ""))
                trunc += int(fl["truncated"]); runons += fl["runons"]
                dl = _style_delta(ev.get("style"))
                if "avg_sentence_length" in dl:  d_satz.append(dl["avg_sentence_length"])
                if "fachbegriff_density" in dl:  d_fachb.append(dl["fachbegriff_density"])
                if "wir_perspektive_ratio" in dl: d_wir.append(dl["wir_perspektive_ratio"])
            if n == 0:
                continue
            f = lambda v, spec=".2f": (format(v, spec) if v is not None else "–")
            avg_s = _mean(scores); avg_w = _mean(words)
            row = (f"  {slug:<16}{f(avg_s):>8}{iss:>7}{fid:>7}{sec:>6}"
                   f"{f(avg_w, '.0f'):>9}{trunc:>5}/{n:<2}{runons:>4}"
                   f"{f(_mean(d_satz), '.1f'):>7}{f(_mean(d_fachb), '.2f'):>7}"
                   f"{f(_mean(d_wir), '.2f'):>6}")
            lines_term.append(row)
            lines_md.append(
                f"| {slug} | {f(avg_s)} | {iss} | {fid} | {sec} | {f(avg_w, '.0f')} "
                f"| {trunc}/{n} | {runons} | {f(_mean(d_satz), '.1f')} "
                f"| {f(_mean(d_fachb), '.2f')} | {f(_mean(d_wir), '.2f')} |")
        lines_md.append("")

    lines_md += [
        "## Lesart",
        "",
        "- **Ø-Score** ist ein Regressions-Floor (Checks bestanden / (bestanden+Issues)), "
        "kein Qualitätsranking — das eigentliche Signal ist die Prosa.",
        "- **Quellentreue** = Anzahl `QUELLENTREUE:`-Issues (aufgestülpte Fachbegriffe).",
        "- **Δ-Werte** = mittlere absolute Abweichung von der Haus-Referenz "
        "(Stilvorlagen) — kleiner ist näher am Hausduktus.",
        "- **trunkiert / Sätze>55w** = deterministische Textqualitäts-Indikatoren "
        "(Abbrüche, Schachtelsätze) direkt aus dem Output.",
        "",
        "Routing-Entscheidung: pro Gruppe das Modell mit der besten Kombination aus "
        "niedriger Fidelity, geringer Stil-Abweichung und sauberer Textqualität "
        "wählen — Score nur als Ausschlusskriterium bei Einbrüchen.",
    ]
    return "\n".join(lines_term), "\n".join(lines_md)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None,
                    help="Markdown-Ziel (default: <root>/routing_report.md)")
    args = ap.parse_args()
    term, md = build_report(args.root)
    print(term)
    out = args.out or (args.root / "routing_report.md")
    out.write_text(md, encoding="utf-8")
    print(f"\nMarkdown-Report: {out}")


if __name__ == "__main__":
    main()
