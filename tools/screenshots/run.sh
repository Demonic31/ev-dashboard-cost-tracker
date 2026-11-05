#!/bin/sh
# Régénère les captures du README (docs/screenshots/) avec des données fictives.
# Usage : tools/screenshots/run.sh [URL du dashboard]   (défaut : http://localhost:8080/)
# Seuls le HTML, le CSS et le JS sont lus sur le dashboard : /api/data est remplacé par le jeu fictif.
# Prérequis : python3 et Docker (Node n'est pas nécessaire sur l'hôte).
set -eu
DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$DIR/../.." && pwd)
URL=${1:-http://localhost:8080/}
PW=1.48.0
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
python3 "$DIR/gen_demo.py" "$TMP/data.json"
docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$DIR":/tools:ro -v "$TMP":/data:ro -v "$ROOT/docs/screenshots":/out \
  -e BASE_URL="$URL" -e DATA=/data/data.json -e OUT=/out -e NODE_PATH=/tmp/w/node_modules \
  mcr.microsoft.com/playwright:v$PW-jammy \
  sh -c "npm i -s --prefix /tmp/w playwright@$PW >/dev/null 2>&1 && node /tools/shots.js"
