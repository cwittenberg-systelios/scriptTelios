#!/usr/bin/env python3
"""
prepare_voice.py - Referenzstimme fuer Chatterbox aus einer Aufnahme schneiden (v19.38).

    python scripts/prepare_voice.py AUFNAHME --start 12:30 --dur 16 --key carsten \\
        --label "Carsten" [--out-dir /workspace/tts/voices] [--cfg 0.3] [--exaggeration 0.5]

Ergebnis: <out-dir>/<key>.wav (24 kHz, mono, 16 bit, gefiltert, lautheitsnormiert)
und <key>.json (label + optionale Parameter). Der TTS-Dienst liest den Ordner
bei jeder Stimmen-Abfrage neu ein - kein Neustart noetig; in der Auswahl
erscheint dann "Chatterbox – <label>".

Gute Referenz: 10-20 s EINE Person, ruhig und deutlich, ohne Musik, Hall,
Lachen oder zweite Stimme; ganze Saetze. Nur mit Einverstaendnis der Person.
Braucht ffmpeg (auf dem Pod vorhanden).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

FILTER = "highpass=f=70,lowpass=f=9000,afftdn=nf=-25,loudnorm=I=-20:TP=-2:LRA=7"


def parse_time(t: str) -> float:
    """'75', '1:15', '0:01:15' -> Sekunden."""
    parts = [float(p) for p in str(t).strip().split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def ffmpeg_cmd(src: str, start: float, dur: float, out: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}",
            "-i", src, "-ac", "1", "-ar", "24000", "-af", FILTER, "-c:a", "pcm_s16le", out]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--start", default="0")
    ap.add_argument("--dur", type=float, default=16.0)
    ap.add_argument("--key", required=True, help="a-z, 0-9, _ oder -, max. 32 Zeichen")
    ap.add_argument("--label", default=None)
    ap.add_argument("--out-dir", default="/workspace/tts/voices")
    ap.add_argument("--cfg", type=float, default=None, help="cfg_weight (Tempo; kleiner = ruhiger)")
    ap.add_argument("--exaggeration", type=float, default=None)
    ap.add_argument("--temperature", type=float, default=None)
    a = ap.parse_args()
    if not re.match(r"^[a-z0-9_-]{1,32}$", a.key):
        ap.error("--key: nur a-z, 0-9, _ oder -")
    if not 6 <= a.dur <= 30:
        ap.error("--dur: 6-30 s (empfohlen 10-20 s)")
    if not shutil.which("ffmpeg"):
        print("ffmpeg fehlt", file=sys.stderr)
        return 2
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / f"{a.key}.wav"
    subprocess.run(ffmpeg_cmd(a.src, parse_time(a.start), a.dur, str(wav)), check=True)
    meta: dict = {"label": a.label or a.key}
    for k, v in (("cfg_weight", a.cfg), ("exaggeration", a.exaggeration), ("temperature", a.temperature)):
        if v is not None:
            meta[k] = v
    (out_dir / f"{a.key}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {wav} ({wav.stat().st_size // 1024} KB) + {a.key}.json")
    print("Anhoeren, bevor du sie nutzt. In der Auswahl: Chatterbox –", meta["label"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
