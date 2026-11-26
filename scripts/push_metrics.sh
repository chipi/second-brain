#!/bin/sh
# Push a Prometheus text file (from `sb build|fetch|fulltext --metrics-file FILE`) to VictoriaMetrics.
# usage: scripts/push_metrics.sh FILE URL      e.g. URL=http://victoriametrics:8428/api/v1/import/prometheus
set -eu
[ $# -eq 2 ] || { echo "usage: $0 FILE URL" >&2; exit 2; }
[ -r "$1" ] || { echo "push_metrics: cannot read $1" >&2; exit 2; }
curl -fsS --max-time 30 --data-binary "@$1" "$2"
