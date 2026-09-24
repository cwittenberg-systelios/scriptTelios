#!/usr/bin/env bash
# setup_tts.sh - richtet den Vorlese-Dienst auf dem Pod ein (v19.35).
#
#   bash backend/scripts/setup_tts.sh [--chatterbox]
#
# Eigenes venv (/workspace/venv-tts), damit piper-tts (onnxruntime) und
# chatterbox-tts (torch==2.6.0) das Backend-venv nicht veraendern.
#   - piper-tts + Stimme de_DE-thorsten-high  -> /workspace/tts
#   - --chatterbox: torch/torchaudio 2.6.0 (CPU-Wheels, ~1 GB) + chatterbox-tts;
#     die Modellgewichte (~2-3 GB) laedt der Dienst beim ersten Satz von
#     HuggingFace (HF_HOME, Default /workspace/hf-cache).
# Danach in /workspace/.env: TTS_ENABLED=true (+ TTS_CHATTERBOX_ENABLED=true)
# und runpod-start.sh bzw. das Backend neu starten.
set -euo pipefail

VENV="${TTS_VENV:-/workspace/venv-tts}"
VOICE_DIR="${TTS_VOICE_DIR:-/workspace/tts}"
VOICE="${TTS_PIPER_VOICE_NAME:-de_DE-thorsten-high}"
WITH_CB=0
[ "${1:-}" = "--chatterbox" ] && WITH_CB=1

echo "[tts] venv: $VENV"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q piper-tts
mkdir -p "$VOICE_DIR"
if [ ! -f "$VOICE_DIR/$VOICE.onnx" ]; then
    echo "[tts] Stimme $VOICE laden"
    "$VENV/bin/python" -m piper.download_voices --data-dir "$VOICE_DIR" "$VOICE"
fi
ls -l "$VOICE_DIR/$VOICE.onnx"

if [ "$WITH_CB" = 1 ]; then
    echo "[tts] Chatterbox (CPU) installieren - dauert einige Minuten"
    "$VENV/bin/pip" install -q torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
    "$VENV/bin/pip" install -q chatterbox-tts
    "$VENV/bin/python" -c "import chatterbox, torch; print('[tts] chatterbox ok, torch', torch.__version__)"
    echo "[tts] Chatterbox-Gewichte vorab laden (~2-3 GB, HF_HOME=${HF_HOME:-/workspace/hf-cache})"
    HF_HOME="${HF_HOME:-/workspace/hf-cache}" "$VENV/bin/python" - <<'PY'
from chatterbox.mtl_tts import ChatterboxMultilingualTTS as M
try:
    M.from_pretrained(device="cpu", t3_model="v3")
except TypeError:
    M.from_pretrained(device="cpu")
print("[tts] Chatterbox-Gewichte im Cache")
PY
fi
echo "[tts] fertig. In /workspace/.env: TTS_ENABLED=true$([ "$WITH_CB" = 1 ] && echo ' und TTS_CHATTERBOX_ENABLED=true')"
