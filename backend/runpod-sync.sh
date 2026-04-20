#!/usr/bin/env bash
set -euo pipefail

# RunPod 1-command workflow (PRO version)
# Supports: push, pull, sync
# Works with ssh.runpod.io proxy (RunPod-safe stream transfer)
#
# Usage:
#   runpod pull <remote_path> <local_path>
#   runpod push <local_path> <remote_path>
#   runpod sync <local_path> <remote_path>
#
# Example:
#   runpod pull /workspace/data ~/Downloads/data
#   runpod push ~/data/file.txt /workspace/file.txt
#   runpod sync ~/project /workspace/project

HOST="4a3przp1a60c8m-64411fdb@ssh.runpod.io"
KEY="$HOME/.ssh/id_ed25519"
SSH="ssh -t -i $KEY"

CMD=${1:-}
SRC=${2:-}
DST=${3:-}

if [[ -z "$CMD" || -z "$SRC" || -z "$DST" ]]; then
  echo "Usage: runpod pull|push|sync <src> <dst>"
  exit 1
fi

has_pv() {
  command -v pv >/dev/null 2>&1
}

stream_with_progress() {
  if has_pv; then
    pv
  else
    cat
  fi
}

is_file() {
  [[ -f "$1" ]]
}

# -------------------- PULL --------------------
if [[ "$CMD" == "pull" ]]; then

  echo "📥 Pulling from RunPod..."

  if [[ "$SRC" == *.* && "$SRC" != */ ]]; then
    mkdir -p "$(dirname "$DST")"
    $SSH "$HOST" "cat '$SRC'" > "$DST"
  else
    mkdir -p "$DST"
    $SSH "$HOST" "cd $(dirname "$SRC") && tar czf - $(basename "$SRC")" \
      | stream_with_progress \
      | tar xzf - -C "$DST"
  fi

# -------------------- PUSH --------------------
elif [[ "$CMD" == "push" ]]; then

  echo "📤 Pushing to RunPod..."

  if is_file "$SRC"; then
    $SSH "$HOST" "cat > '$DST'" < "$SRC"
  else
    tar czf - -C "$(dirname "$SRC")" "$(basename "$SRC")" \
      | stream_with_progress \
      | $SSH "$HOST" "cat > /tmp/upload.tar.gz && mkdir -p $(dirname "$DST") && tar xzf /tmp/upload.tar.gz -C $(dirname "$DST")"
  fi

# -------------------- SYNC --------------------
elif [[ "$CMD" == "sync" ]]; then

  echo "🔄 Sync mode (pull → push)..."

  echo "Step 1: Pull"
  $0 pull "$SRC" "$DST"

  echo "Step 2: Push"
  $0 push "$SRC" "$DST"

  echo "✅ Sync complete"

else
  echo "Unknown command: $CMD"
  echo "Use: pull | push | sync"
  exit 1
fi
