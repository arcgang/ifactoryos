# observability-stack

**Technology:** Prometheus, Grafana, OpenTelemetry, IBM Instana

**LLD component:** `cmp:observability-stack`

Centralised observability — Prometheus metrics, Grafana SLO dashboards (T1–T5, ≤60s lag), OpenTelemetry traces, IBM Instana distributed tracing, adoption metrics (A1–A7, ≤1h lag, 13-month retention)

---

## Adoption Event Pipeline (US-019 · BR-12)

Planning Cockpit emits OTLP session events (login, session_start, optimizer_run, override_applied, publish_confirmed, session_end) via the OpenTelemetry SDK.  The OTel Collector receives them on port 4317 (TLS 1.3) and forwards them to the Azure Monitor Log Analytics workspace **law-ifos-euwe-pr**, table `AdoptionEvent_CL`.

**Retention:** 13 months (NFR-18) — enforced via `azurerm_log_analytics_workspace_table.adoption_event_cl` (395 days).

### Gate 1 Pre-built Queries

Three named saved queries are provisioned in `law-ifos-euwe-pr` (category `Gate1-Adoption`).  Product Ops Analysts run them directly in the Log Analytics portal — no code, ETL, or manual data extraction required.  Default time range: **Nov 1–28, 2025** (Gate 1 measurement window).

| Query name | Gate 1 metric | Description |
|---|---|---|
| `planner_wau` | **A3** — Planner WAU | Distinct `userId` values per calendar week where the planner had ≥1 meaningful action (`optimizer_run`, `override_applied`, or `publish_confirmed`) OR a session ≥10 minutes.  Login-only sessions under 10 minutes with no meaningful action are excluded from the active-user count. |
| `sequence_lock_pct` | **A1** — Sequence Lock % | Percentage of Monday lock windows (calendar weeks W44–W47, 2025) where at least one `publish_confirmed` event was emitted.  Formula: `weeks_with_publish / 4 × 100`.  Target ≥95%. |
| `override_rate` | **A2** — Override Rate | Count of `override_applied` events per calendar week in the measurement window. |

All three queries are also wired as panels in the **Adoption Metrics (A1–A7)** Grafana dashboard (`grafana/dashboards/adoption.json`, uid `ifos-adoption-gate1`), provisioned via the `adoption-metrics-dashboard` ConfigMap.

### Session filter logic

The `planner_wau` query enforces the Gate 1 measurement definition:

- A session is **included** if `session_duration_minutes ≥ 10` **OR** `meaningful_action_count > 0`.
- A session is **excluded** if it contains only a `login` / `session_start` event and ends in under 10 minutes with no optimizer_run, override_applied, or publish_confirmed event.

`sessionStartedAt` is emitted by planning-cockpit at `session_start`; `sessionDurationSeconds` is emitted at `session_end`.

### Files

| Path | Purpose |
|---|---|
| `otel-collector/otel-collector.yaml` | OTel Collector pipeline — OTLP gRPC receiver (port 4317, TLS 1.3), PII redaction processor, Azure Monitor exporter |
| `queries/adoption_queries.kql` | KQL source for all three pre-built queries (also committed for review/diff purposes) |
| `grafana/dashboards/adoption.json` | Grafana dashboard — three panels: Planner WAU (A3), Sequence Lock % (A1), Override Rate (A2) |
