#!/usr/bin/env bash
# setup_tts.sh - richtet den Vorlese-Dienst auf dem Pod ein (v19.35).
#
#   bash backend/scripts/setup_tts.sh [--chatterbox] [--cuda | --cpu-torch]
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
WITH_CB=0; WITH_CUDA=0; WITH_CPU=0
for arg in "$@"; do
    case "$arg" in
        --chatterbox) WITH_CB=1 ;;
        # v19.40: torch mit CUDA fuer Chatterbox auf der GPU (TTS_CHATTERBOX_DEVICE=cuda).
        # Die RTX PRO 4500 ist Blackwell (sm_120) -> erst ab torch 2.7 / CUDA 12.8.
        # chatterbox-tts pinnt torch==2.6.0; pip warnt darueber, laeuft aber.
        # ~4-5 GB zusaetzlich (CUDA-Bibliotheken).
        --cuda) WITH_CUDA=1 ;;
        --cpu-torch) WITH_CPU=1 ;;   # zurueck auf die CPU-Variante (spart den Platz wieder)
        *) echo "unbekannte Option: $arg"; exit 2 ;;
    esac
done

echo "[tts] venv: $VENV"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
# v19.40.1: Reste abgebrochener Installationen ("~orch" -> Warnung
# "Ignoring invalid distribution") wegraeumen
find "$VENV"/lib/python3*/site-packages -maxdepth 1 -name '~*' -exec rm -rf {} + 2>/dev/null || true
# Grosse Pakete (torch ~1 GB Download, ~4 GB entpackt) mit Fortschritt statt -q;
# auf dem Network Volume dauert allein das Entpacken 10-20 Min.
PIP_BIG=("$VENV/bin/pip" install --progress-bar on)
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
    "${PIP_BIG[@]}" torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
    "${PIP_BIG[@]}" chatterbox-tts
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
if [ "$WITH_CUDA" = 1 ]; then
    echo "[tts] torch 2.7.1 mit CUDA 12.8 installieren (Blackwell) - Download ~1 GB, Entpacken 10-20 Min., nicht abbrechen"
    "${PIP_BIG[@]}" torch==2.7.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu128
    "$VENV/bin/python" - <<'PY'
import torch
assert torch.cuda.is_available(), "torch sieht keine GPU"
x = torch.ones(4, device="cuda") * 2          # prueft, ob Kernel fuer diese GPU da sind
print("[tts] CUDA ok:", torch.__version__, torch.cuda.get_device_name(), torch.cuda.get_device_capability(), float(x.sum()))
PY
    echo "[tts] In /workspace/.env: TTS_CHATTERBOX_DEVICE=cuda, dann Dienst neu starten"
fi
if [ "$WITH_CPU" = 1 ]; then
    "${PIP_BIG[@]}" torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
    "$VENV/bin/pip" uninstall -y -q $("$VENV/bin/pip" list 2>/dev/null | awk '/^nvidia-/{print $1}') triton 2>/dev/null || true
    echo "[tts] zurueck auf torch-CPU; TTS_CHATTERBOX_DEVICE=cpu setzen"
fi
echo "[tts] fertig. In /workspace/.env: TTS_ENABLED=true$([ "$WITH_CB" = 1 ] && echo ' und TTS_CHATTERBOX_ENABLED=true')"
