# observability-stack

**Technology:** Prometheus, Grafana, OpenTelemetry, IBM Instana

**LLD component:** `cmp:observability-stack`

Centralised observability — Prometheus metrics, Grafana SLO dashboards (T1–T5, ≤60s lag), OpenTelemetry traces, IBM Instana distributed tracing, adoption metrics (A1–A7, ≤1h lag, 13-month retention)

---

## Adoption Event Pipeline (US-019 · US-020 · BR-12)

Two ingestion paths feed the `AdoptionEvent_CL` table in Azure Monitor Log Analytics workspace **law-ifos-euwe-pr**:

1. **Planning Cockpit (US-019):** emits OTLP session events via the OpenTelemetry SDK; received by the OTel Collector on port 4317 (TLS 1.3).  `component='planning-cockpit'`.
2. **Line Command (US-020):** emits structured AdoptionEvents to the Kafka topic `factory.munich.line-command.adoption.v1` (Avro, schema-registry validated); consumed by the OTel Collector's Kafka receiver, consumer group `line-command-adoption-consumer`.  `component='line-command'`.

**Retention:** 13 months (NFR-18) — enforced via `azurerm_log_analytics_workspace_table.adoption_event_cl` (395 days).

**PII / Works Council §87 BetrVG:** The OTel Collector strips `biometric_data`, `gaze_vector`, and `eye_tracking` fields before any event reaches the LAW table.  All analytics exports aggregate at station/shift level — no operator-identifiable data in exported datasets.

### Gate 1 Pre-built Queries

Six named saved queries are provisioned in `law-ifos-euwe-pr` (category `Gate1-Adoption`).  Product Ops Analysts run them directly in the Log Analytics portal.  Default time range: **Nov 1–28, 2025**.

| Query name | Gate 1 metric | Target | Description |
|---|---|---|---|
| `planner_wau` | **A3** — Planner WAU | ≥90% | Distinct `userId` per week with ≥1 meaningful action OR session ≥10 min.  Login-only sessions excluded. |
| `sequence_lock_pct` | **A1** — Sequence Lock % | ≥95% | % of Monday lock windows with ≥1 `publish_confirmed` event. |
| `override_rate` | **A2** — Override Rate | — | Count of `override_applied` events per calendar week. |
| `line_command_shift_lead_wau` | **A4** — Shift Lead WAU | **≥85%** | Distinct `shift_lead` userId per week with ≥1 meaningful action (`ALERT_ACKNOWLEDGED`, `HANDOVER_SUBMITTED`, `AR_SESSION_STARTED`, `TORQUE_LOG_SUBMITTED`).  Login-only days excluded. |
| `line_command_associate_wau` | **A5** — Associate WAU | **≥70%** | Distinct `assembly_associate` userId per week with ≥1 meaningful action.  Login-only days excluded. |
| `ar_device_adoption` | **A6** — AR Device Adoption | — | AR session counts by `deviceType` (`HoloLens2` / `iPad`) per `stationId` per week.  Asserts no biometric/gaze fields present. |

All six queries are wired as panels in the **Adoption Metrics (A1–A7)** Grafana dashboard (`grafana/dashboards/adoption.json`, uid `ifos-adoption-gate1`).

### WAU login-only exclusion rule (US-020 AC-19b649cd)

For Line Command, a user-day counts as active only when at least one `AdoptionEvent` for that `userId` on that calendar day has `actionType IN ('ALERT_ACKNOWLEDGED', 'HANDOVER_SUBMITTED', 'AR_SESSION_STARTED', 'TORQUE_LOG_SUBMITTED')`.  A kiosk-login-only session (no subsequent meaningful action) does **not** count as an active day and is excluded from the WAU count.

This rule is verified by the integration tests in `tests/test_wau_login_only_exclusion.py`.

### Files

| Path | Purpose |
|---|---|
| `otel-collector/otel-collector.yaml` | OTel Collector pipeline — OTLP gRPC receiver (port 4317) + Kafka receiver (line-command adoption topic), PII redaction, Azure Monitor exporter |
| `queries/adoption_queries.kql` | KQL source for all six pre-built queries |
| `grafana/dashboards/adoption.json` | Grafana dashboard — six panels: A1–A3 (Planning Cockpit) + A4–A6 (Line Command) |
| `tests/test_wau_login_only_exclusion.py` | Integration tests for WAU login-only exclusion rule |
