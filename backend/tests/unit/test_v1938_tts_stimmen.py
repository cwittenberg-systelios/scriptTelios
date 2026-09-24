"""v19.38 - Chatterbox mit eigenen Referenzstimmen, Textbereinigung,
Nachlauf-Schnitt, Hoertest-Parameter, Referenz-Schnitt-Skript."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parents[2]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, BACKEND / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ts = _load("tts_service/tts_server.py", "tts_server_38")
pv = _load("scripts/prepare_voice.py", "prepare_voice")


class TestText:
    def test_anfuehrungszeichen_und_abkuerzungen(self):
        t = ts.normalize_for_tts("Um wen geht es? Bitte Anrede und Kürzel, z. B. „Frau K.“")
        assert t == "Um wen geht es? Bitte Anrede und Kürzel, zum Beispiel Frau Ka."
        assert ts.normalize_for_tts("d.h. bzw. ggf. Dr. Weber") == "das heißt beziehungsweise gegebenenfalls Doktor Weber."

    def test_kuerzel_ausgesprochen_v19382(self):
        n = ts.normalize_for_tts
        assert n("Bitte Kürzel, zum Beispiel „Frau K.“.") == "Bitte Kürzel, zum Beispiel Frau Ka."
        assert n("Wie ging es Frau K. danach?") == "Wie ging es Frau Ka danach?"
        assert n("Danke, Herr M. Wie war die Stunde?") == "Danke, Herr Emm. Wie war die Stunde?"
        assert n("Hr. T., bitte") == "Herr Te, bitte."
        assert n("Herrn B., kam er gut an?") == "Herrn Be, kam er gut an?"
        assert n("Frau Kaiser kam") == "Frau Kaiser kam."          # ganze Namen bleiben

    def test_satzzeichen_am_ende_und_leerraum(self):
        assert ts.normalize_for_tts("  Wie ging es ihr  ") == "Wie ging es ihr."
        assert ts.normalize_for_tts("Gut – danke !") == "Gut, danke!"


def _signal(parts, sr=1000):
    """parts: [(dauer_s, amplitude)] -> Rechtecksignal."""
    return np.concatenate([np.full(int(d * sr), a, dtype="float32") * np.sign(np.sin(np.arange(int(d * sr))))
                           for d, a in parts])


class TestTrim:
    SR = 1000

    def test_nachlaut_nach_pause_wird_abgeschnitten(self):
        x = _signal([(1.5, 0.5), (0.4, 0.0), (0.3, 0.4), (0.2, 0.0)], self.SR)
        y = ts.trim_tail(x, self.SR)
        assert 1.5 <= len(y) / self.SR <= 1.7

    def test_normales_satzende_bleibt(self):
        x = _signal([(1.5, 0.5), (0.1, 0.0), (0.8, 0.5), (0.5, 0.0)], self.SR)
        y = ts.trim_tail(x, self.SR)
        assert len(y) / self.SR >= 2.4          # zweiter Teil ist zu lang fuer einen Nachlaut
        assert len(y) / self.SR <= 2.55         # Stille am Ende gekuerzt

    def test_zwei_nachlaute(self):
        x = _signal([(1.2, 0.5), (0.3, 0.0), (0.2, 0.4), (0.3, 0.0), (0.2, 0.4)], self.SR)
        y = ts.trim_tail(x, self.SR)
        assert len(y) / self.SR <= 1.35


class FakeCB(ts.ChatterboxEngine):
    def __init__(self):
        super().__init__()
        self.calls = []

    def check(self):
        return True, ""

    def _load(self):
        self.model = object()

    def synth_voice(self, text, params, voice):
        self.calls.append((text, dict(params), voice["key"] if voice else None, self._gen_kwargs(params, voice)))
        return ts.pcm16_to_wav(b"\x00\x00" * 1000, 1000)


@pytest.fixture()
def voices(tmp_path):
    d = tmp_path / "voices"
    d.mkdir()
    (d / "carsten.wav").write_bytes(ts.pcm16_to_wav(b"\x00\x00" * 100, 24000))
    (d / "carsten.json").write_text(json.dumps({"label": "Carsten", "cfg_weight": 0.25}))
    (d / "ohne_json.wav").write_bytes(ts.pcm16_to_wav(b"\x00\x00" * 100, 24000))
    (d / "Ungueltig Name.wav").write_bytes(b"x")
    (d / "notiz.txt").write_text("x")
    return d


class TestStimmen:
    def test_discover(self, voices):
        vs = {v["key"]: v for v in ts.discover_voices(str(voices))}
        assert set(vs) == {"carsten", "ohne_json"}
        assert vs["carsten"]["label"] == "Carsten" and vs["carsten"]["cfg_weight"] == 0.25
        assert ts.discover_voices(str(voices / "fehlt")) == []

    def test_service_listet_und_nutzt_stimmen(self, voices, monkeypatch):
        monkeypatch.setenv("TTS_CHATTERBOX_CFG", "0.3")
        cb = FakeCB()
        svc = ts.TTSService(engines={"chatterbox": cb}, voices_dir=str(voices))
        keys = {e["key"]: e for e in svc.list_engines()}
        assert set(keys) == {"chatterbox", "chatterbox:carsten", "chatterbox:ohne_json"}
        assert keys["chatterbox:carsten"]["label"] == "Chatterbox – Carsten"
        svc.synthesize("Um wen geht es? „Frau K.“", "chatterbox:carsten")
        text, params, vkey, kw = cb.calls[-1]
        assert text == "Um wen geht es? Frau Ka." and vkey == "carsten"
        assert kw["cfg_weight"] == 0.25                          # aus carsten.json
        svc.synthesize("Hallo.", "chatterbox:ohne_json")
        assert cb.calls[-1][3]["cfg_weight"] == 0.3              # Default aus der Umgebung
        svc.synthesize("Hallo.", "chatterbox:ohne_json", {"cfg_weight": 0.5, "boese": 1})
        assert cb.calls[-1][1] == {"cfg_weight": 0.5} and cb.calls[-1][3]["cfg_weight"] == 0.5

    def test_neue_stimme_ohne_neustart(self, voices):
        cb = FakeCB()
        svc = ts.TTSService(engines={"chatterbox": cb}, voices_dir=str(voices))
        (voices / "lena.wav").write_bytes(ts.pcm16_to_wav(b"\x00\x00" * 100, 24000))
        svc.synthesize("Hallo.", "chatterbox:lena")
        assert cb.calls[-1][2] == "lena"
        (voices / "lena.wav").unlink()
        assert "chatterbox:lena" not in {e["key"] for e in svc.list_engines()}

    def test_cache_unterscheidet_parameter(self, voices):
        cb = FakeCB()
        svc = ts.TTSService(engines={"chatterbox": cb}, voices_dir=str(voices))
        svc.synthesize("Hallo.", "chatterbox:carsten")
        _, _, cached = svc.synthesize("Hallo.", "chatterbox:carsten")
        assert cached
        _, _, cached2 = svc.synthesize("Hallo.", "chatterbox:carsten", {"cfg_weight": 0.2})
        assert not cached2


class TestBackendMuster:
    def test_engine_schluessel(self):
        from pydantic import ValidationError

        from app.api.interview import TTSIn
        assert TTSIn(text="x", engine="chatterbox:carsten").engine == "chatterbox:carsten"
        for bad in ("chatterbox:", "chatterbox:A B", "espeak", "piper:x y"):
            with pytest.raises(ValidationError):
                TTSIn(text="x", engine=bad)


class TestPrepareVoice:
    def test_zeit_und_befehl(self):
        assert pv.parse_time("75") == 75 and pv.parse_time("1:15") == 75 and pv.parse_time("0:01:15") == 75
        cmd = pv.ffmpeg_cmd("in.mp3", 75.0, 16.0, "out.wav")
        assert cmd[cmd.index("-ss") + 1] == "75.00" and cmd[cmd.index("-t") + 1] == "16.00"
        assert "24000" in cmd and "loudnorm" in cmd[cmd.index("-af") + 1]


# ── v19.38.1: Vorwaermen ─────────────────────────────────────────────────────

class _Model:
    def __init__(self):
        self.prepared = []
        self.conds = "default"

    def prepare_conditionals(self, wav, exaggeration=0.5):
        self.prepared.append((wav, exaggeration))
        self.conds = f"conds:{wav}"


class WarmCB(ts.ChatterboxEngine):
    loads = 0

    def check(self):
        return True, ""

    def _load(self):
        WarmCB.loads += 1
        self.model = _Model()


class OffEngine(ts.Engine):
    key, label = "piper", "Piper"

    def check(self):
        return False, "Stimme fehlt"


class TestWarmup:
    def test_laedt_modell_einmal_und_bereitet_alle_stimmen_vor(self, voices):
        WarmCB.loads = 0
        cb = WarmCB()
        svc = ts.TTSService(engines={"chatterbox": cb, "piper": OffEngine()}, voices_dir=str(voices))
        res = svc.warmup()
        assert WarmCB.loads == 1
        assert set(cb._conds) == {"carsten", "ohne_json"}
        assert res["piper"].startswith("uebersprungen") and res["chatterbox:carsten"].startswith("bereit")
        # zweiter Aufruf / erste Anfrage: nichts wird neu berechnet
        n = len(cb.model.prepared)
        cb.prepare_voice(ts.discover_voices(str(voices))[0])
        svc.warmup()
        assert len(cb.model.prepared) == n and WarmCB.loads == 1

    def test_fehler_beim_vorwaermen_stoppt_nichts(self, voices):
        class Kaputt(WarmCB):
            def _load(self):
                raise RuntimeError("kein Speicher")
        svc = ts.TTSService(engines={"chatterbox": Kaputt()}, voices_dir=str(voices))
        res = svc.warmup()
        assert all(v.startswith("Fehler") for v in res.values())

    def test_start_mit_vorwaermen_konfigurierbar(self):
        src = (BACKEND / "tts_service" / "tts_server.py").read_text()
        assert 'if _env_bool("TTS_WARMUP", True):' in src and "target=service.warmup" in src


# ── v19.40: Chatterbox auf der GPU (Schalter + Rueckfall) ────────────────────

GB = 1024 ** 3


class TestGeraet:
    def test_default_cpu(self):
        assert ts.choose_device("cpu") == "cpu" and ts.choose_device("") == "cpu"

    def test_cuda_nur_mit_genug_speicher(self, monkeypatch):
        monkeypatch.setenv("TTS_GPU_MIN_FREE_GB", "5")
        assert ts.choose_device("cuda", cuda_available=True, mem_get_info=lambda: (7 * GB, 32 * GB)) == "cuda"
        assert ts.choose_device("cuda", cuda_available=True, mem_get_info=lambda: (4 * GB, 32 * GB)) == "cpu"
        assert ts.choose_device("cuda", cuda_available=False) == "cpu"

    def test_oom_erkennung(self):
        assert ts._is_oom(RuntimeError("CUDA out of memory. Tried to allocate 20 MiB"))
        assert not ts._is_oom(RuntimeError("shape mismatch"))


class _GpuModel:
    """generate wirft beim ersten Mal OOM (GPU), auf der CPU klappt es."""
    sr = 1000

    def __init__(self, device):
        self.device = device
        self.conds = "default"

    def prepare_conditionals(self, wav, exaggeration=0.5):
        self.conds = f"conds:{wav}@{self.device}"

    def generate(self, text, language_id, **kw):
        if self.device == "cuda":
            raise RuntimeError("CUDA out of memory. Tried to allocate 64.00 MiB")
        return np.zeros(500, dtype="float32")


class GpuCB(ts.ChatterboxEngine):
    def check(self):
        return True, ""

    def _load(self):
        self._load_on("cuda")

    def _load_on(self, device):
        self.model = _GpuModel(device)
        self.device = device
        self._default_conds = self.model.conds
        self._conds = {}


class TestRueckfall:
    def test_oom_auf_gpu_faellt_auf_cpu_zurueck(self, voices):
        cb = GpuCB()
        svc = ts.TTSService(engines={"chatterbox": cb}, voices_dir=str(voices))
        wav, _, _ = svc.synthesize("Hallo.", "chatterbox:carsten")
        assert wav[:4] == b"RIFF" and cb.device == "cpu"
        assert cb._conds["carsten"].endswith("@cpu")       # Referenz neu auf der CPU berechnet
