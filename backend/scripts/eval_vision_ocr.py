#!/usr/bin/env python3
"""
eval_vision_ocr.py - Vision-OCR (Stufe 3) mit verschiedenen Modellen
vergleichen, v.a. Checkboxen [X]/[ ] (v19.36). Braucht Ollama (Pod).

    cd /workspace/scriptTelios/backend && source /workspace/venv/bin/activate
    python scripts/eval_vision_ocr.py [--models llava,gemma4:31b] [--json out.json]
                                      [--pdf scan.pdf --truth truth.json] [--save-dir DIR]

Standard: sechs SYNTHETISCHE Formularseiten (keine Klientendaten) mit
bekannter Wahrheit - je zwei sauber, leicht gescannt, schlecht gescannt
(Drehung, Rauschen, Unschaerfe, JPEG). Angekreuzt wird mit X, Haken oder
Ausfuellen, wie handschriftlich ueblich.

Eigene Seite zusaetzlich (anonymisiert!): --pdf scan.pdf --truth truth.json,
truth.json = {"checkboxes": {"Label": true, ...}, "felder": {"Label": "Wert"}}.

Kennzahlen je Modell:
  boxen_richtig  Anteil korrekt erkannter Checkboxen (Hauptkriterium)
  falsch_an      nicht angekreuzt, aber als [X] gelesen (gefaehrlicher Fehler)
  falsch_aus     angekreuzt, aber als [ ] gelesen
  fehlt          Checkbox-Zeile nicht im Output gefunden
  felder         Anteil korrekt gelesener Textfelder
  sek_pro_seite  Laufzeit (inkl. evtl. Modellwechsel beim ersten Aufruf)

Hinweis: Der Lauf laedt nacheinander beide Modelle (bei einer GPU:
Modellwechsel). Nicht waehrend der Arbeitszeit laufen lassen.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
import unicodedata
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

# ── Synthetische Formulare ───────────────────────────────────────────────────

BESCHWERDEN = [
    "Schlafstörungen", "Kopfschmerzen", "Rückenschmerzen", "Magen-Darm-Beschwerden",
    "Herzrasen", "Schwindel", "Erschöpfung", "Konzentrationsprobleme",
    "Appetitveränderung", "Tinnitus", "Atemnot", "Gelenkschmerzen",
]
FELDER = {"Kürzel": ["Frau B.", "Herr K.", "Frau M.", "Herr T."],
          "Aufnahmedatum": ["03.09.2026", "17.09.2026", "22.09.2026"],
          "Hausarzt": ["Dr. Weber", "Dr. Schulz", "Dr. Yilmaz"]}

VARIANTEN = [("sauber", 0), ("sauber", 1), ("leicht", 2), ("leicht", 3), ("schlecht", 4), ("schlecht", 5)]


def _font(size: int):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/TTF/DejaVuSans.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                                   # Pillow < 10.1
        return ImageFont.load_default()


def _mark(d: ImageDraw.ImageDraw, x: int, y: int, s: int, art: str, rng: random.Random) -> None:
    j = lambda: rng.randint(-2, 2)  # noqa: E731 - "Handschrift"-Zittern
    ink = rng.choice([20, 20, 90])     # manchmal duenner/blasser Stift
    if art == "x":
        d.line([(x + 4 + j(), y + 4 + j()), (x + s - 4 + j(), y + s - 4 + j())], fill=ink, width=4)
        d.line([(x + s - 4 + j(), y + 4 + j()), (x + 4 + j(), y + s - 4 + j())], fill=ink, width=4)
    elif art == "haken":
        d.line([(x + 5, y + s // 2 + j()), (x + s // 2.5, y + s - 5 + j()), (x + s + 6, y - 6 + j())], fill=ink, width=4)
    else:                                               # ausgefuellt
        d.rectangle([x + 5, y + 5, x + s - 5, y + s - 5], fill=40)


def make_form(seed: int) -> tuple[Image.Image, dict]:
    """A4 bei 200 dpi. Liefert (Bild, Wahrheit)."""
    rng = random.Random(seed)
    img = Image.new("L", (1654, 2339), 255)
    d = ImageDraw.Draw(img)
    f_title, f_text = _font(46), _font(34)
    d.text((140, 120), "Selbstauskunft – Eigenbericht (Testformular)", font=f_title, fill=0)
    felder = {k: rng.choice(v) for k, v in FELDER.items()}
    y = 240
    for k, v in felder.items():
        d.text((140, y), f"{k}:", font=f_text, fill=0)
        d.text((520, y), v, font=f_text, fill=30)
        d.line([(510, y + 44), (1300, y + 44)], fill=120, width=2)
        y += 80
    y += 40
    d.text((140, y), "Welche Beschwerden haben Sie derzeit? (bitte ankreuzen)", font=f_text, fill=0)
    y += 80
    labels = rng.sample(BESCHWERDEN, 10)
    boxes: dict[str, bool] = {}
    s = 42
    for label in labels:
        checked = rng.random() < 0.45
        boxes[label] = checked
        d.rectangle([160, y, 160 + s, y + s], outline=0, width=3)
        if checked:
            _mark(d, 160, y, s, rng.choice(["x", "x", "haken", "voll"]), rng)
        d.text((230, y + 2), label, font=f_text, fill=0)
        y += 90
    return img, {"checkboxes": boxes, "felder": felder}


def scan_effects(img: Image.Image, stufe: str, seed: int) -> Image.Image:
    """Simuliert Scanner/Fax: Drehung, Rauschen, Unschaerfe, JPEG."""
    if stufe == "sauber":
        return img
    rng = random.Random(seed)
    winkel, rauschen, blur, qual = (0.6, 10, 0.6, 60) if stufe == "leicht" else (1.8, 28, 1.3, 25)
    out = img.rotate(rng.uniform(-winkel, winkel), fillcolor=255, resample=Image.BICUBIC)
    px = out.load()
    for _ in range(out.width * out.height // 40):
        x, y = rng.randrange(out.width), rng.randrange(out.height)
        px[x, y] = max(0, min(255, px[x, y] + rng.randint(-rauschen * 4, rauschen * 4)))
    if stufe == "schlecht":
        # Fax/Kopie: halbe Aufloesung, flauer Kontrast, grauer Hintergrund
        w, h = out.size
        out = out.resize((w // 2, h // 2), Image.BILINEAR).resize((w, h), Image.BILINEAR)
        out = out.point(lambda v: 70 + int(v * 0.62))
    out = out.filter(ImageFilter.GaussianBlur(blur))
    import io
    buf = io.BytesIO()
    out.convert("RGB").save(buf, "JPEG", quality=qual)
    return Image.open(io.BytesIO(buf.getvalue())).convert("L")


# ── Auswertung ───────────────────────────────────────────────────────────────

_AN = re.compile(r"\[\s*[xX✓✔×☒■]\s*\]|☒|☑|✅")
_AUS = re.compile(r"\[\s*\]|\[ \]|☐|□")


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).lower()
    return re.sub(r"[^a-zäöüß0-9]+", " ", t).strip()


def parse_checkboxes(text: str, labels: list[str]) -> dict[str, bool | None]:
    """Je Label: True ([X]), False ([ ]), None (Zeile nicht gefunden/unklar)."""
    lines = (text or "").splitlines()
    out: dict[str, bool | None] = {}
    for label in labels:
        key = _norm(label)
        hit = next((ln for ln in lines if key and key in _norm(ln)), None)
        if hit is None:
            out[label] = None
        elif _AN.search(hit):
            out[label] = True
        elif _AUS.search(hit):
            out[label] = False
        else:
            out[label] = None
    return out


def score(truth: dict, text: str) -> dict:
    boxes = truth.get("checkboxes") or {}
    got = parse_checkboxes(text, list(boxes))
    r = {"boxen": len(boxes), "richtig": 0, "falsch_an": 0, "falsch_aus": 0, "fehlt": 0}
    for label, want in boxes.items():
        g = got.get(label)
        if g is None:
            r["fehlt"] += 1
        elif g == want:
            r["richtig"] += 1
        elif g:
            r["falsch_an"] += 1
        else:
            r["falsch_aus"] += 1
    felder = truth.get("felder") or {}
    nt = _norm(text)
    r["felder"] = len(felder)
    r["felder_richtig"] = sum(1 for v in felder.values() if _norm(v) and _norm(v) in nt)
    return r


def summarize(rows: list[dict]) -> dict:
    tot = {k: sum(r[k] for r in rows) for k in ("boxen", "richtig", "falsch_an", "falsch_aus", "fehlt", "felder", "felder_richtig")}
    secs = [r["sek"] for r in rows if r.get("sek") is not None]
    return {
        **tot,
        "boxen_richtig": round(tot["richtig"] / tot["boxen"], 3) if tot["boxen"] else None,
        "felder_quote": round(tot["felder_richtig"] / tot["felder"], 3) if tot["felder"] else None,
        "sek_pro_seite": round(sum(secs) / len(secs), 1) if secs else None,
        "fehler": sum(1 for r in rows if r.get("fehler")),
    }


# ── Lauf ─────────────────────────────────────────────────────────────────────

async def run(models: list[str], pages: list[tuple[str, Image.Image, dict]], save_dir: Path | None) -> dict:
    from app.services.extraction import _check_vision_model_available, _image_to_base64, _ollama_vision_page
    result: dict = {}
    for m in models:
        if not await _check_vision_model_available(m):
            print(f"{m:22s} nicht in Ollama (ollama pull {m}) - uebersprungen")
            continue
        caps = await _capabilities(m)
        if caps is not None and "vision" not in caps:
            print(f"{m:22s} kann laut Ollama keine Bilder lesen (capabilities={caps}) - uebersprungen")
            continue
        rows = []
        for name, img, truth in pages:
            t0 = time.time()
            try:
                text = await _ollama_vision_page(_image_to_base64(img.convert("RGB")), 1, 1, model=m)
                err = None
            except Exception as e:  # noqa: BLE001
                text, err = "", str(e)
            row = {"seite": name, "sek": round(time.time() - t0, 1), "fehler": err, **score(truth, text)}
            rows.append(row)
            if save_dir:
                (save_dir / f"{m.replace(':', '_')}__{name}.txt").write_text(text, encoding="utf-8")
            print(f"  {m:20s} {name:14s} boxen {row['richtig']}/{row['boxen']} "
                  f"(falsch_an {row['falsch_an']}, falsch_aus {row['falsch_aus']}, fehlt {row['fehlt']}) "
                  f"felder {row['felder_richtig']}/{row['felder']} {row['sek']}s{'  FEHLER ' + err if err else ''}")
        result[m] = {"summe": summarize(rows), "seiten": rows}
    return result


async def _capabilities(model: str) -> list[str] | None:
    """Ollama /api/show -> capabilities (z.B. ["completion", "vision"]).
    None, wenn die Ollama-Version das Feld nicht kennt oder nicht erreichbar ist."""
    import httpx

    from app.core.config import settings
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(f"{settings.OLLAMA_HOST}/api/show", json={"model": model})
            r.raise_for_status()
            caps = r.json().get("capabilities")
            return list(caps) if isinstance(caps, list) else None
    except Exception:  # noqa: BLE001
        return None


def synthetic_pages() -> list[tuple[str, Image.Image, dict]]:
    pages = []
    for stufe, seed in VARIANTEN:
        img, truth = make_form(seed)
        pages.append((f"{stufe}-{seed}", scan_effects(img, stufe, seed), truth))
    return pages


def pdf_pages(pdf: Path, truth_path: Path) -> list[tuple[str, Image.Image, dict]]:
    import pdf2image
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    imgs = pdf2image.convert_from_path(str(pdf), dpi=200, fmt="PNG")
    # Wahrheit gilt fuer das ganze Dokument -> Seiten zusammensetzen
    w = max(i.width for i in imgs)
    canvas = Image.new("RGB", (w, sum(i.height for i in imgs)), "white")
    y = 0
    for i in imgs:
        canvas.paste(i, (0, y))
        y += i.height
    return [(f"eigen-{pdf.stem}", canvas, truth)]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="llava,gemma4:31b")
    ap.add_argument("--pdf", type=Path)
    ap.add_argument("--truth", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--save-dir", type=Path, help="Bilder und Rohausgaben ablegen (zum Nachsehen)")
    a = ap.parse_args()
    pages = synthetic_pages()
    if a.pdf:
        if not a.truth:
            ap.error("--pdf braucht --truth")
        pages += pdf_pages(a.pdf, a.truth)
    if a.save_dir:
        a.save_dir.mkdir(parents=True, exist_ok=True)
        for name, img, truth in pages:
            img.save(a.save_dir / f"{name}.png")
            (a.save_dir / f"{name}.truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    res = await run(models, pages, a.save_dir)
    print()
    print(f"{'Modell':22s} {'Boxen richtig':>14s} {'falsch an':>10s} {'falsch aus':>11s} {'fehlt':>6s} {'Felder':>7s} {'s/Seite':>8s}")
    for m, r in res.items():
        s = r["summe"]
        print(f"{m:22s} {s['richtig']:>6d}/{s['boxen']:<3d} {s['boxen_richtig']!s:>5s} {s['falsch_an']:>10d} "
              f"{s['falsch_aus']:>11d} {s['fehlt']:>6d} {s['felder_quote']!s:>7s} {s['sek_pro_seite']!s:>8s}")
    if a.json:
        a.json.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"-> {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
