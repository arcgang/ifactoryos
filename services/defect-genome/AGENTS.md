# defect-genome

LLD component `cmp:defect-genome` · KServe, Python, scikit-learn, DBSCAN, XGBoost, MLflow, watsonx.governance · seeded from the Python template

Custom-trained paint defect clustering model — DBSCAN + XGBoost, real-time cluster detection, root-cause probability distribution, Rule-Based Defect Advisor v1 fallback

## Stories

- `US-014`
- `US-015`

## Endpoints

- `POST /defect/cluster-query`
- `GET /defect/clusters`
- `GET /defect/clusters/{clusterId}`
- `POST /defect/clusters/{clusterId}/corrective-action`
- `GET /alerts`
- `GET /alerts/{alertId}`
- `POST /alerts/{alertId}/acknowledge`
- `GET /models/confidence-threshold`
- `PUT /models/confidence-threshold`
- `POST /models/feedback`

## Entities

- `ent:advisor-v1-rule`
- `ent:cluster-alert`
- `ent:cluster-query-log`
- `ent:cluster-vin-member`
- `ent:confidence-threshold-config`
- `ent:defect-cluster`
- `ent:model-feedback`
- `ent:root-cause-factor`

## Events

- `evt:bmw.munich.paint.defect.events.v1`
- `evt:bmw.munich.defectgenome.cluster.alerts.v1`
- `evt:bmw.munich.defectgenome.query.results.v1`
- `evt:bmw.munich.defectgenome.corrective.actions.v1`

## Acceptance criteria

- AC-4265eaed (US-014): Given the engineer selects a defect class (dust, orange peel, run, crater, or pinhole) and clicks 'Run DefectGenome', when the model returns results, then the root-cause probability distribution is displayed as a ranked list showing the top 5 contributing factors with their probability scores and the supporting VIN count for each factor
- AC-2fd1c105 (US-014): Given the DefectGenome model confidence for a root cause is below a configurable threshold (default: 60%), when the result is displayed, then a 'Low Confidence' badge is shown and the rule-based Defect Advisor v1 result is displayed alongside as a fallback
- AC-dbcad11c (US-014): Given the engineer assigns a root cause from the DefectGenome output, when they click 'Create Corrective Action', then a Jira ticket is pre-populated with the cluster ID, root-cause probability, affected VIN list, and recommended prep stage adjustment, requiring only a single confirm click to submit
- AC-9c199988 (US-015): Given DefectGenome detects 3 or more VINs with the same root-cause signature above 80% confidence within any rolling 4-hour window, when the cluster threshold is crossed, then an in-app alert is pushed to the engineer's Quality Studio session within 5 minutes of the third VIN being classified
- AC-df44b79e (US-015): Given the cluster alert fires, when the engineer opens the alert, then the alert shows the cluster ID, the number of affected VINs, the root-cause signature, the confidence score, and a direct link to the Process Replay for the most recent affected VIN
- AC-f44bc9b9 (US-015): Given the engineer acknowledges the cluster alert, when they assign a corrective action, then the alert status updates to 'In Progress' and the cluster is flagged in the Defect Dashboard with the corrective action Jira ticket ID

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

- **Should DefectGenome run in batch or streaming mode for cluster detection?** → Streaming mode: DefectGenome consumes the UNS Kafka topic bmw.munich.paint.defect.events.v1 in real-time and maintains a rolling in-memory cluster state per defect class. Cluster queries via the HTTP API trigger a synchronous KServe inference call against the current cluster state. (`defect-genome-inference-mode`)
- **How is the Rule-Based Defect Advisor v1 fallback architecturally separated from the ML model?** → Advisor v1 is a separate Python module with deterministic decision trees loaded from the advisor_v1_rule table. It is invoked synchronously within the defect-genome service when model_confidence < low_confidence_threshold. It is NOT a separate microservice. Rules are stored in PostgreSQL and hot-reloadable without redeployment. (`defect-genome-fallback-architecture`)
- **How is the low-confidence threshold (default 60%) made configurable without redeployment?** → The confidence_threshold_config table holds a single-row configuration enforced by a UNIQUE constraint on singleton_guard. The PUT /models/confidence-threshold endpoint updates this row. The service reads the threshold on each inference call (cached with a 30-second TTL in memory). (`defect-genome-confidence-threshold-configurable`)
- **What are the exact thresholds for triggering a cluster alert?** → Alert fires when: (1) 3 or more VINs share the same root-cause signature, (2) confidence_score >= 0.80, (3) all VINs classified within a rolling 4-hour window. All three thresholds are stored in confidence_threshold_config and configurable via the admin API. (`defect-genome-cluster-alert-threshold`)
- **How are duplicate cluster query submissions handled?** → The POST /defect/cluster-query request accepts an optional idempotency_key (UUID). If the same key is submitted within 5 minutes, the cached ClusterQueryResult is returned without re-invoking KServe. The idempotency_key is stored with a UNIQUE index in cluster_query_log. (`defect-genome-idempotency-cluster-query`)
- **How is duplicate corrective action creation prevented?** → POST /defect/clusters/{clusterId}/corrective-action returns HTTP 409 if corrective_action_jira_id is already set on the cluster. The endpoint also accepts an idempotency_key. The Jira integration is called only once per cluster. (`defect-genome-corrective-action-idempotency`)
- **What pagination style is used for list endpoints?** → Cursor-based pagination using opaque base64-encoded cursors encoding (created_at, id) tuples. All list endpoints accept ?cursor=&limit= and return a CursorPage object per conventions. (`defect-genome-pagination-style`)
- **How does defect-genome authenticate service-to-service calls (e.g. from quality-studio)?** → OAuth 2.0 Client Credentials flow for service-to-service calls without user context. For user-context calls from quality-studio via the api-gateway, the OBO (On-Behalf-Of) token flow is used to propagate the engineer's identity for audit trail. (`defect-genome-auth-service-to-service`)
- **How is the <100ms p99 KServe inference latency NFR met?** → DefectGenome is deployed as a KServe InferenceService on ARO. The DBSCAN clustering runs on pre-indexed cluster state (not raw VIN data) maintained in memory by the streaming consumer. XGBoost scoring is a single model call on the cluster feature vector. Model warm-up is enforced via KServe minReplicas=2. (`defect-genome-kserve-inference-latency`)
- **How are VINs handled in analytics exports to comply with GDPR station-level anonymisation?** → VINs are stored in cluster_vin_member and cluster_alert tables for operational use. Analytics exports and Kafka event payloads anonymise to station_id level. The affected_vins array in ClusterQueryResult is only returned in the HTTP API response (not in Kafka events) and is scoped to the engineer's authorised plant. (`defect-genome-gdpr-vin-anonymisation`)
- **How is model governance tracked for DefectGenome?** → Every model version is registered in MLflow Model Registry (experiment tracking, artifact storage) and mirrored to watsonx.governance model inventory as required by security control IBM-AI-001. The model_version field is stored on every cluster_query_log and defect_cluster record for full lineage. (`defect-genome-mlflow-watsonx-governance`)
- **How is the 5-minute alert delivery SLA (US-015) enforced?** → The streaming Kafka consumer publishes the DefectClusterAlert event immediately when the threshold is crossed. Quality Studio subscribes to the Kafka topic and pushes the alert via WebSocket to the engineer's active session. The 5-minute SLA is measured from the third VIN classification event timestamp to the WebSocket push timestamp, tracked in the observability-stack. (`defect-genome-alert-delivery-sla`)
- **How is the Process Replay deep link for the most recent affected VIN generated?** → The process_replay_url is constructed by defect-genome as a deep link into Quality Studio using the pattern: https://quality-studio.internal.bmw-factory-os.eu/process-replay/{vin}. It is stored on both defect_cluster and cluster_alert records and included in the DefectClusterAlert Kafka event payload. (`defect-genome-process-replay-url`)
- **How is EU data residency enforced for all defect-genome data?** → All PostgreSQL tables include a data_region column defaulting to 'eu-de-west-central'. The ARO cluster is deployed in Azure Germany West Central. No data is replicated outside EU. The DR secondary is in Azure Germany North (also EU). Kafka topics are on-premises Munich. (`defect-genome-eu-data-residency`)

## Contract

The endpoints, schemas, tables and events above are defined in `contracts/` in this repository. Implement them as written; a change to the contract is a change to the design.
