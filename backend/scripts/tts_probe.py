#!/usr/bin/env python3
"""
tts_probe.py - Hoertest: dieselben Saetze mit allen Stimmen und einigen
Einstellungen als WAV erzeugen (v19.38). Spricht direkt mit dem TTS-Dienst.

    python scripts/tts_probe.py [--out /workspace/tts_probe] [--url http://127.0.0.1:8011]
                                [--engines chatterbox:carsten,piper] [--cfg 0.2,0.3,0.5]

Dateien: <engine>__cfg<wert>__<nr>.wav (+ __ohne-schnitt fuer den Vergleich
des Nachlauf-Schnitts). Die Zeiten stehen in der Ausgabe und in probe.json.
Laeuft mit jedem Python (nur Standardbibliothek).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

SAETZE = [
    "Um wen geht es? Bitte Anrede und Kürzel, zum Beispiel „Frau K.“",
    "Danke. Was war das Anliegen für die heutige Stunde?",
    "Wie ging Frau K. aus der Stunde? Gab es Hinweise auf Selbstgefährdung?",
    "Verstehe. Hat sie sich an die Kooperationsbedingung gehalten, und ist der Nachtdienst informiert?",
]


def _post(url: str, body: dict) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read(), dict(r.headers)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8011")
    ap.add_argument("--out", default="/workspace/tts_probe")
    ap.add_argument("--engines", default=None, help="Default: alle verfuegbaren")
    ap.add_argument("--cfg", default="0.3,0.5", help="cfg_weight-Werte fuer Chatterbox")
    a = ap.parse_args()
    with urllib.request.urlopen(a.url + "/engines", timeout=10) as r:
        engines = [e for e in json.loads(r.read())["engines"] if e["available"]]
    keys = [k.strip() for k in a.engines.split(",")] if a.engines else [e["key"] for e in engines]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for eng in keys:
        variants = [{"cfg_weight": float(c)} for c in a.cfg.split(",")] if eng.startswith("chatterbox") else [{}]
        if eng.startswith("chatterbox"):
            variants.append({"cfg_weight": float(a.cfg.split(",")[0]), "no_trim": True})
        for p in variants:
            tag = (f"cfg{p['cfg_weight']}" if "cfg_weight" in p else "std") + ("__ohne-schnitt" if p.get("no_trim") else "")
            for i, satz in enumerate(SAETZE, 1):
                t0 = time.time()
                try:
                    wav, h = _post(a.url + "/synthesize", {"text": satz, "engine": eng, "params": p})
                except Exception as e:  # noqa: BLE001
                    print(f"  {eng:24s} {tag:22s} {i}: FEHLER {e}")
                    continue
                name = f"{eng.replace(':', '-')}__{tag}__{i}.wav"
                (out / name).write_bytes(wav)
                row = {"engine": eng, "variante": tag, "satz": i, "datei": name,
                       "synth_s": float(h.get("X-TTS-Synth-S") or 0), "audio_s": float(h.get("X-TTS-Audio-S") or 0),
                       "gesamt_s": round(time.time() - t0, 1)}
                rows.append(row)
                print(f"  {eng:24s} {tag:22s} {i}: {row['audio_s']:.1f}s Audio in {row['synth_s']:.1f}s")
    (out / "probe.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {out} ({len(rows)} Dateien). Zum Anhoeren z.B. per scp auf den Mac holen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
