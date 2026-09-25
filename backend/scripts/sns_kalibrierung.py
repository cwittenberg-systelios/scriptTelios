#!/usr/bin/env python3
"""
Kalibrierung der Dynamischen Komplexitaet gegen den SNS-Export (v19.41, D6=B).

Vergleicht unsere DK-Werte (services/sns_verlauf.dynamic_complexity) je Item
und Tag mit den von SNS exportierten Komplexitaetswerten derselben Klient:in.

Ergebnis der Kalibrierung vom 25.09.2026 (WJ28718IND, HSF + individueller
Bogen, 728 Werte): exakte Reproduktion (max. rel. Abweichung 0,07 %, das ist
die Rundung im SNS-Druck). Die dabei bestimmten SNS-Regeln sind im Kern
umgesetzt:
  - Fenster 7 ueber die Folge der tatsaechlichen Messwerte (x-Tage entfallen,
    keine Interpolation); SNS datiert den Wert auf den ERSTEN Fenstertag
    (unsere Darstellung: letzter Fenstertag, label='end').
  - F: Umkehrpunkte = Vorzeichenwechsel zwischen Differenzen != 0, am Ende
    eines Plateaus; Amplitude / Abstand; Normierung s*(m-1) mit s = Itemskala.
  - D: sortierte Werte gegen Gleichverteilung ueber die ITEMSKALA (HSF-Items
    7-19 haben min=1), alle Teilintervalle, absolute positive Abweichungen.

SNS-Export erzeugen: Fragebogen -> Analyse -> Resonanz-Diagramme ->
"Komplexitaets-Resonanz-Diagramm (in Farbe)", Fenster 7 -> Drucken -> als PDF
speichern. Das PDF enthaelt je Item die Werte pro Datum; das Skript liest es
mit pdftotext (poppler) oder direkt eine damit erzeugte .txt.

Aufruf (im Pod: source /workspace/venv/bin/activate, aus backend/):
  python scripts/sns_kalibrierung.py --raw hsf.csv --sns KomplexBasisHSF.pdf [--xml Fragebogen.xml]
        [--window 7] [--freeze report.json]
Ohne --xml wird fuer "HSF kurz Basis" das Repo-XML genommen, sonst Skala 0-100.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import sns_verlauf as sv  # noqa: E402


def read_sns_export(path: Path) -> dict[str, dict[dt.date, float]]:
    """{itemtext: {datum: dk}} aus dem SNS-Druck (PDF oder pdftotext-Layout-Text)."""
    if path.suffix.lower() == ".pdf":
        if not shutil.which("pdftotext"):
            raise SystemExit("pdftotext (poppler-utils) fehlt - PDF vorher in Text wandeln")
        txt = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True,
                             text=True, check=True).stdout
    else:
        txt = path.read_text(encoding="utf-8", errors="replace")
    items: dict[str, dict[dt.date, float]] = {}
    cur = None
    for ln in txt.splitlines():
        m = re.match(r"\s*(\d{1,2}) - (.+?)\s*$", ln)
        if m and "Werte" not in ln:
            cur = m.group(2)
            items.setdefault(cur, {})
            continue
        m = re.match(r"\s*(\d{2})[/.](\d{2})[/.](\d{4})\s+(-?[\d.,]+)\s*$", ln)
        if m and cur is not None:
            d = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            items[cur][d] = float(m.group(4).replace(",", "."))
    return {k: v for k, v in items.items() if v}


def kalibriere(raw_path: Path, sns_path: Path, window: int = 7, z: float = 1.645,
               horizon: int | None = None, xml_path: Path | None = None) -> dict:
    s = sv.parse_sns_csv(raw_path.read_text(encoding="utf-8-sig", errors="replace"))
    fb = None
    if xml_path:
        fb = sv.parse_sns_questionnaire_xml(xml_path.read_text(encoding="utf-8"))
    elif sv.HSF_QUESTIONNAIRE_NAME.lower() in (s.questionnaire or "").lower():
        fb = sv.load_hsf_basis()
    mapping = sv.match_csv_items(s.items, fb) if fb else {}
    days = [s.dates[0] + dt.timedelta(k) for k in range((s.dates[-1] - s.dates[0]).days + 1)]
    idx = {d: i for i, d in enumerate(days)}
    X = np.full((len(days), len(s.items)), np.nan)
    for d, row in zip(s.dates, s.values, strict=True):
        X[idx[d]] = row
    sns = read_sns_export(sns_path)
    pseudo = sv.SnsQuestionnaire("csv", {}, [sv.SnsQuestion(t, [], [], "", "") for t in s.items])
    report = {"window": window, "z": z, "horizon": horizon, "quelle": str(sns_path.name),
              "items": [], "unmatched_sns": []}
    errs_all: list[float] = []
    krit_ok: list[bool] = []
    rhos: list[float] = []
    for title, vals in sns.items():
        hits = sv.match_csv_items([title], pseudo)
        if 0 not in hits:
            report["unmatched_sns"].append(title)
            continue
        k = hits[0]
        lo, hi = fb.fragen[mapping[k]].skala if (fb and k in mapping) else (0.0, 100.0)
        ours = sv.dynamic_complexity(X[:, k], window, lo, hi, label="start")
        theirs = np.full(len(days), np.nan)
        for d, v in vals.items():
            if d in idx:
                theirs[idx[d]] = v
        both = np.isfinite(ours) & np.isfinite(theirs)
        n = int(both.sum())
        if n == 0:
            report["items"].append({"item": title, "n": 0, "hinweis": "keine gemeinsamen Tage"})
            continue
        rel = np.abs(ours[both] - theirs[both]) / np.where(theirs[both] > 0, theirs[both], 1.0)
        errs_all.extend(rel.tolist())
        rho = sv.spearman(ours, theirs) if np.nanstd(theirs[both]) > 0 else 1.0
        k_ours = sv.dk_critical_ci(np.where(both, ours, np.nan), z, 5, horizon)
        k_theirs = sv.dk_critical_ci(np.where(both, theirs, np.nan), z, 5, horizon)
        gleich = bool(np.array_equal(k_ours, k_theirs))
        krit_ok.append(gleich)
        rhos.append(rho if np.isfinite(rho) else 1.0)
        report["items"].append({
            "item": title, "n": n, "rho": round(float(rho), 4) if np.isfinite(rho) else None,
            "rel_fehler_max": round(float(rel.max()), 5), "rel_fehler_median": round(float(np.median(rel)), 6),
            "kritische_tage_gleich": gleich,
            "abweichende_kritische_tage": [days[t].isoformat() for t in range(len(days)) if k_ours[t] != k_theirs[t]],
        })
    if errs_all:
        emax = float(max(errs_all))
        report["gesamt"] = {"werte": len(errs_all), "rel_fehler_max": round(emax, 5),
                            "rel_fehler_median": round(float(np.median(errs_all)), 6),
                            "rho_min": round(float(min(rhos)), 4),
                            "items_kritisch_gleich": f"{sum(krit_ok)}/{len(krit_ok)}",
                            "akzeptiert": bool(emax < 0.01 and all(krit_ok))}
    else:
        report["gesamt"] = {"akzeptiert": False, "hinweis": "keine vergleichbaren Werte"}
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", required=True, type=Path, help="SNS-Zeitreihen-CSV (Rohwerte)")
    ap.add_argument("--sns", required=True, type=Path, help="SNS-Druck des KRD (PDF oder pdftotext -layout Text)")
    ap.add_argument("--xml", type=Path, default=None, help="Fragebogen-XML (Itemskalen); Default: HSF aus dem Repo")
    ap.add_argument("--window", type=int, default=7)
    ap.add_argument("--z", type=float, default=1.645)
    ap.add_argument("--horizon", type=int, default=None)
    ap.add_argument("--freeze", type=Path, default=None, help="Report als JSON speichern")
    args = ap.parse_args()
    rep = kalibriere(args.raw, args.sns, args.window, args.z, args.horizon, args.xml)
    print(f"Fenster {rep['window']}, z {rep['z']}, Gedächtnis {rep['horizon'] or 'alle'}, Quelle {rep['quelle']}")
    for it in rep["items"]:
        if it.get("n", 0) == 0:
            print(f"  – {it['item'][:52]:52s}  {it.get('hinweis', '')}")
            continue
        flag = "ok " if it["rel_fehler_max"] < 0.01 and it["kritische_tage_gleich"] else "!! "
        print(f"  {flag}{it['item'][:52]:52s} n={it['n']:3d} relFehler max {it['rel_fehler_max']:.5f} "
              f"rho={it['rho']}  krit={'gleich' if it['kritische_tage_gleich'] else it['abweichende_kritische_tage']}")
    if rep["unmatched_sns"]:
        print("Nicht zugeordnet (SNS):", "; ".join(t[:40] for t in rep["unmatched_sns"]))
    print("GESAMT:", json.dumps(rep["gesamt"], ensure_ascii=False))
    if args.freeze:
        args.freeze.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print("gespeichert:", args.freeze)
    return 0 if rep["gesamt"].get("akzeptiert") else 1


if __name__ == "__main__":
    sys.exit(main())
