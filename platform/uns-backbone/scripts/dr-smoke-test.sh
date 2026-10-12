#!/usr/bin/env bash
# DR smoke-test script — exercises all three consoles + UNS backbone + AI serving
# against the Germany North (DR) Application Gateway endpoints directly.
# Direct DR endpoints are used (not Traffic Manager FQDN) so the test validates
# DR infrastructure before the Traffic Manager failover switch is committed.
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed; details logged to stderr
#
# Usage:
#   export DR_BASE_URL=https://apigw-dr.ifos.bmwgroup.net
#   export DR_SMOKE_TOKEN=<jwt-from-vault>
#   bash platform/uns-backbone/scripts/dr-smoke-test.sh
#
# Realises: US-025 · BR-16 (E2E-010)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — all values read from environment; no hardcoded secrets
# ---------------------------------------------------------------------------

DR_BASE_URL="${DR_BASE_URL:?DR_BASE_URL must be set to the Germany North Application Gateway base URL}"
DR_SMOKE_TOKEN="${DR_SMOKE_TOKEN:?DR_SMOKE_TOKEN must be set to a valid JWT}"
# Optional: path to CA bundle for self-signed DR certs
DR_CA_CERT="${DR_CA_CERT:-}"
# RTO budget remaining (seconds). Script enforces total wall-clock ≤ this value.
RTO_BUDGET_SECONDS="${RTO_BUDGET_SECONDS:-14400}"  # 4 h default
# Timeout per HTTP check (seconds)
CHECK_TIMEOUT_SECONDS="${CHECK_TIMEOUT_SECONDS:-30}"
# SPIRE socket path on the DR cluster (verified via kubectl exec)
SPIRE_AGENT_SOCKET="${SPIRE_AGENT_SOCKET:-/run/spire/sockets/agent.sock}"

SCRIPT_START=$(date +%s)
FAILURES=0

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

log_info()  { printf '[INFO]  %s\n' "$*" >&2; }
log_ok()    { printf '[OK]    %s\n' "$*" >&2; }
log_fail()  { printf '[FAIL]  %s\n' "$*" >&2; FAILURES=$((FAILURES + 1)); }

curl_opts() {
  local opts=(-sS --max-time "${CHECK_TIMEOUT_SECONDS}" -o /dev/null -w "%{http_code}")
  [[ -n "${DR_CA_CERT}" ]] && opts+=(--cacert "${DR_CA_CERT}")
  printf '%s\n' "${opts[@]}"
}

check_http() {
  local label="$1" url="$2" expected_status="$3"
  local extra_args=("${@:4}")

  local status
  # shellcheck disable=SC2046
  status=$(curl $(curl_opts) "${extra_args[@]}" \
    -H "Authorization: Bearer ${DR_SMOKE_TOKEN}" \
    "${url}" 2>&1) || true

  if [[ "${status}" == "${expected_status}" ]]; then
    log_ok "${label} → HTTP ${status}"
  else
    log_fail "${label} → expected HTTP ${expected_status}, got '${status}'"
  fi
}

elapsed_seconds() {
  echo $(( $(date +%s) - SCRIPT_START ))
}

assert_within_rto() {
  local elapsed
  elapsed=$(elapsed_seconds)
  if [[ "${elapsed}" -gt "${RTO_BUDGET_SECONDS}" ]]; then
    log_fail "RTO budget exceeded: elapsed ${elapsed}s > ${RTO_BUDGET_SECONDS}s"
  else
    log_ok "RTO check: elapsed ${elapsed}s (budget ${RTO_BUDGET_SECONDS}s)"
  fi
}

# ---------------------------------------------------------------------------
# Check 1 — Planning Cockpit /health → 200 (E2E-010a)
# ---------------------------------------------------------------------------

log_info "=== Check 1: Planning Cockpit health ==="
check_http \
  "Planning Cockpit GET /health" \
  "${DR_BASE_URL}/planning/health" \
  "200"

# ---------------------------------------------------------------------------
# Check 2 — Line Command OEE display endpoint → 200 (E2E-010b)
# ---------------------------------------------------------------------------

log_info "=== Check 2: Line Command OEE display ==="
check_http \
  "Line Command GET /oee" \
  "${DR_BASE_URL}/line-command/oee" \
  "200"

# ---------------------------------------------------------------------------
# Check 3 — Quality Studio Process Replay endpoint → 200 (E2E-010c)
# ---------------------------------------------------------------------------

log_info "=== Check 3: Quality Studio Process Replay ==="
check_http \
  "Quality Studio GET /process-replay/health" \
  "${DR_BASE_URL}/quality/process-replay/health" \
  "200"

# ---------------------------------------------------------------------------
# Check 4 — UNS backbone Kafka bootstrap reachable via Kafka Admin API
# ---------------------------------------------------------------------------

log_info "=== Check 4: UNS backbone Kafka broker reachable ==="
DR_KAFKA_REST="${DR_KAFKA_REST:-${DR_BASE_URL}/kafka-rest}"
check_http \
  "Kafka REST proxy GET /v3/clusters" \
  "${DR_KAFKA_REST}/v3/clusters" \
  "200"

# ---------------------------------------------------------------------------
# Check 5 — Schema Registry reachable and reports subjects
# ---------------------------------------------------------------------------

log_info "=== Check 5: Schema Registry reachable ==="
DR_SCHEMA_REGISTRY_URL="${DR_SCHEMA_REGISTRY_URL:-${DR_BASE_URL}/schema-registry}"
check_http \
  "Schema Registry GET /subjects" \
  "${DR_SCHEMA_REGISTRY_URL}/subjects" \
  "200"

# ---------------------------------------------------------------------------
# Check 6 — AI model serving: SequenceOptimizer InferenceService ready
# ---------------------------------------------------------------------------

log_info "=== Check 6: AI serving SequenceOptimizer ready ==="
DR_AI_SERVING_URL="${DR_AI_SERVING_URL:-${DR_BASE_URL}/ai-serving}"
check_http \
  "KServe GET /v2/models/sequence-optimizer/ready" \
  "${DR_AI_SERVING_URL}/v2/models/sequence-optimizer/ready" \
  "200"

# ---------------------------------------------------------------------------
# Check 7 — RTO compliance: all checks completed within budget
# ---------------------------------------------------------------------------

log_info "=== Check 7: RTO budget compliance ==="
assert_within_rto

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

ELAPSED=$(elapsed_seconds)
log_info "=== Smoke test complete: ${FAILURES} failure(s), elapsed ${ELAPSED}s ==="

if [[ "${FAILURES}" -gt 0 ]]; then
  printf '[RESULT] FAIL — %d check(s) failed after %ds\n' "${FAILURES}" "${ELAPSED}" >&2
  exit 1
fi

printf '[RESULT] PASS — all DR smoke checks passed in %ds\n' "${ELAPSED}" >&2
exit 0
