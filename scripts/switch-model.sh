#!/usr/bin/env bash
# Switch the loaded VLM through askcam-ui and wait until it is ready.  Usage: scripts/switch-model.sh <preset> [ui-url]
set -euo pipefail
M=$1; UI=${2:-http://localhost:8000}
curl -fs -X POST "$UI/api/models/load" -H 'Content-Type: application/json' -d "{\"model\":\"$M\"}" >/dev/null
sleep 2
for _ in $(seq 1 120); do
  r=$(curl -s "$UI/api/models" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['active'] or '-', d['switching'] or '-', d['error'] or '-')")
  set -- $r
  [ "$2" = "-" ] && [ "$3" != "-" ] && { echo "switch failed: $3"; exit 1; }
  [ "$2" = "-" ] && [ "$1" = "$M" ] && { echo "loaded: $M"; exit 0; }
  sleep 2
done
echo "timeout"; exit 1
