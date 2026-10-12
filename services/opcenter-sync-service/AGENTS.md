# opcenter-sync-service

LLD component `cmp:opcenter-sync-service` · Rust, Siemens OpCenter 10.2 REST/GraphQL API, Kafka producer, SPIFFE/SPIRE mTLS

Rust-based bidirectional adapter for Siemens OpCenter 10.2 — sequence lock publish (signed API key), OEE/Andon event subscription, 5s p95 delivery to UNS

## Stories

- `EN-opcenter-sync-service`
- `US-001`
- `US-004`
- `US-007`
- `US-008`
- `US-011`
- `US-013`
- `US-016`
- `US-017`
- `US-019`

## Endpoints

- `GET /health`
- `GET /andon/alerts`
- `GET /gate-results`
- `GET /oee/snapshots`
- `GET /sequence/jobs`
- `POST /sequence/publish`
- `GET /andon/alerts/{alertId}`
- `GET /sequence/jobs/{syncJobId}`
- `GET /gate-results/{gateResultId}`
- `POST /opcenter/sequence`

## Entities

- `ent:andon-alert`
- `ent:gate-result`
- `ent:oee-snapshot`
- `ent:opcenter-api-key-rotation`
- `ent:sync-job`

## Events

- `evt:bmw.munich.opcenter.andon.alert`
- `evt:bmw.munich.opcenter.oee.updated`
- `evt:bmw.munich.opcenter.sequence.published`
- `evt:bmw.munich.opcenter.gate_result.received`
- `evt:bmw.munich.opcenter.sequence.publish_failed`

## Acceptance criteria

- AC-680aba58 (US-001): Given the planner opens Constraint Radar on any Monday between 05:00 and 06:17, when the page loads, then paint capacity utilisation, body WIP queue depth, kitting readiness, and all scheduled maintenance windows for the next 7 days are displayed with data timestamps no older than 60 seconds from the UNS
- AC-4c26c424 (US-001): Given a constraint value crosses a configurable warning threshold, when the planner is viewing Constraint Radar, then the affected constraint is highlighted in amber with the threshold value and current value displayed side by side
- AC-64b0a4bb (US-001): Given the UNS latency exceeds 1,000ms p99, when Constraint Radar is open, then a staleness indicator is shown on each affected data tile with the last-known-good timestamp
- AC-bb537d8f (US-004): Given the planner clicks 'Publish to OpCenter' on a finalised sequence, when the publish completes, then a confirmation message shows the OpCenter sequence ID, the timestamp, and the source field value 'planning-cockpit' within 5 seconds
- AC-70528fee (US-004): Given a sequence upload is attempted via any method other than the Planning Cockpit API key, when OpCenter receives the upload, then OpCenter rejects it with an error code and the rejection is logged in the Planning Cockpit audit trail
- AC-b3df1a1d (US-004): Given the OpCenter API is unavailable at publish time, when the planner clicks Publish, then the sequence is queued locally with a visible 'Pending Publish' status and automatically retried every 30 seconds until confirmed, with the planner notified of the delay
- AC-5be353b7 (US-007): Given the Shift Lead logs into Line Command at shift start, when the Command Center loads, then OEE values for all 4 weld lines are displayed with data no older than 30 seconds, alongside all open predictive Andon alerts sorted by time-to-predicted-failure ascending
- AC-183ec0b0 (US-007): Given a predictive Andon alert is active, when the Shift Lead views the Command Center, then each alert shows the affected station, the predicted failure type, the minutes remaining to predicted failure (countdown updating every 60 seconds), and the recommended action
- AC-6a6f12c4 (US-007): Given the outgoing Shift Lead has completed a Handover Builder summary, when the incoming Shift Lead opens Command Center, then the handover notes are displayed as the first item in the alert queue with a 'New Handover' badge until acknowledged
- AC-7cf0baef (US-008): Given the Shift Lead selects a weld cell from the Command Center, when Station Detail opens, then the live weld current and voltage time-series traces from the Beckhoff PLC are displayed for the last 60 minutes with the predictive alert threshold overlaid as a reference line
- AC-20f08ac3 (US-008): Given a predictive alert is active for the selected station, when the Shift Lead views Station Detail, then the alert threshold crossing point is highlighted on the trace with the timestamp and the predicted time to failure
- AC-5a1d1b22 (US-008): Given the Shift Lead clicks 'Dispatch Maintenance' from Station Detail, when the action is confirmed, then the dispatch event is logged in the audit trail with the Shift Lead's user ID, station ID, alert ID, and timestamp
- AC-13685a24 (US-011): Given the associate completes a torque tightening at a Marriage or Final station, when the Beckhoff PLC records the torque/angle trace, then the value is published to the UNS and linked to the VIN digital thread within 2 seconds, and the AR overlay step indicator turns green
- AC-69c77dfd (US-011): Given a torque value is outside the specified tolerance for the VIN's option code, when the PLC records the out-of-tolerance value, then the AR overlay step indicator turns red and an alert is raised in Line Command for the Shift Lead within 30 seconds
- AC-b734032a (US-011): Given the UNS is temporarily unavailable, when a torque tightening occurs, then the value is cached locally on the edge gateway and published to the UNS within 60 seconds of connectivity restoration, with the associate's AR overlay showing a 'Pending Sync' indicator
- AC-aa9ca19f (US-013): Given the engineer selects a defect VIN and clicks 'Launch Process Replay', when the replay loads, then all 14 prep stage parameters, booth parameters, and climate data are displayed as aligned time-series on a single scrollable timeline within 10 seconds
- AC-2479bfe0 (US-013): Given the replay is loaded, when the engineer scrubs the timeline to any timestamp, then all parameter values at that timestamp are shown in a data panel alongside the stage name and the VIN's position in the prep tunnel at that moment
- AC-27f43d8d (US-013): Given a prep stage has missing parameter data (OQ-6 partial traceability scenario), when the replay loads, then the missing stage is shown as a grey gap with a 'Data Unavailable' label and the completeness percentage for the VIN is displayed at the top of the replay view
- AC-7e3c424d (US-016): Given a vehicle completes its final gate station and all 5 gate results are received via OpCenter, when the validator opens Gate Command for that VIN, then the unified health score is displayed within 10 seconds showing pass/conditional/fail per gate with the raw measurement values
- AC-f201c288 (US-016): Given any gate result shows a conditional pass (measurement outside tolerance but within rework threshold), when the validator views the health score, then the anomalous measurement is highlighted with the tolerance band, the actual value, and the historical distribution for that measurement at that gate
- AC-3f0328c7 (US-016): Given the validator clicks 'Certify', when the certification is confirmed, then the Certificate Studio generates the Digital Birth Certificate and the event is logged with the validator's user ID, VIN, gate IDs, health score snapshot, and timestamp—all within 5 minutes of the vehicle completing the final gate station
- AC-61ec7342 (US-017): Given a vehicle is in Gate Command and one or more gate results have not yet been received from OpCenter, when the validator views the health score, then the missing gate results are shown as 'Pending' with a spinner and the 'Certify' button is disabled with a tooltip explaining which results are outstanding
- AC-653c90e8 (US-017): Given all 5 gate results are received, when the completeness indicator updates to 100%, then the 'Certify' button becomes enabled and the validator receives a visual confirmation that the health score is complete
- AC-202159f2 (US-017): Given a gate station times out and never sends a result (e.g., NVH station network failure), when the timeout threshold (configurable, default 10 minutes) is exceeded, then the validator receives an alert identifying the failed station and a 'Manual Override' option that requires a typed justification and supervisor approval before certification proceeds
- AC-4167263e (US-019): Given any planner performs a session action (login, optimizer run, override, publish), when the action completes, then an event is emitted to the instrumentation layer within 1 second containing the user ID, action type, session ID, and timestamp
- AC-8d26ecac (US-019): Given the Gate 1 measurement window (Nov 1–28, 2025), when the Product Ops Analyst queries the instrumentation layer, then WAU, sequence lock %, and override rate are available as pre-built queries with no manual data extraction required
- AC-7f3009e7 (US-019): Given a planner session lasts less than 10 minutes without a meaningful action (optimizer run, override, or publish), when the WAU calculation runs, then that session is excluded from the active user count per the Gate 1 measurement definition

## Non-functional requirements

- availability: ≥ 99.95% monthly availability (≤ 21.9 minutes downtime per month)
- latency: < 1,000 ms p99 edge-to-Kafka-consumer latency
- scalability: Onboarding a new plant namespace to the UNS requires ≤ 5 configuration files and ≤ 2 weeks of integration work (no core platform code changes); validated by architecture review at M3
- performance: < 1,000 ms p99 edge-to-Kafka-consumer latency
- availability: ≥ 99.95% monthly availability (≤ 21.9 minutes downtime per month)
- availability: ≥ 99.9% monthly availability per console (≤ 43.8 minutes downtime per month)
- performance: < 2.0 s p75 cold-start LCP for all three consoles
- performance: < 100 ms p99 inference latency per model invocation (KServe metrics)
- security: Zero critical findings in IEC 62443 SL2 external audit (T6); zero critical/high CVEs in container images at Gate 1 snapshot
- reliability: AR Workbench must serve cached instructions for the next 10 VINs during a network outage; torque logs must be queued locally and published within 60 seconds of reconnection; zero data loss for cached events
- data_management: ≥ 95% data quality score across 500 critical tags, measured by automated profiler (completeness + timeliness + schema_validity)
- compliance: 100% of data storage and processing on EU-located infrastructure (Azure EU Central or on-prem Munich); zero cross-border personal data transfers
- reliability: RTO ≤ 4 hours; RPO ≤ 15 minutes; both validated by a documented DR drill at M9 (Sep 30, 2025)
- observability: 100% of UNS, console, and AI model components instrumented with Prometheus metrics and OpenTelemetry traces; SLO dashboards for T1–T5 available in Grafana with ≤ 60-second data lag
- scalability: Onboarding a new plant namespace to the UNS requires ≤ 5 configuration files and ≤ 2 weeks of integration work (no core platform code changes); validated by architecture review at M3
- usability: VIN-specific AR overlay loads within 5 seconds of RFID read at Marriage and Final stations, measured at the 95th percentile
- maintainability: All APIs versioned per semantic versioning; breaking changes require new major version; previous major version supported for minimum 6 months after deprecation notice
- accessibility: WCAG 2.1 Level AA compliance for all console web interfaces, verified by automated axe-core scan with zero critical violations at release
- integration: OpCenter OEE and Andon events available to Line Command consumers within 5 seconds of the event occurring in OpCenter, measured at p95
- privacy: Zero operator-identifiable data in predictive alert analytics exports or AR session aggregate reports; all analytics aggregated at station or shift level minimum
- observability: All 7 adoption metrics (A1–A7) queryable from the instrumentation layer as pre-built queries with ≤ 1-hour data lag; data retained for minimum 13 months

## Settled decisions

- **Should the OpCenter event subscription use GraphQL subscription (push) or REST polling?** → GraphQL subscription is the primary mechanism. A 10-second REST polling fallback is pre-built as RISK-02 mitigation and activates automatically when the subscription drops. (`graphql-subscription-with-rest-polling-fallback`)
- **How is the 'planning-cockpit is sole sequence source' constraint enforced technically?** → All sequence publish requests are signed with the Planning Cockpit API key fetched from HashiCorp Vault. OpCenter is configured (by Integration Lead) to reject any upload not bearing this signature. The source field is hardcoded to 'planning-cockpit' and validated server-side. (`opcenter-api-key-signing-planning-cockpit-only`)
- **How are duplicate sequence publish requests handled?** → Callers supply a UUID idempotencyKey. The sync_job table has a unique index on idempotency_key. Duplicate submissions return the cached response (HTTP 200) without re-submitting to OpCenter. (`sequence-publish-idempotency-key`)
- **What retry interval is used when OpCenter is unavailable?** → 30-second fixed interval with exponential backoff capped at 5 minutes after 10 consecutive failures. The sync_job.next_retry_at column drives the retry scheduler. (`30-second-retry-interval-queued-publish`)
- **Should Andon alerts include operator IDs?** → No. andon_alert.station_id is station-level only. No operator_id is stored in this table. Operator-linked data exists only in the immutable-audit-log (restricted access). (`station-level-anonymisation-andon-alerts`)
- **How are duplicate gate result events from OpCenter handled?** → A unique index on gate_result.opcenter_event_id (where not null) prevents duplicate ingestion. The service checks for existing opcenter_event_id before insert and skips duplicates. (`gate-result-dedup-on-opcenter-event-id`)
- **What Kafka topic naming convention is used?** → Topics follow the pattern: bmw.{plant}.opcenter.{entity}.{event_type} (e.g., bmw.munich.opcenter.oee.updated). Plant namespace is a prefix to support multi-plant expansion with ≤5 config files. (`kafka-topic-naming-convention`)
- **How is the OT-to-IT boundary secured for the OpCenter connection?** → The opcenter-sync-service uses SPIFFE/SPIRE-issued X.509 SVIDs for mTLS on all connections to OpCenter. SVIDs are auto-rotated by SPIRE. No static TLS certificates at zone boundaries. (`spiffe-svid-mtls-opcenter-connection`)
- **How is the OpCenter API key managed and rotated?** → The API key is stored in HashiCorp Vault and injected via Vault agent injector. The opcenter_api_key_rotation table tracks rotation schedule. Maximum 90-day rotation enforced per EA-SEC-002. Health endpoint exposes rotation due date. (`opcenter-api-key-90-day-rotation-vault`)
- **How long are OEE snapshots retained in the opcenter-sync-service database?** → 90 days hot in oee_snapshot table, then purged. Raw time-series data is retained long-term in TimescaleDB (owned by timescaledb component). (`oee-snapshot-retention-90-days`)
- **How long are gate results retained?** → 7 years in the gate_result table, consistent with automotive quality regulatory requirements. AES-256 at rest. (`gate-result-7-year-retention`)
- **What pagination style is used on list endpoints?** → Cursor-based pagination using opaque cursor tokens (base64-encoded composite of created_at + UUID). Default limit 50, maximum 200. (`cursor-pagination-on-all-list-endpoints`)
- **What Rust async runtime and HTTP client are used?** → Tokio async runtime with Axum for the inbound HTTP API, reqwest for outbound OpCenter REST calls, and graphql-ws for the OpCenter GraphQL subscription. rdkafka (librdkafka bindings) for Kafka producer. (`rust-async-tokio-runtime`)
- **How is the 5s p95 OpCenter-to-Kafka-consumer delivery target enforced?** → The service measures and emits a Prometheus histogram metric opcenter_sync_event_delivery_seconds (labels: event_type, line_id). Grafana SLO alert fires if p95 exceeds 5s. The GraphQL subscription path is the primary mechanism; REST polling fallback at 10s is the degraded path. (`5s-p95-delivery-target-kafka`)
- **Does opcenter-sync-service handle torque tightening events (US-011)?** → No. US-011 torque tightening flows from Beckhoff PLC → edge-gateways → UNS backbone directly. opcenter-sync-service is not in that data path. US-011 is listed in story_refs because the component is referenced in the solution, but no endpoints or entities are owned here for that story. (`us011-out-of-scope-for-opcenter-sync`)
- **Where is opcenter-sync-service data stored?** → All tables reside in PostgreSQL on Azure Germany West Central (primary) with active-passive DR replica in Azure Germany North. No data leaves EU infrastructure. (`eu-data-residency-azure-germany-west-central`)

## Contract

The endpoints, schemas, tables and events above are defined in `contracts/` in this repository. Implement them as written; a change to the contract is a change to the design.
