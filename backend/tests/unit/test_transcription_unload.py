"""v19.5: Whisper+pyannote VRAM-Freigabe nach Transkription (_free_gpu_after_transcription)."""
from app.services import transcription as tr
from app.core.config import settings


def test_unload_true_raeumt_whisper_und_pyannote(monkeypatch):
    monkeypatch.setattr(settings, "WHISPER_UNLOAD_AFTER", True, raising=False)
    monkeypatch.setattr(tr, "_model_cache", {"k": object()}, raising=False)

    called = {}
    class FakePipe:
        def to(self, dev): called["to"] = str(dev)
    monkeypatch.setattr(tr, "_diarization_pipeline", FakePipe(), raising=False)

    tr._free_gpu_after_transcription()

    assert tr._model_cache == {}                 # Whisper geleert
    assert tr._diarization_pipeline is None       # pyannote freigegeben
    # .to("cpu") ist best-effort und nur wenn torch verfuegbar -> hier nicht asserten


def test_unload_false_behaelt_pyannote(monkeypatch):
    monkeypatch.setattr(settings, "WHISPER_UNLOAD_AFTER", False, raising=False)
    monkeypatch.setattr(tr, "_model_cache", {"k": object()}, raising=False)
    pipe = object()
    monkeypatch.setattr(tr, "_diarization_pipeline", pipe, raising=False)

    tr._free_gpu_after_transcription()

    assert tr._model_cache == {}                  # Whisper-Cache immer geleert
    assert tr._diarization_pipeline is pipe        # pyannote bleibt (early return)


def test_unload_true_ohne_pyannote_kein_crash(monkeypatch):
    # pyannote nie geladen (None) -> Helper darf nicht crashen.
    monkeypatch.setattr(settings, "WHISPER_UNLOAD_AFTER", True, raising=False)
    monkeypatch.setattr(tr, "_model_cache", {}, raising=False)
    monkeypatch.setattr(tr, "_diarization_pipeline", None, raising=False)

    tr._free_gpu_after_transcription()   # darf einfach durchlaufen

    assert tr._diarization_pipeline is None
