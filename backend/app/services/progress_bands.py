"""Adaptive Progress-Bänder aus performance.log."""
import json, statistics
from pathlib import Path

FALLBACK = {
    "dokumentation":      {"transcription": 120, "extraction": 5,  "llm": 35},
    "anamnese":           {"transcription": 180, "extraction": 15, "llm": 60},
    "verlaengerung":      {"transcription": 0,   "extraction": 25, "llm": 55},
    "folgeverlaengerung": {"transcription": 0,   "extraction": 35, "llm": 55},
    "akutantrag":         {"transcription": 0,   "extraction": 20, "llm": 30},
    "entlassbericht":     {"transcription": 0,   "extraction": 25, "llm": 90},
}
_cache: dict = {}

def load_durations(log: str = "/workspace/performance.log") -> dict:
    """Lädt Median-Dauern pro Workflow/Phase aus dem Performance-Log."""
    if _cache:
        return _cache
    samples: dict = {}
    try:
        for line in Path(log).read_text(encoding="utf-8").splitlines()[-1000:]:
            try:
                j = json.loads(line)
                wf, phases = j.get("workflow"), j.get("phases") or {}
                if not wf or not phases:
                    continue
                samples.setdefault(wf, {})
                for ph, dur in phases.items():
                    if isinstance(dur, (int, float)) and dur > 0:
                        samples[wf].setdefault(ph, []).append(float(dur))
            except Exception:
                continue
    except FileNotFoundError:
        pass
    for wf, fb in FALLBACK.items():
        s = samples.get(wf, {})
        _cache[wf] = {
            ph: statistics.median(s[ph]) if len(s.get(ph, [])) >= 3 else fb[ph]
            for ph in fb
        }
    return _cache

def compute_bands(workflow: str, has_audio: bool, has_docs: bool) -> dict:
    """Berechnet Prozent-Bänder für die aktiven Phasen dieses Jobs."""
    d = load_durations().get(workflow, FALLBACK.get(workflow, FALLBACK["dokumentation"]))
    active = {}
    if has_audio and d.get("transcription", 0) > 0:
        active["transcription"] = d["transcription"]
    if has_docs and d.get("extraction", 0) > 0:
        active["extraction"] = d["extraction"]
    active["llm"] = max(d.get("llm", 30), 1)
    total = sum(active.values())
    bands, cursor = {}, 5.0
    for phase in ("transcription", "extraction", "llm"):
        if phase not in active:
            continue
        width = 90 * (active[phase] / total)
        bands[phase] = (int(round(cursor)), int(round(cursor + width)))
        cursor += width
    return bands

def reload():
    """Cache leeren um neue Messungen zu laden."""
    _cache.clear()
