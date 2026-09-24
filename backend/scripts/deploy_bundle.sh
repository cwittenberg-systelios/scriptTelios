#!/usr/bin/env bash
# deploy_bundle.sh - Frontend-Bundle zum Cloudflare Worker hochladen (v19.32).
#
# Ersetzt den manuellen Upload von systelios.js als Confluence-Anhang. Laeuft
# auf jedem Rechner mit Internet (kein Intranet noetig); das Makro laedt das
# Bundle danach von ${SYSTELIOS_PROXY_BASE}/systelios.js.
#
#   backend/scripts/deploy_bundle.sh            # Upload backend/static/systelios.js
#   backend/scripts/deploy_bundle.sh --build    # vorher `npm run build` im frontend/
#   backend/scripts/deploy_bundle.sh --meta     # nur anzeigen, was der Worker hat
#   backend/scripts/deploy_bundle.sh --rollback # vorherige Fassung zurueckholen
#
# Konfiguration (Umgebung oder backend/.env, nicht im Repo):
#   SYSTELIOS_PROXY_BASE   z.B. https://systelios-proxy.<account>.workers.dev
#   BUNDLE_UPLOAD_SECRET   == Secret BUNDLE_UPLOAD_SECRET im Worker
#                          (Fallback im Worker: CONFLUENCE_SHARED_SECRET)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BUNDLE="$REPO/backend/static/systelios.js"
ENVFILE="$REPO/backend/.env"

if [[ -f "$ENVFILE" ]]; then
  # nur die beiden Variablen aus .env ziehen (kein `source` - .env kann Backend-Werte enthalten)
  _pb="$(grep -E '^SYSTELIOS_PROXY_BASE=' "$ENVFILE" | tail -1 | cut -d= -f2- | tr -d '"'"'" || true)"
  _sec="$(grep -E '^BUNDLE_UPLOAD_SECRET=' "$ENVFILE" | tail -1 | cut -d= -f2- | tr -d '"'"'" || true)"
  : "${SYSTELIOS_PROXY_BASE:=$_pb}"
  : "${BUNDLE_UPLOAD_SECRET:=$_sec}"
fi
: "${SYSTELIOS_PROXY_BASE:?SYSTELIOS_PROXY_BASE fehlt (Umgebung oder backend/.env)}"
BASE="${SYSTELIOS_PROXY_BASE%/}"

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

case "${1:-}" in
  --meta)
    curl -fsS "$BASE/systelios.js/meta" ; echo ; exit 0 ;;
  --rollback)
    : "${BUNDLE_UPLOAD_SECRET:?BUNDLE_UPLOAD_SECRET fehlt}"
    curl -fsS -X POST -H "X-Bundle-Secret: $BUNDLE_UPLOAD_SECRET" "$BASE/systelios.js/rollback" ; echo ; exit 0 ;;
  --build)
    ( cd "$REPO/frontend" && npm run build ) ;;
  "") ;;
  *) echo "Unbekannte Option: $1" >&2 ; exit 2 ;;
esac

: "${BUNDLE_UPLOAD_SECRET:?BUNDLE_UPLOAD_SECRET fehlt (Umgebung oder backend/.env)}"
[[ -f "$BUNDLE" ]] || { echo "Bundle fehlt: $BUNDLE (erst bauen: --build)" >&2 ; exit 1 ; }

SHA="$(sha256 "$BUNDLE")"
SIZE="$(wc -c < "$BUNDLE" | tr -d ' ')"
LABEL="$(grep -m1 -oE '^## \[v[0-9.]+\]' "$REPO/docs/CHANGELOG.md" 2>/dev/null | tr -d '#[] ' || true)"
USER_="$(git -C "$REPO" config user.name 2>/dev/null || whoami)"

echo "Upload: $BUNDLE ($SIZE Bytes, sha ${SHA:0:12}…, ${LABEL:-ohne Label}) -> $BASE/systelios.js"
RESP="$(curl -fsS -X POST \
  -H "Content-Type: text/javascript" \
  -H "X-Bundle-Secret: $BUNDLE_UPLOAD_SECRET" \
  -H "X-Bundle-Sha256: $SHA" \
  -H "X-Bundle-Version: $LABEL" \
  -H "X-Bundle-User: $USER_" \
  --data-binary "@$BUNDLE" \
  "$BASE/systelios.js")"
echo "$RESP"

# Gegenprobe: liefert der Worker die hochgeladene Pruefsumme?
GOT="$(curl -fsS "$BASE/systelios.js/meta" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("sha256",""))')"
if [[ "$GOT" == "$SHA" ]]; then
  echo "OK - Worker liefert sha ${GOT:0:12}… ; Version: $(curl -fsS "$BASE/systelios.js/meta" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))')"
else
  echo "FEHLER - Worker meldet sha ${GOT:0:12}…, erwartet ${SHA:0:12}…" >&2 ; exit 1
fi
