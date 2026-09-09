#!/usr/bin/env bash
# Lint-Gate fuer scriptTelios-Backend (v19.21, Sprint S2).
# Aufruf: cd backend && bash scripts/lint_gate.sh
# Exit-Code != 0 blockiert das Ausliefern eines Patches (commit_patch.sh).
#
# Regeln: siehe ruff.toml (E722, F, B, ASYNC, RUF006). Neue Verstoesse
# duerfen nicht eingecheckt werden; bestehende sind seit S2 auf 0.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v ruff >/dev/null 2>&1; then
    echo "ruff fehlt: pip install ruff  (Version siehe requirements.txt)" >&2
    exit 2
fi

echo "[lint_gate] ruff check app scripts"
ruff check app scripts
echo "[lint_gate] OK - keine Verstoesse"
