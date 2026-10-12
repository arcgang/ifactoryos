# sequence-optimizer

LLD component `cmp:sequence-optimizer` · KServe, Python, scikit-optimize, MLflow, watsonx.governance · seeded from the Python template

Custom-trained Monte Carlo optimisation model — 50 runs against live constraints, ranked VIN sequence within 45s, rule-based heuristic fallback in shadow mode

## Stories

- `US-002`
- `US-003`
- `US-005`
- `US-006`

## Endpoints

- `POST /optimize`
- `GET /optimize/{runId}`
- `GET /optimize/{runId}/explain/{vin}`
- `POST /overrides`
- `GET /overrides`
- `GET /overrides/{overrideId}`
- `GET /overrides/export`
- `GET /alerts`
- `POST /alerts/{alertId}/acknowledge`
- `POST /resequence`
- `GET /constraints/thresholds`
- `PUT /constraints/thresholds`
- `GET /my26/config`
- `POST /my26/config/validate`
- `POST /my26/config/save`
- `GET /model/health`

## Entities

- `ent:constraint-alert`
- `ent:constraint-threshold`
- `ent:model-governance-entry`
- `ent:my26-config`
- `ent:optimizer-run`
- `ent:optimizer-run-vin`
- `ent:sequence-override`
- `ent:vin-constraint-explanation`

## Events

- `evt:bmw.munich.i4.sequence.optimized.v1`
- `evt:bmw.munich.i4.sequence.override.applied.v1`
- `evt:bmw.munich.i4.sequence.constraint.alert.v1`
- `evt:bmw.munich.i4.sequence.resequence.triggered.v1`
- `evt:bmw.munich.i4.sequence.my26.config.activated.v1`
- `evt:bmw.munich.i4.uns.constraints.live.v1`

## Acceptance criteria

- AC-1848ffb5 (US-002): Given the planner clicks 'Run Optimizer' in Sequence Builder, when the SequenceOptimizer completes 50 Monte Carlo runs, then the ranked VIN sequence is displayed within 45 seconds of the click (leaving 15 seconds for review before the 60-second total target)
- AC-c9b64630 (US-002): Given the optimizer run completes, when the planner views the ranked sequence, then each VIN in the top-ranked sequence shows its position, the constraint score driving its rank, and any mandatory override flags (VIP priority, regulatory hold)
- AC-40a058cb (US-002): Given the optimizer run fails or times out after 45 seconds, when the planner is waiting, then a fallback rule-based heuristic sequence is presented with a clear label indicating it is the heuristic result, not the Monte Carlo optimum
- AC-06b604aa (US-003): Given the optimizer returns a ranked sequence, when the planner selects any VIN in the sequence, then a constraint contribution panel shows the top 3 constraint factors driving that VIN's position with their weighted scores and the data source for each factor
- AC-f214c397 (US-003): Given the planner applies a manual override to move a VIN, when the override is saved, then the Override Log records the VIN ID, original position, new position, planner's typed justification (minimum 10 characters), and timestamp
- AC-970d2dfa (US-003): Given the planner has applied overrides, when they view the Override Log, then all overrides for the current week are listed and exportable as a PDF for Plant Director review
- AC-5e06b8e5 (US-005): Given a constraint value (e.g., kitting readiness for a specific VIN) drops below a configurable threshold after the Monday sequence lock, when the threshold is crossed, then the planner receives an in-app alert in Constraint Radar within 2 minutes of the threshold breach, identifying the affected VINs and the constraint causing the breach
- AC-f0232f64 (US-005): Given the planner receives a mid-week disruption alert, when they click 'Re-sequence Affected VINs', then Scenario Lab pre-loads only the affected VINs and their downstream dependencies for a targeted Monte Carlo run, not the full weekly sequence
- AC-de48ca50 (US-005): Given a re-sequence is published mid-week, when the OpCenter sequence version diff is queried, then the re-sequenced VINs are attributed to 'planning-cockpit' in the audit log
- AC-aa5496da (US-006): Given the planner opens the MY26 Configuration Wizard before Week 30, when they import new option codes and paint palette definitions, then the wizard validates each entry against the SequenceOptimizer constraint model and flags any conflicts or missing constraint mappings before saving
- AC-2d79a38d (US-006): Given the MY26 configuration is saved, when the planner runs the SequenceOptimizer with a MY26 VIN, then the optimizer applies the new option codes and paint codes without error and the sequence output reflects the updated constraints
- AC-5550bb81 (US-006): Given the MY26 configuration is active, when the planner publishes a sequence containing MY26 VINs to OpCenter, then OpCenter accepts the sequence and the audit log records the MY26 config version used

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

- **other: keda sequence optimizer scaling** → Use KEDA (Kafka consumer lag trigger) for SequenceOptimizer auto-scaling rather than CPU-based HPA (`keda-sequence-optimizer-scaling`)
- **How do we prevent duplicate optimisation runs if the planning-cockpit retries a POST /optimize request?** → The client supplies an optional idempotency_key (UUID) in the request body. The optimizer stores it in optimizer_run.idempotency_key (UNIQUE constraint). A duplicate key within 10 minutes returns the cached OptimizeResponse with HTTP 200 rather than launching a new run. (`optimizer-idempotency-key`)
- **When and how is the rule-based heuristic fallback activated?** → The heuristic is always deployed in shadow mode (model_governance_entry.is_shadow=true). It is promoted to primary response if: (a) the Monte Carlo KServe call times out after 45 s, (b) KServe returns a non-2xx error, or (c) confidence_score < 0.70 at M9 review. The response always carries resultType='heuristic_fallback' so the UI can label it clearly. (`heuristic-fallback-shadow-mode`)
- **Should the number of Monte Carlo runs be configurable per request or fixed at 50?** → The API accepts monteCarloRuns (1–50, default 50). The 45-second SLA is calibrated for 50 runs × <100 ms p99 per inference = <5 s inference budget. Callers may request fewer runs for targeted re-sequences but the default is always 50. (`monte-carlo-run-count-fixed-50`)
- **How does the sequence-optimizer deliver constraint breach alerts within 2 minutes of threshold crossing?** → The optimizer runs a Kafka Streams consumer on the uns.constraints.live topic. On each message it evaluates the value against constraint_threshold. If a breach is detected it writes to constraint_alert, publishes ConstraintAlertEvent to Kafka, and the planning-cockpit polls GET /alerts (or subscribes via SSE in the cockpit layer). The 2-minute SLA is met by the Kafka consumer processing latency (<1 s) plus cockpit polling interval (≤30 s). (`constraint-alert-delivery-mechanism`)
- **How does the targeted re-sequence avoid running a full weekly sequence?** → POST /resequence accepts an explicit affectedVins list (sourced from the alert's affected_vins) plus the alertId. The optimizer only loads those VINs and their downstream dependencies (determined by position adjacency in the current locked sequence) into the Monte Carlo run. The full weekly sequence is not re-evaluated. (`resequence-scope-affected-vins-only`)
- **How is a mid-week re-sequence attributed to 'planning-cockpit' in the OpCenter audit log?** → The ResequenceTriggeredEvent payload carries source='planning-cockpit'. The opcenter-sync-service reads this field and sets the sequence version source in OpCenter accordingly. The immutable-audit-log entry also records source=planning-cockpit. (`resequence-audit-attribution`)
- **Should MY26 config validation and save be a single endpoint or two separate calls?** → Two separate endpoints: POST /my26/config/validate (dry-run, no persistence) and POST /my26/config/save (persists only if validation passes internally; returns 409 if unresolved errors exist). The wizard UI calls validate first, shows issues, then calls save after the planner confirms. (`my26-validate-before-save-two-step`)
- **How are MY26 configurations versioned to support audit trail and OpCenter attribution?** → Each saved MY26 config receives a UUID (config_id) and a semver string (version). The optimizer_run table stores my26_config_id as a FK. The SequenceOptimizedEvent and ResequenceTriggeredEvent carry my26ConfigVersion. The opcenter-sync-service includes the config version in the OpCenter sequence publish payload. (`my26-config-versioning`)
- **What is the minimum justification length for a manual override?** → 10 characters, enforced at both the API layer (OpenAPI minLength: 10) and the database layer (CHECK constraint on sequence_override.justification). (`override-justification-minimum-length`)
- **Is the planner_id stored in sequence_override a GDPR PII field?** → planner_id stores the Entra ID subject claim (a UUID-format opaque identifier), not a name or email. It is classified as pseudonymous. The GDPR station-level anonymisation rule applies to analytics exports; the override log is a restricted-access operational record, not an analytics export. No name or email is stored. (`planner-id-pseudonymous-not-pii`)
- **What pagination style is used for list endpoints?** → Cursor-based pagination using opaque next_cursor (base64-encoded last row's created_at + id) and limit (1–200, default 50), returning the shared CursorPage schema. No offset pagination. (`cursor-pagination-style`)
- **How does sequence-optimizer authenticate to KServe and other internal services?** → OAuth 2.0 Client Credentials flow via BMW Entra ID for service-to-service calls (no user context). Secrets injected via HashiCorp Vault agent injector — no hardcoded credentials. mTLS with SPIFFE/SPIRE SVIDs for cross-zone calls to uns-backbone. (`service-to-service-auth-client-credentials`)
- **How does the sequence-optimizer scale under load?** → KEDA with a Kafka consumer lag trigger on the optimize request topic scales KServe inference pods. CPU-based HPA is not used. Minimum 1 replica; maximum scaled to handle concurrent Monday morning runs. (`keda-kafka-lag-scaling`)
- **How is the SequenceOptimizer model registered in watsonx.governance?** → Each deployed model version is recorded in model_governance_entry with mlflow_run_id and watsonx_gov_id. The GET /model/health endpoint exposes the active model version and shadow status. The watsonx.governance entry is created as part of the CI/CD deployment pipeline. (`watsonx-governance-model-inventory`)
- **How is EU data residency enforced and auditable at the table level?** → All owned tables include a data_region column defaulting to 'eu-de-west-central'. Table comments carry the data_residency annotation. No cross-border replication is configured. The active-passive DR secondary is in Azure Germany North (also EU). (`eu-data-residency-column`)
- **Should the constraint snapshot used for an optimisation run be stored?** → Yes. The full ConstraintSnapshot JSON is stored in optimizer_run.constraint_snapshot (JSONB). This enables post-hoc audit of exactly which constraint values drove the ranked sequence, supporting the planner's ability to defend the AI output to the Plant Manager. (`constraint-snapshot-in-run-record`)

## Contract

The endpoints, schemas, tables and events above are defined in `contracts/` in this repository. Implement them as written; a change to the contract is a change to the design.
