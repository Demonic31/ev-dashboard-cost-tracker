#!/usr/bin/env bash
# Bascule la PREPROD : « on » relance le dashboard (code du worktree), « off » l'arrête et met à sa place
# la page « PREPROD en pause », pour que le Synology n'affiche pas sa propre page d'erreur.
set -euo pipefail
cd "$(dirname "$0")/.."
dc() { docker compose --env-file preprod/.env -f preprod/docker-compose.yml --profile pause "$@"; }
case "${1:-}" in
  on)  dc stop pause && dc up -d --build e208-dashboard ;;
  off) dc stop e208-dashboard && dc up -d --build pause ;;
  *)   echo "usage : $0 on|off" >&2; exit 1 ;;
esac
