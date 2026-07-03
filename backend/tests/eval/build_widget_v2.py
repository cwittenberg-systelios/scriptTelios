#!/usr/bin/env python3
"""Confluence-Vergleichs-Widget aus Eval-Ergebnissen bauen (Runde 2).

Ersetzt das (nicht versionierte) build_widget.py der ersten Runde. Liest die
per-Modell-Output-Verzeichnisse (wie compare_models.py) und injiziert ein neues
`const DATA = {...};` in das bestehende Widget-HTML der Runde 1, das als
Template dient — Layout, Rating-System und Leaderboard bleiben unveraendert,
nur die Daten (Modelle, Faelle, Texte, Metriken) werden ausgetauscht.

Nutzung (im backend-Verzeichnis):
    python -m tests.eval.build_widget_v2 \
        --root /workspace/eval_results/model_cmp2 \
        --template /workspace/modellvergleich.html \
        --out /workspace/modellvergleich_v2.html \
        --logs /workspace/logs

Runtime pro Modell wird aus /workspace/logs/cmp_<slug>.log gelesen (pytest-
Schlusszeile "... in 8770.93s"); fehlt das Log, bleibt die Laufzeit leer.

WICHTIG fuers Rating: Der content-property-Key im Widget bleibt
`stx-model-ratings`. Damit sich die Bewertungen von Runde 1 und Runde 2 nicht
vermischen, haengt dieses Skript automatisch das Suffix `-v2` an (per
--rating-key aenderbar).
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

try:
    from tests.eval.compare_models import _collect
except ImportError:
    from compare_models import _collect

# ── Modell-Metadaten (Runde 2). Unbekannte Slugs bekommen Fallbacks. ──────────
# strength = neutrale Staerke-Beschreibung (KEIN Urteil - Bias-Vermeidung,
# gleiche Konvention wie Runde 1). statusQuo markiert NUR den Ist-Zustand.
MODELS_META = {
    "qwen3_32b": dict(
        name="Qwen3 32B", tag="qwen3:32b",
        strength="Breite Stilsteuerung über Therapeuten hinweg",
        statusQuo=True),
    "gemma3_27b": dict(
        name="Gemma 3 27B", tag="gemma3:27b",
        strength="Konsistente Struktur, natürliche deutsche Prosa",
        statusQuo=False),
    "gemma4_31b": dict(
        name="Gemma 4 31B", tag="gemma4:31b",
        strength="Neueste Gemma-Generation (Apache 2.0) · 256K-Kontext",
        statusQuo=False),
    "mistral-small3_2": dict(
        name="Mistral 3.2", tag="mistral-small3.2",
        strength="Knappe, sachliche Sprache · europäisches Modell (Apache 2.0) · 128K-Kontext",
        statusQuo=False),
    "mistral-small": dict(
        name="Mistral Small 3", tag="mistral-small",
        strength="Knappe, sachliche Sprache · europäisches Modell",
        statusQuo=False),
}

WF_LABELS = {
    "dokumentation": "Gesprächsdoku", "anamnese": "Anamnese",
    "akutantrag": "Akutantrag", "verlaengerung": "Verlängerung",
    "folgeverlaengerung": "Folgeverlängerung", "entlassbericht": "Entlassbericht",
}

_RUNTIME_RE = re.compile(r"in (\d+(?:\.\d+)?)s(?:\s*\(|\s*=|\s*$)")


def _runtime_from_log(logs: Path | None, slug: str) -> float | None:
    if not logs:
        return None
    p = logs / f"cmp_{slug}.log"
    if not p.exists():
        return None
    # letzte pytest-Zeitangabe im Log gewinnt
    hits = _RUNTIME_RE.findall(p.read_text(encoding="utf-8", errors="replace"))
    return float(hits[-1]) if hits else None


def _fmt_runtime(s: float | None) -> str:
    if s is None:
        return "–"
    h, rem = divmod(int(s), 3600)
    m = rem // 60
    return f"{h}h {m:02d}min" if h else f"{m}min"


def _style3(st: dict | None, which: str) -> dict:
    """-> {'satz','fachb','wir'} aus dem .style.json-Block (None-tolerant)."""
    src = ((st or {}).get(which)) or {}
    def g(k):
        v = src.get(k)
        return round(v, 2) if isinstance(v, (int, float)) else None
    return {"satz": g("avg_sentence_length"),
            "fachb": g("fachbegriff_density"),
            "wir": g("wir_perspektive_ratio")}


def _pretty_label(case_id: str, workflow: str) -> str:
    # "dok-01-familienkonflikt" -> "Familienkonflikt"
    parts = [p for p in case_id.split("-") if not p.isdigit()]
    # Workflow-Kuerzel-Praefix (dok/an/akut/va/fva/eb) abwerfen
    if parts and len(parts[0]) <= 4:
        parts = parts[1:]
    return " ".join(p.capitalize() for p in parts) or case_id


def build_data(root: Path, logs: Path | None) -> dict:
    slugs, cases = _collect(root)

    # ── Modelle ──
    models = []
    baseline_rt = None
    runtimes = {s: _runtime_from_log(logs, s) for s in slugs}
    for s in slugs:
        if MODELS_META.get(s, {}).get("statusQuo"):
            baseline_rt = runtimes.get(s)
    for slug in slugs:
        meta = MODELS_META.get(slug) or dict(
            name=slug, tag=slug.replace("_", ":"), strength="", statusQuo=False)
        per = [c[slug] for c in cases.values() if slug in c]
        scores = [e["score"] for e in per if e["score"] is not None]
        issues = sum(len(e["issues"]) for e in per)
        fidelity = sum(1 for e in per for i in e["issues"] if "QUELLENTREUE" in i.upper())
        rt = runtimes.get(slug)
        speedup = (round(baseline_rt / rt, 2)
                   if isinstance(rt, (int, float)) and isinstance(baseline_rt, (int, float)) and rt
                   else None)
        models.append({
            "slug": slug, **meta,
            "runtime_s": rt, "runtime": _fmt_runtime(rt), "speedup": speedup,
            "avg_score": round(sum(scores) / len(scores), 3) if scores else None,
            "issues": issues, "fidelity": fidelity,
            "steer": None,   # Stilsteuerbarkeit: erst nach CMP_STYLE_VAR-Lauf
        })
    models.sort(key=lambda m: m["tag"])

    # ── Faelle ──
    out_cases = []
    for (wf, cid) in sorted(cases.keys()):
        per_model = cases[(wf, cid)]
        any_ev = next(iter(per_model.values()))
        ref = _style3(any_ev.get("style"), "reference")
        outputs = {}
        for slug, ev in per_model.items():
            outputs[slug] = {
                "pass": bool(ev["pass"]),
                "words": ev["words"] if isinstance(ev["words"], int) else len(ev["text"].split()),
                "score": round(ev["score"], 3) if ev["score"] is not None else None,
                "issues": ev["issues"],
                "style": _style3(ev.get("style"), "output"),
                "text": ev["text"],
            }
        out_cases.append({
            "id": cid, "wf": wf,
            "wfLabel": WF_LABELS.get(wf, wf.capitalize()),
            "label": _pretty_label(cid, wf),
            "ref": ref, "outputs": outputs,
        })

    return {"generated": time.strftime("%Y-%m-%d %H:%M"),
            "models": models, "cases": out_cases}


def inject(template: Path, data: dict, out: Path, rating_key_suffix: str) -> None:
    html = template.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    new_html, n = re.subn(
        r"const DATA\s*=\s*\{.*?\};",
        lambda m: f"const DATA = {payload};",
        html, count=1, flags=re.S,
    )
    if n != 1:
        raise SystemExit("Template-Anker 'const DATA = {...};' nicht gefunden!")
    # Ratings der Runde 2 getrennt speichern (eigener content-property-Key)
    if rating_key_suffix:
        new_html = new_html.replace("stx-model-ratings",
                                    f"stx-model-ratings{rating_key_suffix}")
    out.write_text(new_html, encoding="utf-8")
    kb = out.stat().st_size / 1024
    print(f"Widget geschrieben: {out}  ({kb:.0f} kB, "
          f"{len(data['models'])} Modelle, {len(data['cases'])} Fälle)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--template", required=True, type=Path,
                    help="Widget-HTML der Runde 1 (modellvergleich.html)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--logs", type=Path, default=None,
                    help="Log-Verzeichnis mit cmp_<slug>.log (Runtimes)")
    ap.add_argument("--rating-key", default="-v2",
                    help="Suffix fuer den Rating-Property-Key (default: -v2; "
                         "leer = Ratings von Runde 1 weiterverwenden)")
    args = ap.parse_args()
    data = build_data(args.root, args.logs)
    inject(args.template, data, args.out, args.rating_key)


if __name__ == "__main__":
    main()
