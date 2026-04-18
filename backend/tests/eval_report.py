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
WF_COL = {"entlassbericht": C["red"], "verlaengerung": C["blue"],
          "folgeverlaengerung": C["orange"], "anamnese": C["green"],
          "dokumentation": C["dark"]}
WF_LBL = {"entlassbericht": "Entlassbericht", "verlaengerung": "Verlängerung",
           "folgeverlaengerung": "Folgeverlängerung", "anamnese": "Anamnese",
           "dokumentation": "Gesprächsdoku"}

def load_eval_results(d: Path) -> dict:
    results = defaultdict(list)
    for wd in sorted(d.iterdir()):
        if not wd.is_dir() or wd.name.startswith(".") or wd.name in ("style_variance","style_jury"):
            continue
        wf = wd.name
        for ef in sorted(wd.glob("*.eval.txt")):
            tid = ef.stem.replace(".eval","")
            e = {"test_id":tid,"workflow":wf,"word_count":0,"passed":0,"issues":0,
                 "score":0.0,"status":"?","issue_list":[],"style_metrics":None}
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
                elif ln.startswith("- "): e["issue_list"].append(ln[2:])
            tot = e["passed"]+e["issues"]
            e["score"] = e["passed"]/tot if tot else 0.0
            tf = wd/f"{tid}.txt"
            if tf.exists(): e["word_count"] = len(tf.read_text("utf-8").split())
            sf = wd/f"{tid}.style.json"
            if sf.exists():
                try: e["style_metrics"] = json.loads(sf.read_text("utf-8"))
                except: pass
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

    # Title
    story.append(Spacer(1,2*cm))
    story.append(Paragraph("scriptTelios Evaluations-Report",st["T"]))
    story.append(Paragraph(f"Generiert: {datetime.now().strftime('%d.%m.%Y %H:%M')} · "
        f"{len(data['workflows'])} Workflows · {tot} Testfälle",st["Sub"]))
    rows = [["",""],["Testfälle",str(tot)],
        ["Bestanden",f"{passed}/{tot} ({passed/tot*100:.0f}%)" if tot else "–"],
        ["Ø Score",f"{avg_sc:.1f}%"],["Ø Wörter",f"{avg_wc:.0f}"],
        ["Workflows",", ".join(WF_LBL.get(w,w) for w in data["workflows"])]]
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
    story.append(_hbar("Score pro Testfall",lbs,[e["score"]*100 for e in ae],
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
    story.append(PageBreak())

    # Detail tables
    for wf,entries in data["workflows"].items():
        story.append(Paragraph(f"Workflow: {WF_LBL.get(wf,wf)}",st["H2"]))
        dd = [["Testfall","Wörter","Score","Status","Issues"]]
        for e in entries:
            dd.append([e["test_id"],str(e["word_count"]),f"{e['score']*100:.0f}%",e["status"],str(e["issues"])])
        dt = Table(dd,colWidths=[7*cm,2*cm,2*cm,2*cm,1.5*cm])
        dt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),C["dark"]),("TEXTCOLOR",(0,0),(-1,0),colors.white),
            ("FONTSIZE",(0,0),(-1,-1),8),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("ALIGN",(1,0),(-1,-1),"CENTER"),("GRID",(0,0),(-1,-1),.5,C["grid"]),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,C["cream"]]),
            ("BOTTOMPADDING",(0,0),(-1,-1),3),("TOPPADDING",(0,0),(-1,-1),3)]))
        story.append(dt); story.append(Spacer(1,3*mm))
        for e in entries:
            if e["issue_list"]:
                items = [Paragraph(f'<font color="{C["red_lt"].hexval()}"><b>{e["test_id"]}:</b></font>',st["B"])]
                for iss in e["issue_list"]: items.append(Paragraph(f"• {iss}",st["Iss"]))
                items.append(Spacer(1,2*mm))
                story.append(KeepTogether(items))

    # ── LLM-Output-Vergleich (2-spaltig, Referenz vs. Output) ────────
    st.add(ParagraphStyle("RefTxt", parent=st["Normal"], fontSize=6, leading=8,
                           textColor=C["dark"], fontName="Helvetica"))
    st.add(ParagraphStyle("ColHead", parent=st["Normal"], fontSize=8, leading=10,
                           textColor=colors.white, fontName="Helvetica-Bold"))
    has_comparison = any(e.get("ref_text") or e.get("output_text")
                         for wf in data["workflows"].values() for e in wf)
    if has_comparison:
        story.append(PageBreak())
        story.append(Paragraph("Textvergleich: Referenz vs. LLM-Output", st["H2"]))
        story.append(Paragraph(
            "Gegenüberstellung der Stilvorlage (links) und des generierten Texts (rechts). "
            "Nur Testfälle mit verfügbarer Stilvorlage werden angezeigt.",
            ParagraphStyle("CompInfo", parent=st["Normal"], fontSize=8, textColor=C["gray"],
                           spaceAfter=4*mm)))
        pw = A4[0] - 4*cm  # page width minus margins
        col_w = pw / 2 - 2*mm
        for wf, entries in data["workflows"].items():
            for e in entries:
                ref = e.get("ref_text", "")
                out = e.get("output_text", "")
                if not ref and not out:
                    continue
                # Escape XML entities for Paragraph
                ref_safe = ref.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\n","<br/>")
                out_safe = out.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\n","<br/>")
                header = [[Paragraph(f'<font color="white">Referenz — {WF_LBL.get(wf,wf)}</font>', st["ColHead"]),
                           Paragraph(f'<font color="white">Output — {e["test_id"]}</font>', st["ColHead"])]]
                body = [[Paragraph(ref_safe or "<i>Keine Referenz</i>", st["RefTxt"]),
                         Paragraph(out_safe or "<i>Kein Output</i>", st["RefTxt"])]]
                ht = Table(header, colWidths=[col_w, col_w])
                ht.setStyle(TableStyle([
                    ("BACKGROUND", (0,0), (0,0), C["blue"]),
                    ("BACKGROUND", (1,0), (1,0), C["red"]),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 3),
                    ("TOPPADDING", (0,0), (-1,-1), 3),
                ]))
                bt = Table(body, colWidths=[col_w, col_w])
                bt.setStyle(TableStyle([
                    ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#f0f4f8")),
                    ("BACKGROUND", (1,0), (1,-1), colors.HexColor("#fdf2f2")),
                    ("VALIGN", (0,0), (-1,-1), "TOP"),
                    ("GRID", (0,0), (-1,-1), .3, C["grid"]),
                    ("LEFTPADDING", (0,0), (-1,-1), 4),
                    ("RIGHTPADDING", (0,0), (-1,-1), 4),
                    ("TOPPADDING", (0,0), (-1,-1), 3),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 3),
                ]))
                story.append(ht)
                story.append(bt)
                story.append(Spacer(1, 6*mm))

    if data.get("variance"):
        story.append(PageBreak())
        story.append(Paragraph("Stil-Varianz",st["H2"]))
        for vr in data["variance"]:
            sc = vr.get("variance_score",0)
            cl = C["green"] if sc>.15 else (C["orange"] if sc>.05 else C["red"])
            ic = "✓" if sc>.15 else ("⚠" if sc>.05 else "✗")
            story.append(Paragraph(f'<font color="{cl.hexval()}"><b>{ic}</b></font> '
                f'{vr.get("therapeut_a","?")} vs. {vr.get("therapeut_b","?")} — {sc:.3f}',st["B"]))
    if data.get("jury"):
        story.append(Spacer(1,6*mm))
        story.append(Paragraph("LLM-Stil-Jury",st["H2"]))
        for jr in data["jury"]:
            sc = jr.get("score",0)
            cl = C["green"] if sc>=4 else (C["orange"] if sc>=3 else C["red"])
            reason = jr.get("reason","").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            story.append(Paragraph(f'<font color="{cl.hexval()}"><b>{sc}/5</b></font> — {reason}',st["B"]))
            story.append(Spacer(1, 2*mm))
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
