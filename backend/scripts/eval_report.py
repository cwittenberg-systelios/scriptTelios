#!/usr/bin/env python3
"""
scriptTelios Eval-Report Generator
====================================
Erzeugt PDF-Report mit Diagrammen aus Evaluations-Ergebnissen.
Nutzt ausschließlich reportlab (kein matplotlib).

    python scripts/eval_report.py /workspace/eval_results/
    python scripts/eval_report.py /workspace/eval_results/ -o report.pdf

Oder automatisch nach pytest:
    pytest tests/test_eval.py -v --eval-report
"""
import argparse, json, re, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether,
)
from reportlab.graphics.shapes import Drawing, Rect, String, Line

C = {
    "red": colors.HexColor("#8b1a1a"), "red_lt": colors.HexColor("#c44d4d"),
    "dark": colors.HexColor("#2c2c2c"), "gray": colors.HexColor("#999999"),
    "grid": colors.HexColor("#dddddd"), "green": colors.HexColor("#2d7a3a"),
    "green_lt": colors.HexColor("#a8d5b0"), "orange": colors.HexColor("#c47d1a"),
    "blue": colors.HexColor("#1a5c8b"), "cream": colors.HexColor("#f8f6f2"),
    "purple": colors.HexColor("#7b1fa2"), "teal": colors.HexColor("#00695c"),
}

# v13: Workflow-Farben und -Labels kommen aus dem zentralen WORKFLOWS-Modul,
# wenn es importierbar ist. Skript laeuft auch standalone (ohne app/-Paket im
# PYTHONPATH) - dann faellt es auf hardcoded Werte zurueck. Sync-Test in
# test_suite.py vergleicht beide Varianten.
try:
    # Versuche app/-Paket zu finden (typischerweise via PYTHONPATH oder wenn
    # das Skript aus dem Backend-Root gestartet wird).
    import sys as _sys
    from pathlib import Path as _Path
    _backend_root = _Path(__file__).resolve().parent.parent  # scripts/.. = backend/
    if str(_backend_root) not in _sys.path:
        _sys.path.insert(0, str(_backend_root))
    from app.core.workflows import WORKFLOWS as _WORKFLOWS
    WF_COL = {w.key: colors.HexColor(w.color_hex) for w in _WORKFLOWS}
    WF_LBL = {w.key: w.short_label for w in _WORKFLOWS}
except ImportError:
    # Fallback: hardcoded. MUSS bei Aenderung von WORKFLOWS manuell synchronisiert
    # werden - sync-test in test_suite.py wacht darueber.
    WF_COL = {"entlassbericht": C["red"], "verlaengerung": C["blue"],
              "folgeverlaengerung": C["orange"], "anamnese": C["green"],
              "dokumentation": C["dark"], "akutantrag": C["purple"]}
    WF_LBL = {"entlassbericht": "Entlassbericht", "verlaengerung": "Verlängerung",
               "folgeverlaengerung": "Folgeverlängerung", "anamnese": "Anamnese",
               "dokumentation": "Gesprächsdoku", "akutantrag": "Akutantrag"}

def load_eval_results(d: Path) -> dict:
    results = defaultdict(list)
    for wd in sorted(d.iterdir()):
        if not wd.is_dir() or wd.name.startswith(".") or wd.name in ("style_variance","style_jury"):
            continue
        wf = wd.name
        for ef in sorted(wd.glob("*.eval.txt")):
            tid = ef.stem.replace(".eval","")
            e = {"test_id":tid,"workflow":wf,"word_count":0,"passed":0,"issues":0,
                 "score":0.0,"status":"?","issue_list":[],"style_metrics":None,
                 "jury_score":None,"jury_reason":None,"composite":0.0,
                 # v13 Ä5: Längenquellen-Telemetrie
                 "length_source":None, "length_min":0, "length_max":0,
                 # v19.1: Generierungs-Telemetrie (Think-Block-Diagnose, Retry)
                 "gen_degraded": False, "gen_retry_used": False,
                 "gen_telemetry": None}
            for ln in ef.read_text("utf-8").split("\n"):
                ln = ln.strip()
                if ln.startswith("[PASS]") or ln.startswith("[FAIL]"):
                    e["status"] = "PASS" if "[PASS]" in ln else "FAIL"
                    m = re.search(r"\((\d+)w\)", ln)
                    if m: e["word_count"] = int(m.group(1))
                elif "Checks bestanden" in ln:
                    m = re.search(r"(\d+)\s+Checks", ln)
                    if m: e["passed"] = int(m.group(1))
                elif "Probleme:" in ln:
                    m = re.search(r"(\d+)\s+Probleme", ln)
                    if m: e["issues"] = int(m.group(1))
                elif ln.startswith("Längenanker:"):
                    # v13 Ä5: "Längenanker: 400-600w (Quelle: Stilvorlage(n) (n=2))"
                    m = re.match(
                        r"Längenanker:\s*(\d+)-(\d+)w\s*\(Quelle:\s*(.+?)\)\s*$",
                        ln,
                    )
                    if m:
                        e["length_min"] = int(m.group(1))
                        e["length_max"] = int(m.group(2))
                        e["length_source"] = m.group(3).strip()
                elif ln.startswith("- "): e["issue_list"].append(ln[2:])
            tot = e["passed"]+e["issues"]
            e["score"] = e["passed"]/tot if tot else 0.0
            tf = wd/f"{tid}.txt"
            if tf.exists(): e["word_count"] = len(tf.read_text("utf-8").split())
            sf = wd/f"{tid}.style.json"
            if sf.exists():
                try: e["style_metrics"] = json.loads(sf.read_text("utf-8"))
                except: pass
            # v19.1: Generierungs-Telemetrie (Think-Block-Diagnose, Retry-Status).
            # Geschrieben vom test_eval.py-Patch wenn das LLM-Result entsprechende
            # Felder lieferte; fehlt bei Pre-v19.1-Ergebnissen.
            tf2 = wd/f"{tid}.telemetry.json"
            if tf2.exists():
                try:
                    _td = json.loads(tf2.read_text("utf-8"))
                    e["gen_degraded"]   = bool(_td.get("degraded"))
                    e["gen_retry_used"] = bool(_td.get("retry_used"))
                    e["gen_telemetry"]  = _td.get("telemetry") or {}
                except Exception:
                    pass
            # Referenz- und Output-Text fuer 2-spaltigen Vergleich
            rf = wd/f"{tid}.ref.txt"
            if rf.exists(): e["ref_text"] = rf.read_text("utf-8")
            if tf.exists(): e["output_text"] = tf.read_text("utf-8")
            results[wf].append(e)
    var, jury = [], []
    vd = d/"style_variance"
    if vd.exists():
        for f in sorted(vd.glob("*.json")):
            try: var.append(json.loads(f.read_text("utf-8")))
            except: pass
    jd = d/"style_jury"
    if jd.exists():
        for f in sorted(jd.glob("*.jury.json")):
            try: jury.append(json.loads(f.read_text("utf-8")))
            except: pass

    # P6: Jury-Scores in die Test-Eintraege einmappen, Composite berechnen,
    # Status nochmal pruefen (Jury <3 -> FAIL, auch wenn Regex-Checks PASS waren).
    # Match-Strategie: jury-record enthaelt test_id und workflow.
    jury_by_id: dict[str, dict] = {}
    for jr in jury:
        tid = jr.get("test_id") or jr.get("testcase_id") or ""
        wf = jr.get("workflow", "")
        # Schluessel "<workflow>/<test_id>" um Kollisionen zwischen Workflows
        # zu vermeiden (test_id kann theoretisch dupliziert sein).
        key = f"{wf}/{tid}" if wf else tid
        if key:
            jury_by_id[key] = jr

    for wf, entries in results.items():
        for e in entries:
            key = f"{wf}/{e['test_id']}"
            jr = jury_by_id.get(key) or jury_by_id.get(e["test_id"])
            if jr:
                sc = jr.get("score")
                if isinstance(sc, (int, float)):
                    e["jury_score"] = float(sc)
                    e["jury_reason"] = jr.get("reason", "") or jr.get("begruendung", "")
            # Composite-Score: 0.7 * Regex-Score + 0.3 * Jury-Score (normiert auf 0-1).
            # Wenn keine Jury vorliegt: nur Regex-Score (= bisheriges Verhalten).
            if e["jury_score"] is not None:
                e["composite"] = round(0.7 * e["score"] + 0.3 * (e["jury_score"] / 5.0), 4)
                # Jury-Failures sollen sich auch im Pass/Fail niederschlagen:
                # Score < 3/5 = harter Fail, auch wenn Regex grün war.
                if e["jury_score"] < 3 and e["status"] == "PASS":
                    e["status"] = "FAIL"
                    e["issue_list"].append(
                        f"STIL-JURY: niedrige Bewertung ({e['jury_score']:.0f}/5) - "
                        f"{(e['jury_reason'] or '')[:120]}"
                    )
                    e["issues"] += 1
            else:
                e["composite"] = e["score"]

    return {"workflows": dict(results), "variance": var, "jury": jury}

def _hbar(title, labels, values, bcols, w=460, bh=18, ref=None, rl=None, sfx="%", mx=None):
    lw, cw = 140, w-150
    n = len(labels); gap = 5; h = n*(bh+gap)+40
    d = Drawing(w, h)
    d.add(String(w/2, h-12, title, textAnchor="middle", fontSize=10,
                 fontName="Helvetica-Bold", fillColor=C["dark"]))
    if mx is None: mx = max(values)*1.15 if values and max(values)>0 else 100
    y0 = h-30
    for i,(lb,v,cl) in enumerate(zip(labels,values,bcols)):
        y = y0-i*(bh+gap)
        d.add(String(lw-4, y+bh/2-4, lb, textAnchor="end", fontSize=7, fillColor=C["dark"]))
        bw = max((v/mx)*cw,1) if mx else 1
        d.add(Rect(lw, y, bw, bh, fillColor=cl, strokeColor=colors.white, strokeWidth=.5))
        d.add(String(lw+bw+3, y+bh/2-4, f"{v:.0f}{sfx}", fontSize=7, fillColor=C["dark"]))
    if ref and mx:
        rx = lw+(ref/mx)*cw
        d.add(Line(rx,y0+bh+2,rx,y0-n*(bh+gap)+bh,strokeColor=C["red_lt"],strokeWidth=.8,strokeDashArray=[3,3]))
        if rl: d.add(String(rx+2,y0+bh+4,rl,fontSize=6,fillColor=C["red_lt"]))
    return d

def _stacked(title, labels, pv, iv, w=400):
    n = len(labels); bw = min(40,(w-60)/n-8); ch = 140; h = ch+60
    d = Drawing(w, h)
    d.add(String(w/2,h-12,title,textAnchor="middle",fontSize=10,
                 fontName="Helvetica-Bold",fillColor=C["dark"]))
    mx = max(p+i for p,i in zip(pv,iv)) if pv else 1
    x0, y0 = 50, 30
    for i,(lb,p,iv2) in enumerate(zip(labels,pv,iv)):
        x = x0+i*(bw+12)
        ph = (p/mx)*ch; ih = (iv2/mx)*ch
        d.add(Rect(x,y0,bw,ph,fillColor=C["green"],strokeColor=colors.white,strokeWidth=.5))
        d.add(Rect(x,y0+ph,bw,ih,fillColor=C["red_lt"],strokeColor=colors.white,strokeWidth=.5))
        d.add(String(x+bw/2,y0-14,lb,textAnchor="middle",fontSize=7,fillColor=C["dark"]))
        d.add(String(x+bw/2,y0+ph+ih+3,str(p+iv2),textAnchor="middle",fontSize=7,fillColor=C["dark"]))
    d.add(Rect(w-100,h-28,8,8,fillColor=C["green"],strokeWidth=0))
    d.add(String(w-88,h-27,"Bestanden",fontSize=6,fillColor=C["dark"]))
    d.add(Rect(w-100,h-40,8,8,fillColor=C["red_lt"],strokeWidth=0))
    d.add(String(w-88,h-39,"Probleme",fontSize=6,fillColor=C["dark"]))
    return d

def _issue_bars(title, labels, sizes, icols, w=320):
    n = len(labels); h = n*22+40; mx = max(sizes) if sizes else 1; bmax = w-130
    d = Drawing(w, h)
    d.add(String(w/2,h-12,title,textAnchor="middle",fontSize=10,
                 fontName="Helvetica-Bold",fillColor=C["dark"]))
    tot = sum(sizes) or 1
    for i,(lb,sz,cl) in enumerate(zip(labels,sizes,icols)):
        y = h-34-i*22
        bw = max((sz/mx)*bmax,2)
        d.add(Rect(105,y,bw,16,fillColor=cl,strokeWidth=0))
        d.add(String(100,y+4,lb,textAnchor="end",fontSize=7,fillColor=C["dark"]))
        d.add(String(105+bw+4,y+4,f"{sz} ({sz/tot*100:.0f}%)",fontSize=7,fillColor=C["dark"]))
    return d

def build_report(data: dict, out: Path, charts_dir: Path = None):
    doc = SimpleDocTemplate(str(out),pagesize=A4,leftMargin=2*cm,rightMargin=2*cm,
                            topMargin=2*cm,bottomMargin=2*cm)
    st = getSampleStyleSheet()
    st.add(ParagraphStyle("T",parent=st["Title"],fontSize=20,textColor=C["red"],spaceAfter=4*mm))
    st.add(ParagraphStyle("Sub",parent=st["Normal"],fontSize=10,textColor=C["gray"],spaceAfter=8*mm))
    st.add(ParagraphStyle("H2",parent=st["Heading2"],fontSize=13,textColor=C["dark"],
                           spaceBefore=6*mm,spaceAfter=3*mm))
    st.add(ParagraphStyle("B",parent=st["Normal"],fontSize=9,leading=13,textColor=C["dark"]))
    st.add(ParagraphStyle("Iss",parent=st["Normal"],fontSize=8,leading=11,
                           textColor=C["red_lt"],leftIndent=10*mm))
    story = []
    ae = [e for wf in data["workflows"].values() for e in wf]
    tot = len(ae)
    passed = sum(1 for e in ae if e["status"]=="PASS")
    avg_sc = sum(e["score"] for e in ae)/tot*100 if tot else 0
    avg_wc = sum(e["word_count"] for e in ae)/tot if tot else 0
    # P6: Composite + Jury-Coverage als Zusatzkennzahlen
    avg_comp = sum(e.get("composite", e["score"]) for e in ae)/tot*100 if tot else 0
    jury_cov = [e for e in ae if e.get("jury_score") is not None]
    avg_jury = (sum(e["jury_score"] for e in jury_cov)/len(jury_cov)) if jury_cov else None

    # Title
    story.append(Spacer(1,2*cm))
    story.append(Paragraph("scriptTelios Evaluations-Report",st["T"]))
    story.append(Paragraph(f"Generiert: {datetime.now().strftime('%d.%m.%Y %H:%M')} · "
        f"{len(data['workflows'])} Workflows · {tot} Testfälle",st["Sub"]))
    rows = [["",""],["Testfälle",str(tot)],
        ["Bestanden",f"{passed}/{tot} ({passed/tot*100:.0f}%)" if tot else "–"],
        ["Ø Score (Regex)",f"{avg_sc:.1f}%"],
        ["Ø Composite",f"{avg_comp:.1f}%"]]
    if avg_jury is not None:
        rows.append(["Ø LLM-Jury",f"{avg_jury:.1f}/5  ({len(jury_cov)}/{tot} Coverage)"])
    rows.extend([
        ["Ø Wörter",f"{avg_wc:.0f}"],
        ["Workflows",", ".join(WF_LBL.get(w,w) for w in data["workflows"])]
    ])
    t = Table(rows,colWidths=[5*cm,11*cm])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),C["dark"]),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTSIZE",(0,0),(-1,-1),9),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("GRID",(0,0),(-1,-1),.5,C["grid"]),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,C["cream"]]),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),("TOPPADDING",(0,0),(-1,-1),4)]))
    story.append(t); story.append(PageBreak())

    # Charts page
    story.append(Paragraph("Qualitäts-Scores",st["H2"]))
    lbs = [e["test_id"].split("-",1)[-1][:25] for e in ae]
    # P6: Composite-Score (Regex + Jury) als Hauptchart, mit Jury-Faehnchen.
    story.append(_hbar("Composite-Score pro Testfall (70% Regex + 30% Jury)",
        lbs,[e.get("composite", e["score"])*100 for e in ae],
        [WF_COL.get(e["workflow"],C["gray"]) for e in ae],ref=70,rl="Min 70%",mx=105))
    story.append(Spacer(1,4*mm))
    # Legend
    lg = "  ".join(f'<font color="{WF_COL.get(w,C["gray"]).hexval()}">\u2588\u2588</font> {WF_LBL.get(w,w)}'
                   for w in data["workflows"])
    story.append(Paragraph(lg,ParagraphStyle("lg",parent=st["Normal"],fontSize=8,alignment=TA_CENTER)))
    story.append(Spacer(1,6*mm))

    story.append(Paragraph("Output-Länge",st["H2"]))
    story.append(_hbar("Wörter pro Testfall",lbs,[e["word_count"] for e in ae],
        [WF_COL.get(e["workflow"],C["gray"]) for e in ae],sfx="w"))
    story.append(PageBreak())

    # Pass/Fail
    story.append(Paragraph("Checks pro Workflow",st["H2"]))
    wfs = list(data["workflows"].keys())
    story.append(_stacked("Bestanden vs. Probleme",
        [WF_LBL.get(w,w)[:12] for w in wfs],
        [sum(e["passed"] for e in data["workflows"][w]) for w in wfs],
        [sum(e["issues"] for e in data["workflows"][w]) for w in wfs]))
    story.append(Spacer(1,8*mm))

    # Issues
    cats = defaultdict(int)
    cm2 = {"Datenschutz":colors.HexColor("#d32f2f"),"Halluzination":colors.HexColor("#e64a19"),
        "Länge":C["orange"],"Keywords":C["blue"],"Format":C["gray"],
        "Struktur":C["purple"],"Stil":C["teal"],"Sonstige":colors.HexColor("#9e9e9e")}
    for e in ae:
        for iss in e.get("issue_list",[]):
            if "DATENSCHUTZ" in iss: cats["Datenschutz"]+=1
            elif "HALLUZINATION" in iss: cats["Halluzination"]+=1
            elif "kurz" in iss or "lang" in iss: cats["Länge"]+=1
            elif "Keyword" in iss: cats["Keywords"]+=1
            elif "Pattern" in iss: cats["Format"]+=1
            elif "Sektion" in iss: cats["Struktur"]+=1
            elif "STIL" in iss: cats["Stil"]+=1
            else: cats["Sonstige"]+=1
    if cats:
        story.append(Paragraph("Issue-Kategorien",st["H2"]))
        cl,cs = list(cats.keys()),list(cats.values())
        story.append(_issue_bars("Häufigkeit",cl,cs,[cm2.get(l,C["gray"]) for l in cl]))

    # v13 Ä5: Verteilung der Längenanker-Quellen.
    # Zeigt, ob Längenlimits aus Stilvorlagen oder aus Workflow-Defaults
    # abgeleitet wurden. Wichtige Diagnose: wenn ein FAIL-Test
    # source="style_too_short_fallback" zeigt, wissen wir dass die
    # Stilvorlage zu kurz war für eine valide Längenherleitung.
    src_counts = defaultdict(int)
    for e in ae:
        src = e.get("length_source")
        if src:
            # Kurze Labels für die Anzeige
            short = (
                "Stilvorlage" if src.startswith("Stilvorlage(n)")
                else "Stil zu kurz → Default" if "zu kurz" in src
                else "Stil ungültig → Default" if "out-of-range" in src
                else "Workflow-Default" if "keine Stilvorlage" in src
                else src[:30]
            )
            src_counts[short] += 1
    if src_counts:
        src_colors = {
            "Stilvorlage": C["green"],
            "Stil zu kurz → Default": C["orange"],
            "Stil ungültig → Default": C["red_lt"],
            "Workflow-Default": C["gray"],
        }
        story.append(Spacer(1,4*mm))
        story.append(Paragraph("Längenanker-Quellen",st["H2"]))
        sl, sv = list(src_counts.keys()), list(src_counts.values())
        story.append(_issue_bars(
            "Aus welcher Quelle kam das Wortlimit?",
            sl, sv, [src_colors.get(l, C["gray"]) for l in sl],
        ))

    # ── v19.1: Generierungs-Stabilitaet (Think-Block-Diagnose) ───────────
    # Aggregat ueber alle Testfaelle: wie oft schlug der Retry-Layer an,
    # wie oft blieb der Output trotz Retry degradiert, wie oft traf der
    # Generation-Cap zu. Datenquelle: *.telemetry.json (geschrieben vom
    # test_eval.py-Patch v19.1). Fehlende Telemetrie = Pre-v19.1-Run.
    _gen_total = sum(1 for e in ae if e.get("gen_telemetry") is not None)
    if _gen_total:
        _gen_retry    = sum(1 for e in ae if e.get("gen_retry_used"))
        _gen_degraded = sum(1 for e in ae if e.get("gen_degraded"))
        _gen_tokcap   = sum(1 for e in ae
                            if (e.get("gen_telemetry") or {}).get("tokens_hit_cap"))
        _gen_thinkfb  = sum(1 for e in ae
                            if (e.get("gen_telemetry") or {}).get("used_thinking_fallback"))

        story.append(Spacer(1, 4*mm))
        story.append(Paragraph("Generierungs-Stabilität", st["H2"]))

        # KPI-Tabelle: vier Indikatoren als Quick-View
        def _pct(n: int) -> str:
            return f"{n}/{_gen_total} ({100*n/_gen_total:.0f}%)"
        kpi = Table(
            [
                ["Indikator", "Häufigkeit", "Bedeutung"],
                ["Retry ausgelöst",     _pct(_gen_retry),
                 "Erster Pass war zu kurz → härterer 2. Pass"],
                ["Output degradiert",   _pct(_gen_degraded),
                 "Beide Pässe zu kurz → Think-Block-Bug nicht behoben"],
                ["Token-Cap erreicht",  _pct(_gen_tokcap),
                 "num_predict-Budget voll ausgeschöpft"],
                ["Thinking-Fallback",   _pct(_gen_thinkfb),
                 "content leer, nur thinking-Feld vorhanden"],
            ],
            colWidths=[4.5*cm, 3.5*cm, 8.5*cm],
        )
        kpi.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), C["dark"]),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 8),
            ("ALIGN",      (1,0), (1,-1), "CENTER"),
            ("GRID",       (0,0), (-1,-1), .5, C["grid"]),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, C["cream"]]),
            ("BOTTOMPADDING", (0,0), (-1,-1), 3),
            ("TOPPADDING",    (0,0), (-1,-1), 3),
            # Degraded-Zeile rot einfaerben wenn >0
            *([("BACKGROUND", (0,2), (-1,2), colors.HexColor("#fde0e0"))]
              if _gen_degraded > 0 else []),
        ]))
        story.append(kpi)
        story.append(Spacer(1, 2*mm))

        # Pro-Workflow-Aggregation
        wf_rows = [["Workflow", "Tests", "Retry", "Degraded",
                    "Ø think_ratio", "Token-Cap"]]
        for _wf, _entries in data["workflows"].items():
            _wfe = [e for e in _entries if e.get("gen_telemetry") is not None]
            if not _wfe:
                continue
            _n = len(_wfe)
            _r = sum(1 for e in _wfe if e.get("gen_retry_used"))
            _d = sum(1 for e in _wfe if e.get("gen_degraded"))
            _tc = sum(1 for e in _wfe
                      if (e.get("gen_telemetry") or {}).get("tokens_hit_cap"))
            _ratios = [
                (e.get("gen_telemetry") or {}).get("think_ratio") or 0
                for e in _wfe
            ]
            _avg_ratio = sum(_ratios) / len(_ratios) if _ratios else 0.0
            wf_rows.append([
                WF_LBL.get(_wf, _wf),
                str(_n),
                str(_r),
                str(_d),
                f"{_avg_ratio:.0%}",
                str(_tc),
            ])
        if len(wf_rows) > 1:
            wft = Table(wf_rows, colWidths=[5.0*cm, 1.6*cm, 1.6*cm, 1.8*cm,
                                            2.4*cm, 1.8*cm])
            wft.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), C["dark"]),
                ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
                ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",   (0,0), (-1,-1), 8),
                ("ALIGN",      (1,0), (-1,-1), "CENTER"),
                ("GRID",       (0,0), (-1,-1), .5, C["grid"]),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, C["cream"]]),
                ("BOTTOMPADDING", (0,0), (-1,-1), 3),
                ("TOPPADDING",    (0,0), (-1,-1), 3),
            ]))
            story.append(wft)
            story.append(Spacer(1, 1*mm))
            story.append(Paragraph(
                '<font size="7" color="#666666">'
                '<b>Retry</b>: Anzahl Faelle mit ausgeloestem Retry-Layer · '
                '<b>Degraded</b>: trotz Retry leerer/zu-kurzer Output · '
                '<b>Ø think_ratio</b>: durchschnittlicher Think-Block-Anteil · '
                '<b>Token-Cap</b>: num_predict voll ausgeschoepft'
                '</font>',
                st["Normal"],
            ))

    story.append(PageBreak())

    # Detail tables
    for wf,entries in data["workflows"].items():
        story.append(Paragraph(f"Workflow: {WF_LBL.get(wf,wf)}",st["H2"]))
        # P6: Jury-Spalte ergaenzt. "—" wenn keine Jury-Bewertung vorliegt.
        # v13 Ä5: Quelle-Spalte ergaenzt (kurzform: S=Stilvorlage, F=Fallback, D=Default)
        # v19.1: Gen-Spalte ergaenzt (Generierungs-Status: ✓ / ↻ / ✗)
        dd = [["Testfall","Wörter","Score","Jury","Composite","Status",
               "Issues","Quelle","Gen"]]
        # Indizes der Zeilen mit kritischen Gen-States (rot einfaerben)
        degraded_rows: list[int] = []
        retry_rows:    list[int] = []
        for _row_idx, e in enumerate(entries, start=1):  # 1 = nach Header
            jury_cell = f"{e['jury_score']:.0f}/5" if e.get("jury_score") is not None else "—"
            # Quelle-Code: 1 Buchstabe für Kompaktheit
            src = e.get("length_source") or ""
            if src.startswith("Stilvorlage(n)"):
                src_code = "S"
            elif "zu kurz" in src:
                src_code = "F↓"
            elif "out-of-range" in src:
                src_code = "F↑"
            elif "keine Stilvorlage" in src:
                src_code = "D"
            else:
                src_code = "—"
            # v19.1: Gen-Status-Badge (ASCII-sicher fuer Helvetica)
            if e.get("gen_degraded"):
                gen_code = "X"
                degraded_rows.append(_row_idx)
            elif e.get("gen_retry_used"):
                gen_code = "R"
                retry_rows.append(_row_idx)
            elif e.get("gen_telemetry") is not None:
                gen_code = "OK"
            else:
                gen_code = "—"   # Pre-v19.1-Run, keine Telemetrie verfuegbar
            dd.append([
                e["test_id"],
                str(e["word_count"]),
                f"{e['score']*100:.0f}%",
                jury_cell,
                f"{e.get('composite', e['score'])*100:.0f}%",
                e["status"],
                str(e["issues"]),
                src_code,
                gen_code,
            ])
        dt = Table(dd,colWidths=[4.6*cm,1.3*cm,1.3*cm,1.1*cm,1.5*cm,1.3*cm,
                                  1.0*cm,1.0*cm,0.8*cm])
        _ts = [("BACKGROUND",(0,0),(-1,0),C["dark"]),("TEXTCOLOR",(0,0),(-1,0),colors.white),
            ("FONTSIZE",(0,0),(-1,-1),8),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("ALIGN",(1,0),(-1,-1),"CENTER"),("GRID",(0,0),(-1,-1),.5,C["grid"]),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,C["cream"]]),
            ("BOTTOMPADDING",(0,0),(-1,-1),3),("TOPPADDING",(0,0),(-1,-1),3)]
        # v19.1: Gen-Status farblich hervorheben (Spalte 8 = Gen).
        # ROWBACKGROUNDS wird per-Cell ueberschrieben fuer auffaellige Faelle.
        for _r in degraded_rows:
            _ts.append(("BACKGROUND", (8,_r), (8,_r), colors.HexColor("#fde0e0")))
            _ts.append(("TEXTCOLOR",  (8,_r), (8,_r), C["red"]))
            _ts.append(("FONTNAME",   (8,_r), (8,_r), "Helvetica-Bold"))
        for _r in retry_rows:
            _ts.append(("BACKGROUND", (8,_r), (8,_r), colors.HexColor("#fff3e0")))
            _ts.append(("TEXTCOLOR",  (8,_r), (8,_r), C["orange"]))
        dt.setStyle(TableStyle(_ts))
        story.append(dt); story.append(Spacer(1,1*mm))
        # v13 Ä5 + v19.1: Legende für Quelle- und Gen-Spalte
        story.append(Paragraph(
            '<font size="7" color="#666666">'
            'Quelle: <b>S</b>=Stilvorlage(n) · '
            '<b>F↓</b>=Stilvorlage zu kurz, Default-Fallback · '
            '<b>F↑</b>=Stilvorlage zu lang, Default-Fallback · '
            '<b>D</b>=Workflow-Default (keine Stilvorlage)<br/>'
            'Gen: <b>OK</b>=normal · <b>R</b>=Retry ausgelöst · '
            '<b>X</b>=degradiert (Think-Block-Bug nicht behoben) · '
            '<b>—</b>=Pre-v19.1, keine Telemetrie'
            '</font>',
            st["Normal"],
        ))
        story.append(Spacer(1,3*mm))
        for e in entries:
            if e["issue_list"]:
                items = [Paragraph(f'<font color="{C["red_lt"].hexval()}"><b>{e["test_id"]}:</b></font>',st["B"])]
                for iss in e["issue_list"]: items.append(Paragraph(f"• {iss}",st["Iss"]))
                items.append(Spacer(1,2*mm))
                story.append(KeepTogether(items))

    # ── LLM-Output-Vergleich (untereinander: Referenz, dann Output) ──────
    st.add(ParagraphStyle("RefTxt", parent=st["Normal"], fontSize=7, leading=9,
                           textColor=C["dark"], fontName="Helvetica"))
    st.add(ParagraphStyle("ColHead", parent=st["Normal"], fontSize=8, leading=10,
                           textColor=colors.white, fontName="Helvetica-Bold"))
    has_comparison = any(e.get("ref_text") or e.get("output_text")
                         for wf in data["workflows"].values() for e in wf)
    if has_comparison:
        story.append(PageBreak())
        story.append(Paragraph("Textvergleich: Stilvorlage vs. LLM-Output", st["H2"]))
        story.append(Paragraph(
            "Stilvorlage (blau) = Beispieltext eines anderen Patienten desselben Therapeuten. "
            "LLM-Output (rot) = generierter Text für den aktuellen Testfall. "
            "Nur der Schreibstil soll übereinstimmen, nicht die Inhalte.",
            ParagraphStyle("CompInfo", parent=st["Normal"], fontSize=8, textColor=C["gray"],
                           spaceAfter=4*mm)))
        pw = A4[0] - 4*cm  # page width minus margins

        def _safe_text(text):
            """XML-Escaping + Zeilenumbrueche fuer reportlab Paragraph."""
            if not text:
                return "<i>Nicht verfügbar</i>"
            safe = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            safe = safe.replace("\n", "<br/>")
            return safe

        for wf, entries in data["workflows"].items():
            for e in entries:
                ref = e.get("ref_text", "")
                out = e.get("output_text", "")
                if not ref and not out:
                    continue

                story.append(Paragraph(
                    f'<b>{WF_LBL.get(wf,wf)}</b> — {e["test_id"]}',
                    ParagraphStyle("CompTitle", parent=st["Normal"], fontSize=9,
                                   textColor=C["dark"], fontName="Helvetica-Bold",
                                   spaceBefore=6*mm, spaceAfter=2*mm)))

                # Stilvorlage (blau)
                hdr_ref = Table(
                    [[Paragraph('<font color="white">Stilvorlage (anderer Patient, nur Stil-Referenz)</font>', st["ColHead"])]],
                    colWidths=[pw])
                hdr_ref.setStyle(TableStyle([
                    ("BACKGROUND", (0,0), (-1,-1), C["blue"]),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 2),
                    ("TOPPADDING", (0,0), (-1,-1), 2),
                ]))
                story.append(hdr_ref)
                story.append(Paragraph(_safe_text(ref), st["RefTxt"]))
                story.append(Spacer(1, 3*mm))

                # LLM-Output (rot)
                hdr_out = Table(
                    [[Paragraph(f'<font color="white">LLM-Output — {e["test_id"]}</font>', st["ColHead"])]],
                    colWidths=[pw])
                hdr_out.setStyle(TableStyle([
                    ("BACKGROUND", (0,0), (-1,-1), C["red"]),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 2),
                    ("TOPPADDING", (0,0), (-1,-1), 2),
                ]))
                story.append(hdr_out)
                story.append(Paragraph(_safe_text(out), st["RefTxt"]))
                story.append(Spacer(1, 6*mm))
    else:
        story.append(PageBreak())
        story.append(Paragraph("Textvergleich: Referenz vs. LLM-Output", st["H2"]))
        story.append(Paragraph(
            "Keine Referenztexte verfügbar. Bitte Tests mit aktueller test_eval.py "
            "nochmal ausführen — die .ref.txt Dateien werden dann automatisch gespeichert.",
            ParagraphStyle("CompNote", parent=st["Normal"], fontSize=9, textColor=C["orange"],
                           spaceAfter=4*mm)))

    if data.get("variance"):
        story.append(PageBreak())
        story.append(Paragraph("Stil-Varianz",st["H2"]))
        # P6: nach Workflow gruppieren statt eine flache Liste zu produzieren.
        # Vorher war im PDF nur "TherapeutA vs TherapeutB - 0.239" ohne Kontext.
        from collections import defaultdict as _dd
        grouped = _dd(list)
        for vr in data["variance"]:
            wf = vr.get("workflow", "?")
            grouped[wf].append(vr)
        for wf in sorted(grouped.keys()):
            wf_label = WF_LBL.get(wf, wf)
            story.append(Paragraph(
                f'<b>{wf_label}</b>',
                ParagraphStyle("VarWF", parent=st["Normal"], fontSize=9,
                               textColor=C["dark"], spaceBefore=3*mm, spaceAfter=1*mm)))
            for vr in grouped[wf]:
                sc = vr.get("variance_score",0)
                cl = C["green"] if sc>.15 else (C["orange"] if sc>.05 else C["red"])
                ic = "✓" if sc>.15 else ("⚠" if sc>.05 else "✗")
                story.append(Paragraph(f'<font color="{cl.hexval()}"><b>{ic}</b></font> '
                    f'{vr.get("therapeut_a","?")} vs. {vr.get("therapeut_b","?")} — {sc:.3f}',
                    st["B"]))
    if data.get("jury"):
        story.append(Spacer(1,6*mm))
        story.append(Paragraph("LLM-Stil-Jury",st["H2"]))
        # P6: Sortiere Failures (<3) nach oben, dann nach Score absteigend.
        sorted_jury = sorted(
            data["jury"],
            key=lambda r: (r.get("score", 0) >= 3, -r.get("score", 0))
        )
        for jr in sorted_jury:
            sc = jr.get("score",0)
            cl = C["green"] if sc>=4 else (C["orange"] if sc>=3 else C["red"])
            reason = jr.get("reason","") or jr.get("begruendung","")
            # XML-Escaping + Zeilenumbrueche
            reason = reason.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\n","<br/>")
            # P6: Label mit Workflow + test_id (vorher anonym)
            wf = jr.get("workflow","?")
            tid = jr.get("test_id","") or jr.get("testcase_id","")
            label = f"{WF_LBL.get(wf,wf)} / {tid}" if tid else WF_LBL.get(wf,wf)
            jury_style = ParagraphStyle("JuryItem", parent=st["Normal"], fontSize=8, leading=11, textColor=C["dark"])
            story.append(Paragraph(
                f'<font color="{cl.hexval()}"><b>{sc}/5</b></font> '
                f'<b>{label}</b> — {reason}', jury_style))
            story.append(Spacer(1, 3*mm))
    doc.build(story)
    return out

def main():
    p = argparse.ArgumentParser(description="scriptTelios Eval-Report")
    p.add_argument("results_dir")
    p.add_argument("-o","--output",default=None)
    a = p.parse_args()
    rd = Path(a.results_dir)
    if not rd.exists(): print(f"Nicht gefunden: {rd}"); sys.exit(1)
    op = Path(a.output) if a.output else rd/"eval_report.pdf"
    data = load_eval_results(rd)
    tot = sum(len(v) for v in data["workflows"].values())
    if not tot: print("Keine Ergebnisse."); sys.exit(1)
    print(f"{tot} Testfälle in {len(data['workflows'])} Workflows")
    build_report(data, op)
    print(f"Report: {op}")

if __name__ == "__main__":
    main()
