#!/usr/bin/env python3
"""
scriptTelios Performance-Report
================================

Liest /workspace/performance.log (JSON-Lines) und gibt eine
Zusammenfassung der Job-Performance aus.

Aufruf:
    python scripts/perf_report.py                      # Alle Daten
    python scripts/perf_report.py --last 24h           # Letzte 24 Stunden
    python scripts/perf_report.py --last 7d            # Letzte 7 Tage
    python scripts/perf_report.py --workflow anamnese   # Nur Anamnese-Jobs
    python scripts/perf_report.py --json               # Maschinenlesbar
    python scripts/perf_report.py --log /pfad/zu/performance.log
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_LOG = "/workspace/performance.log"


def parse_args():
    p = argparse.ArgumentParser(description="scriptTelios Performance-Report")
    p.add_argument("--log", default=DEFAULT_LOG, help="Pfad zur performance.log")
    p.add_argument("--last", default=None, help="Zeitraum: 1h, 24h, 7d, 30d")
    p.add_argument("--workflow", default=None, help="Filter: dokumentation|anamnese|verlaengerung|entlassbericht")
    p.add_argument("--json", action="store_true", help="JSON-Output statt Text")
    p.add_argument("--errors", action="store_true", help="Nur fehlgeschlagene Jobs zeigen")
    return p.parse_args()


def parse_timespan(s: str) -> timedelta:
    """Parst '1h', '24h', '7d', '30d' in timedelta."""
    s = s.strip().lower()
    if s.endswith("h"):
        return timedelta(hours=int(s[:-1]))
    if s.endswith("d"):
        return timedelta(days=int(s[:-1]))
    raise ValueError(f"Unbekanntes Zeitformat: {s} (erwartet: 1h, 24h, 7d)")


def load_entries(log_path: str, since: datetime | None, workflow: str | None, errors_only: bool) -> list[dict]:
    """Lädt und filtert Einträge aus der performance.log."""
    entries = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Zeitfilter
            if since and "ts" in entry:
                try:
                    ts = datetime.fromisoformat(entry["ts"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < since:
                        continue
                except (ValueError, TypeError):
                    pass

            # Workflow-Filter
            if workflow and entry.get("workflow") != workflow:
                continue

            # Error-Filter
            if errors_only and entry.get("status") != "error":
                continue

            entries.append(entry)

    return entries


def load_backend_errors(log_dir: str, since: datetime | None) -> list[dict]:
    """
    Parst ERROR- und WARNING-Zeilen aus dem Backend-Log (systelios.log / backend.log).
    Erfasst Fehler die nicht in performance.log stehen:
    VRAM-Probleme, OCR-Fehler, Transkriptions-Timeouts, Stil-Extraktion etc.
    """
    import re

    errors = []
    # Alle möglichen Log-Dateien prüfen (persistent auf /workspace)
    log_files = [
        Path(log_dir) / "systelios.log",
        Path(log_dir) / "backend.log",
        Path("/workspace") / "systelios.log",
        Path("/workspace") / "backend.log",
    ]
    # Deduplizieren (falls log_dir schon /workspace ist)
    log_files = list(dict.fromkeys(log_files))

    log_pattern = re.compile(
        r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d{3})\s+"
        r"(ERROR|WARNING)\s+"
        r"(\S+)\s+"
        r"(.+)$"
    )

    for log_file in log_files:
        if not log_file.exists():
            continue
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = log_pattern.match(line.strip())
                    if not m:
                        continue

                    ts_str, level, source, message = m.groups()

                    # Zeitfilter
                    if since:
                        try:
                            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f")
                            ts = ts.replace(tzinfo=timezone.utc)
                            if ts < since:
                                continue
                        except ValueError:
                            pass

                    # Bekannte noise rausfiltern
                    if any(skip in message for skip in [
                        "torchcodec",
                        "fontManager",
                        "unauthenticated requests",
                    ]):
                        continue

                    errors.append({
                        "ts": ts_str,
                        "level": level,
                        "source": source.split(".")[-1],  # nur Modulname
                        "message": message[:200],
                    })
        except OSError:
            continue

    return errors


def compute_stats(entries: list[dict], backend_errors: list[dict] | None = None) -> dict:
    """Berechnet Statistiken aus den gefilterten Einträgen."""
    total = len(entries)
    if total == 0 and not backend_errors:
        return {"total": 0, "message": "Keine Einträge gefunden"}

    # Basis-Zähler
    by_status = defaultdict(int)
    by_workflow = defaultdict(list)
    durations = []
    output_words = []
    job_errors = []

    # Input-Statistiken
    with_audio = 0
    with_pdf = 0
    with_style = 0

    for e in entries:
        by_status[e.get("status", "?")] += 1
        wf = e.get("workflow", "?")
        d = e.get("duration_s")
        if d is not None:
            durations.append(d)
            by_workflow[wf].append(d)

        ow = e.get("output_words", 0)
        if ow:
            output_words.append(ow)

        if e.get("status") == "error":
            job_errors.append({
                "ts": e.get("ts", "?"),
                "workflow": wf,
                "error": (e.get("error") or "")[:200],
                "duration_s": d,
            })

        inp = e.get("input") or {}
        if inp.get("has_audio"):
            with_audio += 1
        if inp.get("has_vorbef_pdf") or inp.get("has_selbst_pdf"):
            with_pdf += 1
        if inp.get("has_style"):
            with_style += 1

    # Workflow-Statistiken
    workflow_stats = {}
    for wf, durs in sorted(by_workflow.items()):
        workflow_stats[wf] = {
            "count": len(durs),
            "avg_s": round(sum(durs) / len(durs), 1),
            "min_s": round(min(durs), 1),
            "max_s": round(max(durs), 1),
            "median_s": round(sorted(durs)[len(durs) // 2], 1),
        }

    # Backend-Errors kategorisieren
    error_categories = defaultdict(int)
    if backend_errors:
        for err in backend_errors:
            msg = err["message"].lower()
            if "vram" in msg or "cuda" in msg or "out of memory" in msg:
                error_categories["VRAM/CUDA"] += 1
            elif "404" in msg or "not found" in msg:
                error_categories["Modell nicht gefunden"] += 1
            elif "timeout" in msg:
                error_categories["Timeout"] += 1
            elif "nicht erreichbar" in msg or "connect" in msg.lower():
                error_categories["Verbindung"] += 1
            elif "ocr" in msg or "extraktion" in msg or "pdfplumber" in msg:
                error_categories["OCR/Extraktion"] += 1
            elif "stilprofil" in msg or "style" in msg:
                error_categories["Stilprofil"] += 1
            elif "transkription" in msg or "whisper" in msg:
                error_categories["Transkription"] += 1
            elif "diarization" in msg or "pyannote" in msg:
                error_categories["Diarization"] += 1
            else:
                error_categories["Sonstige"] += 1

    return {
        "total": total,
        "by_status": dict(by_status),
        "by_workflow": workflow_stats,
        "duration": {
            "avg_s": round(sum(durations) / len(durations), 1) if durations else 0,
            "min_s": round(min(durations), 1) if durations else 0,
            "max_s": round(max(durations), 1) if durations else 0,
            "median_s": round(sorted(durations)[len(durations) // 2], 1) if durations else 0,
        },
        "output": {
            "avg_words": round(sum(output_words) / len(output_words)) if output_words else 0,
            "min_words": min(output_words) if output_words else 0,
            "max_words": max(output_words) if output_words else 0,
        },
        "inputs": {
            "with_audio": with_audio,
            "with_pdf": with_pdf,
            "with_style": with_style,
        },
        "job_errors": job_errors[-10:],
        "backend_errors": (backend_errors or [])[-20:],
        "error_categories": dict(error_categories),
        "backend_error_count": len(backend_errors) if backend_errors else 0,
        "time_range": {
            "first": entries[0].get("ts", "?") if entries else "?",
            "last": entries[-1].get("ts", "?") if entries else "?",
        },
    }


def _q(vals: list, frac: float):
    vals = sorted(v for v in vals if isinstance(v, (int, float)))
    if not vals:
        return None
    return round(vals[min(len(vals) - 1, int(len(vals) * frac))], 1)


def compute_interview_stats(entries: list[dict]) -> dict:
    """v19.33: Interview-Latenzen und Nutzung (kind=interview_chat /
    interview_transcribe). "Dialoge pro Woche" ist der Indikator fuer den
    Umstieg auf zwei GPUs (GPU_PROFILE=dual)."""
    chats = [e for e in entries if e.get("kind") == "interview_chat"]
    trans = [e for e in entries if e.get("kind") == "interview_transcribe"]
    tts = [e for e in entries if e.get("kind") == "interview_tts"]
    if not chats and not trans and not tts:
        return {}
    sessions: dict[str, str] = {}
    for e in chats:
        sid = e.get("session") or ""
        if sid and sid not in sessions:
            sessions[sid] = e.get("ts", "")
    weeks: dict[str, int] = defaultdict(int)
    for ts in sessions.values():
        try:
            y, w, _ = datetime.fromisoformat(ts).isocalendar()
            weeks[f"{y}-KW{w:02d}"] += 1
        except (ValueError, TypeError):
            continue
    users = {e.get("user") for e in chats if e.get("user")}

    def col(rows, key):
        return [r.get(key) for r in rows]

    reloads = [e for e in chats if isinstance(e.get("load_s"), (int, float)) and e["load_s"] > 1.0]
    return {
        "turns": len(chats), "dialoge": len(sessions), "nutzer": len(users),
        "dialoge_pro_woche": dict(sorted(weeks.items())),
        "ttft_s": {"median": _q(col(chats, "ttft_s"), 0.5), "p90": _q(col(chats, "ttft_s"), 0.9)},
        "total_s": {"median": _q(col(chats, "total_s"), 0.5), "p90": _q(col(chats, "total_s"), 0.9)},
        "reloads": len(reloads),
        "reload_load_s": {"median": _q(col(reloads, "load_s"), 0.5), "max": _q(col(reloads, "load_s"), 1.0)},
        "turns_mit_jobs": sum(1 for e in chats if (e.get("jobs_running") or 0) > 0),
        "diktate": len(trans),
        "transcribe_s": {"median": _q(col(trans, "transcribe_s"), 0.5), "p90": _q(col(trans, "transcribe_s"), 0.9)},
        "whisper_loads": sum(1 for e in trans if (e.get("whisper_load_s") or 0) > 1.0),
        "tts": _tts_stats(tts),
    }


def _tts_stats(rows: list[dict]) -> dict:
    """v19.35: Vorlese-Zeiten je Engine (ohne Cache-Treffer). rtf = Rechenzeit
    / Audiolaenge; < 1 heisst schneller als Echtzeit (keine Luecken)."""
    out: dict = {}
    for eng in sorted({r.get("engine") for r in rows if r.get("engine")}):
        rs = [r for r in rows if r.get("engine") == eng and not r.get("cached")]
        rtf = [r["synth_s"] / r["audio_s"] for r in rs
               if isinstance(r.get("synth_s"), (int, float)) and r.get("audio_s")]
        out[eng] = {"saetze": len(rs), "cache_treffer": sum(1 for r in rows if r.get("engine") == eng and r.get("cached")),
                    "synth_s": {"median": _q([r.get("synth_s") for r in rs], 0.5), "p90": _q([r.get("synth_s") for r in rs], 0.9)},
                    "rtf_median": round(sorted(rtf)[len(rtf) // 2], 2) if rtf else None}
    return out


def print_interview(iv: dict) -> None:
    if not iv:
        return
    print("── Interview (Dialog-Modus) ───────────────────────────")
    print(f"  Dialoge: {iv['dialoge']} | Turns: {iv['turns']} | Nutzer: {iv['nutzer']} | Diktate: {iv['diktate']}")
    if iv["dialoge_pro_woche"]:
        print("  Dialoge/Woche: " + ", ".join(f"{k}: {v}" for k, v in iv["dialoge_pro_woche"].items()))
    print(f"  Erstes Wort:  Median {iv['ttft_s']['median']}s | p90 {iv['ttft_s']['p90']}s")
    print(f"  Turn gesamt:  Median {iv['total_s']['median']}s | p90 {iv['total_s']['p90']}s")
    print(f"  Modell neu geladen: {iv['reloads']}x (Median {iv['reload_load_s']['median']}s, "
          f"max {iv['reload_load_s']['max']}s) | Turns mit parallelen Jobs: {iv['turns_mit_jobs']}")
    print(f"  Diktat:       Median {iv['transcribe_s']['median']}s | p90 {iv['transcribe_s']['p90']}s | "
          f"Whisper geladen: {iv['whisper_loads']}x")
    for eng, t in (iv.get("tts") or {}).items():
        print(f"  Vorlesen {eng:11s} {t['saetze']} Saetze | Median {t['synth_s']['median']}s | "
              f"p90 {t['synth_s']['p90']}s | RTF {t['rtf_median']} | Cache {t['cache_treffer']}x")
    print()


def print_report(stats: dict, args):
    """Gibt den Report als Text aus."""
    if stats["total"] == 0:
        if stats.get("interview"):
            print_interview(stats["interview"])
        else:
            print("Keine Einträge gefunden.")
        return

    print("=" * 60)
    print("  scriptTelios Performance-Report")
    print("=" * 60)
    tr = stats["time_range"]
    print(f"  Zeitraum: {tr['first'][:19]} – {tr['last'][:19]}")
    print(f"  Jobs gesamt: {stats['total']}")
    print()

    # Status-Übersicht
    print("── Status ─────────────────────────────────────────────")
    for status, count in sorted(stats["by_status"].items()):
        pct = round(count / stats["total"] * 100)
        bar = "█" * (pct // 2)
        print(f"  {status:12s}  {count:4d}  ({pct:3d}%)  {bar}")
    print()

    # Workflow-Übersicht
    print("── Workflows ──────────────────────────────────────────")
    print(f"  {'Workflow':20s}  {'Jobs':>5s}  {'Avg':>6s}  {'Med':>6s}  {'Min':>6s}  {'Max':>6s}")
    print(f"  {'─'*20}  {'─'*5}  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*6}")
    for wf, ws in stats["by_workflow"].items():
        print(f"  {wf:20s}  {ws['count']:5d}  {ws['avg_s']:5.1f}s  {ws['median_s']:5.1f}s  {ws['min_s']:5.1f}s  {ws['max_s']:5.1f}s")
    print()

    # Dauer gesamt
    d = stats["duration"]
    print("── Dauer (alle Workflows) ─────────────────────────────")
    print(f"  Durchschnitt: {d['avg_s']:.1f}s | Median: {d['median_s']:.1f}s | Min: {d['min_s']:.1f}s | Max: {d['max_s']:.1f}s")
    print()

    print_interview(stats.get("interview") or {})

    # Output
    o = stats["output"]
    if o["avg_words"]:
        print("── Output ─────────────────────────────────────────────")
        print(f"  Wörter: Avg {o['avg_words']} | Min {o['min_words']} | Max {o['max_words']}")
        print()

    # Inputs
    inp = stats["inputs"]
    print("── Inputs ─────────────────────────────────────────────")
    print(f"  Mit Audio: {inp['with_audio']} | Mit PDF: {inp['with_pdf']} | Mit Stilvorlage: {inp['with_style']}")
    print()

    # Fehler-Kategorien
    cats = stats.get("error_categories", {})
    if cats:
        print("── Fehler nach Kategorie ──────────────────────────────")
        for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
            print(f"  {cat:25s}  {count:4d}")
        print(f"  {'─'*25}  {'─'*4}")
        print(f"  {'Gesamt':25s}  {stats.get('backend_error_count', 0):4d}")
        print()

    # Job-Fehler (aus performance.log)
    if stats.get("job_errors"):
        print("── Fehlgeschlagene Jobs ────────────────────────────────")
        for err in stats["job_errors"]:
            print(f"  [{err['ts'][:19]}] {err['workflow']:15s} ({err['duration_s']}s)")
            print(f"    → {err['error']}")
        print()

    # Backend-Errors (aus systelios.log / backend.log)
    be = stats.get("backend_errors", [])
    if be:
        print("── Backend-Fehler (Log) ───────────────────────────────")
        for err in be:
            level_mark = "⚠" if err["level"] == "WARNING" else "✗"
            print(f"  {level_mark} [{err['ts'][:19]}] {err['source']:15s} {err['message'][:100]}")
        if stats.get("backend_error_count", 0) > len(be):
            print(f"  ... und {stats['backend_error_count'] - len(be)} weitere")
        print()


def main():
    args = parse_args()

    log_path = args.log
    if not os.path.exists(log_path):
        print(f"Log-Datei nicht gefunden: {log_path}")
        print("Tipp: Performance-Logging wird automatisch aktiviert wenn Jobs laufen.")
        sys.exit(1)

    since = None
    if args.last:
        delta = parse_timespan(args.last)
        since = datetime.now(timezone.utc) - delta

    entries = load_entries(log_path, since, args.workflow, args.errors)

    # Backend-Errors aus systelios.log / backend.log laden
    log_dir = str(Path(log_path).parent)
    backend_errors = load_backend_errors(log_dir, since)

    # v19.33: Interview-Zeilen (mit "kind") getrennt von Job-Zeilen auswerten
    jobs = [e for e in entries if not e.get("kind")]
    interview = [e for e in entries if str(e.get("kind", "")).startswith("interview_")]
    stats = compute_stats(jobs, backend_errors)
    stats["interview"] = compute_interview_stats(interview)

    if args.json:
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    else:
        print_report(stats, args)


if __name__ == "__main__":
    main()
