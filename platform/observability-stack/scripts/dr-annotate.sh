#!/usr/bin/env bash
# dr-annotate.sh — Mark DR drill timestamps on the Grafana dashboard and push
# corresponding metrics to the Prometheus pushgateway.
#
# Called by the smoke-test suite (platform/uns-backbone/tests/test_dr_smoke.py)
# at two points:
#   failure_declared  — immediately after the simulated failure is triggered
#   service_confirmed — once all smoke tests pass post-recovery
#
# Both events are recorded as:
#   1. A Grafana annotation (enables automatic RTO calculation on the dashboard)
#   2. A Prometheus pushgateway metric (enables dr:rto_minutes / dr:rpo_minutes
#      recording rules and the DRRPOBreached / DRRTOBreached alerts)
#
# Required environment variables:
#   GRAFANA_URL          e.g. https://grafana.ifos.munich.internal
#   GRAFANA_API_KEY      Service-account token with Editor role
#   PUSHGATEWAY_URL      e.g. http://pushgateway.monitoring.svc.cluster.local:9091
#   DR_DRILL_ID          Unique identifier for this drill run (e.g. 2026-10-12-001)
#
# Optional environment variables:
#   GRAFANA_DASHBOARD_UID  Default: dr-t10-slo-001
#   KAFKA_BOOTSTRAP        Default: uns-kafka-bootstrap.confluent.svc.cluster.local:9092
#   KAFKA_TOPIC_PATTERN    Default: bmw\.munich\.uns\.ddata\..+\.v1
#   DRY_RUN                Set to "true" to print actions without executing them
#
# Usage:
#   dr-annotate.sh failure_declared
#   dr-annotate.sh service_confirmed [--last-uns-ts <unix_seconds>] [--first-uns-ts <unix_seconds>]
#
# Exit codes:
#   0  success
#   1  missing required environment variable
#   2  Grafana annotation POST failed
#   3  pushgateway POST failed
#   4  unknown event type
#   5  invalid argument

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DASHBOARD_UID="${GRAFANA_DASHBOARD_UID:-dr-t10-slo-001}"
PUSHGATEWAY_JOB="dr_drill"

# ---------------------------------------------------------------------------
# Validate required environment variables
# ---------------------------------------------------------------------------

_require_env() {
  local var="$1"
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: Required environment variable ${var} is not set." >&2
    echo "       See script header for the full list of required variables." >&2
    exit 1
  fi
}

_require_env GRAFANA_URL
_require_env GRAFANA_API_KEY
_require_env PUSHGATEWAY_URL
_require_env DR_DRILL_ID

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_now_unix() {
  # Returns current UTC epoch in seconds. Avoids date -d on macOS vs Linux by
  # using Python which is available in the smoke-test container.
  python3 -c "import time; print(int(time.time()))"
}

_now_iso() {
  python3 -c "import datetime; print(datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'))"
}

_log() {
  echo "[dr-annotate] $*"
}

_dry_run_guard() {
  if [[ "${DRY_RUN:-false}" == "true" ]]; then
    _log "[DRY RUN] Would execute: $*"
    return 0
  fi
  "$@"
}

# ---------------------------------------------------------------------------
# Grafana annotation
# ---------------------------------------------------------------------------

_post_grafana_annotation() {
  local event="$1"   # failure_declared | service_confirmed
  local ts_ms="$2"   # epoch milliseconds
  local text="$3"
  local tags="$4"    # JSON array string e.g. '["dr-drill","failure_declared"]'

  local payload
  payload=$(printf '{"dashboardUID":"%s","time":%s,"text":"%s","tags":%s}' \
    "${DASHBOARD_UID}" "${ts_ms}" "${text}" "${tags}")

  _log "Posting Grafana annotation: event=${event} dashboard=${DASHBOARD_UID}"

  local http_code
  http_code=$(_dry_run_guard curl \
    --silent \
    --output /dev/null \
    --write-out "%{http_code}" \
    --request POST \
    --header "Content-Type: application/json" \
    --header "Authorization: Bearer ${GRAFANA_API_KEY}" \
    --data "${payload}" \
    "${GRAFANA_URL}/api/annotations" \
  ) || { echo "ERROR: curl failed posting Grafana annotation" >&2; exit 2; }

  if [[ "${DRY_RUN:-false}" != "true" ]] && [[ "${http_code}" != "200" ]]; then
    echo "ERROR: Grafana annotation POST returned HTTP ${http_code}" >&2
    exit 2
  fi

  _log "Grafana annotation posted (HTTP ${http_code:-DRY_RUN})"
}

# ---------------------------------------------------------------------------
# Prometheus pushgateway metrics
# ---------------------------------------------------------------------------

_push_metric() {
  local metric_name="$1"
  local metric_value="$2"
  local help_text="$3"
  local extra_labels="${4:-}"   # e.g. drill_id="2026-10-12-001"

  local labels="drill=\"true\",drill_id=\"${DR_DRILL_ID}\""
  if [[ -n "${extra_labels}" ]]; then
    labels="${labels},${extra_labels}"
  fi

  local body
  body=$(printf '# HELP %s %s\n# TYPE %s gauge\n%s{%s} %s\n' \
    "${metric_name}" "${help_text}" \
    "${metric_name}" \
    "${metric_name}" "${labels}" "${metric_value}")

  _log "Pushing metric ${metric_name}=${metric_value} to pushgateway"

  local http_code
  http_code=$(_dry_run_guard curl \
    --silent \
    --output /dev/null \
    --write-out "%{http_code}" \
    --request POST \
    --header "Content-Type: text/plain" \
    --data-binary "${body}" \
    "${PUSHGATEWAY_URL}/metrics/job/${PUSHGATEWAY_JOB}/instance/${DR_DRILL_ID}" \
  ) || { echo "ERROR: curl failed pushing to pushgateway" >&2; exit 3; }

  if [[ "${DRY_RUN:-false}" != "true" ]] && [[ "${http_code}" != "200" && "${http_code}" != "202" ]]; then
    echo "ERROR: Pushgateway POST returned HTTP ${http_code}" >&2
    exit 3
  fi

  _log "Metric pushed (HTTP ${http_code:-DRY_RUN})"
}

# ---------------------------------------------------------------------------
# Query last UNS Kafka message timestamp before failure via Prometheus API
# ---------------------------------------------------------------------------

_query_last_uns_message_ts() {
  local prometheus_url="${GRAFANA_URL/grafana./prometheus.}"
  prometheus_url="${prometheus_url:-${PUSHGATEWAY_URL%:9091}}"

  local topic_pattern="${KAFKA_TOPIC_PATTERN:-bmw\\.munich\\.uns\\.ddata\\..+\\.v1}"
  local promql="max(kafka_server_brokertopicmetrics_messagesin_total{topic=~\"${topic_pattern}\"})"

  _log "Querying Prometheus for last UNS message timestamp"

  local ts
  ts=$(_dry_run_guard curl \
    --silent \
    --get \
    --data-urlencode "query=${promql}" \
    "${GRAFANA_URL%/grafana*}/prometheus/api/v1/query" \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
results = data.get('data', {}).get('result', [])
if results:
    print(int(float(results[0]['value'][0])))
else:
    print(0)
") || echo "0"

  echo "${ts}"
}

# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

handle_failure_declared() {
  local now_unix
  now_unix=$(_now_unix)
  local now_ms=$(( now_unix * 1000 ))
  local now_iso
  now_iso=$(_now_iso)

  _log "Handling failure_declared at ${now_iso} (drill_id=${DR_DRILL_ID})"

  # Capture last UNS message timestamp immediately before failure
  local last_uns_ts
  last_uns_ts=$(_query_last_uns_message_ts)
  if [[ "${last_uns_ts}" == "0" ]]; then
    _log "WARNING: Could not query last UNS message timestamp; using current time as fallback"
    last_uns_ts="${now_unix}"
  fi

  # Grafana annotation
  _post_grafana_annotation \
    "failure_declared" \
    "${now_ms}" \
    "DR Drill ${DR_DRILL_ID}: failure declared at ${now_iso}" \
    '["dr-drill","failure_declared"]'

  # Pushgateway metrics
  _push_metric \
    "dr_failure_declared_timestamp_seconds" \
    "${now_unix}" \
    "Unix timestamp when DR failure was declared for drill drill_id"

  _push_metric \
    "dr_last_uns_message_timestamp_before_failure_seconds" \
    "${last_uns_ts}" \
    "Unix timestamp of the last UNS DDATA Kafka message produced before the declared failure"

  _log "failure_declared event recorded: ts=${now_unix} last_uns_ts=${last_uns_ts}"
  echo "${now_unix}"
}

handle_service_confirmed() {
  local last_uns_ts="${LAST_UNS_TS:-0}"
  local first_uns_ts="${FIRST_UNS_TS:-0}"

  # Parse optional CLI overrides
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --last-uns-ts)
        last_uns_ts="$2"; shift 2 ;;
      --first-uns-ts)
        first_uns_ts="$2"; shift 2 ;;
      *)
        echo "ERROR: Unknown argument: $1" >&2; exit 5 ;;
    esac
  done

  local now_unix
  now_unix=$(_now_unix)
  local now_ms=$(( now_unix * 1000 ))
  local now_iso
  now_iso=$(_now_iso)

  _log "Handling service_confirmed at ${now_iso} (drill_id=${DR_DRILL_ID})"

  # If first_uns_ts was not passed in, query current UNS message timestamp
  if [[ "${first_uns_ts}" == "0" ]]; then
    first_uns_ts=$(_query_last_uns_message_ts)
    if [[ "${first_uns_ts}" == "0" ]]; then
      _log "WARNING: Could not query first UNS message timestamp; using current time as fallback"
      first_uns_ts="${now_unix}"
    fi
  fi

  # Grafana annotation
  _post_grafana_annotation \
    "service_confirmed" \
    "${now_ms}" \
    "DR Drill ${DR_DRILL_ID}: service confirmed at ${now_iso}" \
    '["dr-drill","service_confirmed"]'

  # Pushgateway metrics
  _push_metric \
    "dr_service_confirmed_timestamp_seconds" \
    "${now_unix}" \
    "Unix timestamp when DR service recovery was confirmed for drill drill_id"

  _push_metric \
    "dr_first_uns_message_timestamp_after_recovery_seconds" \
    "${first_uns_ts}" \
    "Unix timestamp of the first UNS DDATA Kafka message produced after recovery confirmation"

  _log "service_confirmed event recorded: ts=${now_unix} first_uns_ts=${first_uns_ts}"
  echo "${now_unix}"
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <failure_declared|service_confirmed> [options]" >&2
  echo "  failure_declared" >&2
  echo "  service_confirmed [--last-uns-ts <unix_seconds>] [--first-uns-ts <unix_seconds>]" >&2
  exit 5
fi

EVENT="$1"
shift

case "${EVENT}" in
  failure_declared)
    handle_failure_declared "$@"
    ;;
  service_confirmed)
    handle_service_confirmed "$@"
    ;;
  *)
    echo "ERROR: Unknown event '${EVENT}'. Expected: failure_declared | service_confirmed" >&2
    exit 4
    ;;
esac
