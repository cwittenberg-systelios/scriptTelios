#!/usr/bin/env bash
# pod_bundle.sh - Frontend-Bundle auf dem Pod bauen und ausliefern (v19.43).
#
#   pod_bundle.sh build    baut backend/static/systelios.js NUR, wenn sich der
#                          Inhalt der Quellen geaendert hat (Pruefsumme statt
#                          Datei-Zeitstempel - die loesten den Build bei jedem
#                          Start aus).
#   pod_bundle.sh deploy   laedt das Bundle zum Cloudflare Worker hoch (ueber
#                          deploy_bundle.sh), aber nur, wenn es sich seit der
#                          letzten Auslieferung vom Pod geaendert hat und der
#                          Worker es nicht schon hat. Ein vom Mac ausgeliefertes
#                          neueres Bundle wird so nicht bei jedem Pod-Start
#                          ueberschrieben.
#
# Aufgerufen von runpod-start.sh (Schritt 5). Fehler beim Ausliefern sind nie
# fatal - der Pod startet trotzdem.
#
# Umgebung (aus /workspace/.env bzw. backend/.env):
#   SYSTELIOS_PROXY_BASE, BUNDLE_UPLOAD_SECRET   wie deploy_bundle.sh
#   BUNDLE_AUTO_DEPLOY=false                     Ausliefern vom Pod abschalten
# Fuer Tests ueberschreibbar: FRONTEND_DIR, BACKEND_DIR, NPM, DEPLOY_SCRIPT
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${BACKEND_DIR:-$(cd "$HERE/.." && pwd)}"
FRONTEND_DIR="${FRONTEND_DIR:-$(cd "$BACKEND_DIR/../frontend" 2>/dev/null && pwd || echo "$BACKEND_DIR/../frontend")}"
STATIC_DIR="$BACKEND_DIR/static"
BUNDLE="$STATIC_DIR/systelios.js"
FP_FILE="$STATIC_DIR/.build-fingerprint"
DEPLOYED_FILE="$STATIC_DIR/.deployed-sha"
NPM="${NPM:-npm}"
DEPLOY_SCRIPT="${DEPLOY_SCRIPT:-$BACKEND_DIR/scripts/deploy_bundle.sh}"

OK="${OK:-[OK]    }"; GO="${GO:-[.....] }"; WARN="${WARN:-[WARN]  }"

# Als Array (nicht als Funktion) - xargs kann keine Shell-Funktionen aufrufen
if command -v sha256sum >/dev/null; then SHACMD=(sha256sum); else SHACMD=(shasum -a 256); fi
sha() { "${SHACMD[@]}" "$@"; }

# Wert aus der Umgebung, sonst aus backend/.env
envval() {
  local v="${!1:-}"
  if [[ -z "$v" && -f "$BACKEND_DIR/.env" ]]; then
    v="$(grep -E "^$1=" "$BACKEND_DIR/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'" || true)"
  fi
  printf '%s' "$v"
}

# Pruefsumme ueber Namen + Inhalt aller Quellen. prompt-defaults.jsx ist
# generiert (prebuild aus prompts.py/interview_sets.py) und zaehlt nicht.
fingerprint() {
  (
    cd "$BACKEND_DIR/.." 2>/dev/null || exit 0
    local fe be
    fe="$(realpath --relative-to=. "$FRONTEND_DIR" 2>/dev/null || echo "$FRONTEND_DIR")"
    be="$(realpath --relative-to=. "$BACKEND_DIR" 2>/dev/null || echo "$BACKEND_DIR")"
    find "$fe/src" "$fe/klinische-dokumentation.jsx" "$fe/index.html" "$fe/package.json" \
         "$fe/package-lock.json" "$fe"/vite.config.* \
         "$be/app/services/prompts.py" "$be/app/core/interview_sets.py" \
         "$be/scripts/export_prompt_defaults.py" \
         -type f ! -name 'prompt-defaults.jsx' -print0 2>/dev/null \
      | LC_ALL=C sort -z | xargs -0 -r "${SHACMD[@]}" | "${SHACMD[@]}" | cut -c1-32
  )
}

cmd_build() {
  local fp old
  fp="$(fingerprint)"
  old="$(cat "$FP_FILE" 2>/dev/null || true)"
  if [[ -f "$BUNDLE" && -n "$fp" && "$fp" == "$old" ]]; then
    echo "${OK}Bundle aktuell (Quellen unveraendert) – kein Rebuild"
    return 0
  fi
  if [[ ! -f "$BUNDLE" ]]; then echo "${GO}Bundle nicht vorhanden – wird gebaut..."
  else echo "${GO}Quellen geaendert – Bundle wird neu gebaut..."; fi
  mkdir -p "$STATIC_DIR"
  (
    cd "$FRONTEND_DIR" || exit 1
    if [[ ! -d node_modules || package.json -nt node_modules/.package-lock.json ]]; then
      echo "${GO}npm install..."
      "$NPM" install --silent || exit 1
    fi
    echo "${GO}npm run build..."
    "$NPM" run build
  ) || { echo "${WARN}Build fehlgeschlagen – Frontend evtl. nicht aktuell"; return 1; }
  if [[ -f "$BUNDLE" ]]; then
    echo "$fp" > "$FP_FILE"
    echo "${OK}Bundle erstellt: systelios.js ($(du -k "$BUNDLE" | cut -f1) KB)"
  else
    echo "${WARN}Bundle wurde nicht erstellt – Frontend evtl. nicht verfuegbar"
    return 1
  fi
}

cmd_deploy() {
  local base secret local_sha last remote
  base="$(envval SYSTELIOS_PROXY_BASE)"; secret="$(envval BUNDLE_UPLOAD_SECRET)"
  if [[ "$(envval BUNDLE_AUTO_DEPLOY | tr '[:upper:]' '[:lower:]')" == "false" ]]; then
    echo "${OK}Ausliefern vom Pod abgeschaltet (BUNDLE_AUTO_DEPLOY=false)"; return 0
  fi
  if [[ -z "$base" || -z "$secret" ]]; then
    echo "${OK}Kein Ausliefern (SYSTELIOS_PROXY_BASE/BUNDLE_UPLOAD_SECRET nicht gesetzt)"; return 0
  fi
  [[ -f "$BUNDLE" ]] || { echo "${WARN}Kein Bundle zum Ausliefern"; return 0; }
  local_sha="$(sha "$BUNDLE" | cut -d' ' -f1)"
  last="$(cat "$DEPLOYED_FILE" 2>/dev/null || true)"
  if [[ "$local_sha" == "$last" ]]; then
    echo "${OK}Bundle unveraendert seit der letzten Auslieferung (${local_sha:0:12}…)"; return 0
  fi
  remote="$(curl -fsS --max-time 10 "${base%/}/systelios.js/meta" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("sha256",""))' 2>/dev/null || true)"
  if [[ "$remote" == "$local_sha" ]]; then
    echo "$local_sha" > "$DEPLOYED_FILE"
    echo "${OK}Worker hat dieses Bundle schon (${local_sha:0:12}…)"; return 0
  fi
  echo "${GO}Bundle zum Worker ausliefern (${local_sha:0:12}…)..."
  if SYSTELIOS_PROXY_BASE="$base" BUNDLE_UPLOAD_SECRET="$secret" bash "$DEPLOY_SCRIPT" >"$STATIC_DIR/.deploy.log" 2>&1; then
    echo "$local_sha" > "$DEPLOYED_FILE"
    echo "${OK}Bundle ausgeliefert: $(tail -1 "$STATIC_DIR/.deploy.log")"
  else
    echo "${WARN}Ausliefern fehlgeschlagen (Pod laeuft trotzdem) – $STATIC_DIR/.deploy.log:"
    tail -3 "$STATIC_DIR/.deploy.log" | sed 's/^/        /'
  fi
  return 0
}

case "${1:-}" in
  build) cmd_build ;;
  deploy) cmd_deploy ;;
  fingerprint) fingerprint ;;
  *) echo "Aufruf: $0 build|deploy|fingerprint" >&2; exit 2 ;;
esac
