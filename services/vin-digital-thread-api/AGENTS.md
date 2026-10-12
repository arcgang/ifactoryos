# vin-digital-thread-api

LLD component `cmp:vin-digital-thread-api` · Python · seeded from Python

API in front of vin-digital-thread — serves GET /api/v1/vin/{vin}/thread

## Stories

- `US-013`
- `US-023`
- `US-028`
- `US-030`

## Endpoints

- `GET /vin/{vin}/thread`
- `POST /vin/{vin}/thread/events`
- `GET /vin/{vin}/thread/replay`
- `GET /vin/{vin}/thread/snapshot`
- `GET /vin/{vin}/thread/completeness`
- `GET /api/v1/vin/{vin}/thread`

## Entities

- `ent:thread-event-ingest-log`
- `ent:thread-query-audit-log`
- `ent:vin-thread-completeness-cache`

## Events

- `evt:factory.munich.uns.manufacturing.event.v1`
- `evt:factory.munich.vin.thread.event.linked.v1`
- `evt:factory.munich.vin.thread.completeness.alert.v1`
- `evt:factory.munich.vin.thread.final.gate.complete.v1`

## Acceptance criteria

- AC-aa9ca19f (US-013): Given the engineer selects a defect VIN and clicks 'Launch Process Replay', when the replay loads, then all 14 prep stage parameters, booth parameters, and climate data are displayed as aligned time-series on a single scrollable timeline within 10 seconds
- AC-2479bfe0 (US-013): Given the replay is loaded, when the engineer scrubs the timeline to any timestamp, then all parameter values at that timestamp are shown in a data panel alongside the stage name and the VIN's position in the prep tunnel at that moment
- AC-27f43d8d (US-013): Given a prep stage has missing parameter data (OQ-6 partial traceability scenario), when the replay loads, then the missing stage is shown as a grey gap with a 'Data Unavailable' label and the completeness percentage for the VIN is displayed at the top of the replay view
- AC-04d1d046 (US-023): Given any user action that mutates a production sequence, quality certificate, or VIN digital record, when the action is committed, then an audit log entry is written within 1 second containing the user ID, action type, affected entity ID, before/after state hash, and UTC timestamp
- AC-094f88b4 (US-023): Given an audit log entry is written, when the log is queried at any future time, then the entry cannot be modified or deleted—any query returning a modified entry is treated as a system integrity failure
- AC-a3d97564 (US-023): Given the audit log retention policy, when log entries are older than the configured retention period (minimum 7 years for regulatory compliance), then entries are archived to cold storage rather than deleted, and remain queryable within 24 hours of an archive retrieval request
- AC-4e7be641 (US-028): Given any data containing or derivable from worker identity (user IDs, session logs, torque logs linked to operator), when the data is stored or processed, then it resides exclusively on infrastructure located within the EU (Azure EU Central or on-prem Munich) and no copy is transmitted outside the EU
- AC-95ca5900 (US-028): Given the Works Council requests a data processing inventory, when the request is made, then a GDPR Article 30 record of processing activities is available within 5 business days listing all data categories, purposes, retention periods, and legal bases
- AC-32c77b54 (US-028): Given a worker requests deletion of their personal data under GDPR Article 17, when the request is processed, then all directly identifying data (name, employee ID) is pseudonymised within 30 days while operational records (VIN-linked torque logs) are retained for the regulatory retention period
- AC-2818cd8a (US-030): Given any manufacturing event (PLC trace, gate result, torque log, AR instruction execution) is published to the UNS, when the event is ingested, then it is linked to the VIN node in the Neo4j digital thread graph within 5 seconds with the shop, station, timestamp, and data source
- AC-32301269 (US-030): Given a VIN completes the final quality gate, when the digital thread graph is queried for that VIN, then a continuous chain of events from body-in-white entry through final gate certification is returned with no gaps in shop-boundary transitions
- AC-b3b68360 (US-030): Given a VIN's digital thread is queried for Process Replay, when the query executes, then all 14 paint prep stage parameters are returned as a time-ordered sequence linked to the VIN node, with completeness ≥95% for VINs processed after UNS go-live

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

- **How does vin-digital-thread-api connect to the Neo4j causal cluster for read queries?** → The API connects to the Neo4j causal cluster via Bolt/TLS using the neo4j+s:// scheme. Read queries (Process Replay, thread traversal, snapshot) are routed to read replicas using the bolt+routing protocol. Write operations (event ingestion) are routed to the cluster leader. Credentials are injected via HashiCorp Vault agent injector — no static credentials. (`neo4j-bolt-tls-read-path`)
- **How is cursor pagination implemented for Neo4j graph traversal results?** → The cursor is a base64-encoded opaque token containing the last event's (occurredAt, eventId) tuple. The Cypher query uses WHERE e.occurredAt > $cursorTs OR (e.occurredAt = $cursorTs AND e.eventId > $cursorId) ORDER BY e.occurredAt ASC, e.eventId ASC LIMIT $limit. This avoids SKIP-based pagination which degrades on large graphs. (`cursor-pagination-neo4j-skip-limit`)
- **How is idempotency enforced for the POST /vin/{vin}/thread/events endpoint and the UNS Kafka consumer?** → Every ingest request must carry an idempotencyKey (UUID). For the REST endpoint this is a required request body field. For the Kafka consumer the UNS messageId is used as the idempotency key. The key is stored in thread_event_ingest_log with a UNIQUE constraint. Duplicate submissions return 409 (REST) or are silently discarded (Kafka). The idempotency key is checked before any Neo4j write. (`idempotency-key-event-ingestion`)
- **Should the API write to PostgreSQL or Neo4j first when ingesting an event?** → Write to PostgreSQL (thread_event_ingest_log with status=PENDING) first, then write to Neo4j. On Neo4j success, update status to LINKED. A background reconciliation job retries PENDING/FAILED records older than 5 seconds up to 3 times before marking FAILED and emitting an alert. (`pg-write-ahead-log-before-neo4j`)
- **How is the vin_thread_completeness_cache kept fresh without re-querying Neo4j on every /completeness request?** → The cache is updated synchronously after every successful event ingestion (UPSERT on vin_thread_completeness_cache). The /completeness endpoint reads from the cache if updated_at is within 30 seconds; otherwise it re-queries Neo4j and refreshes the cache. Expected sample counts per stage are derived from a static configuration file (14 stages × defined parameter set) loaded at startup. (`completeness-cache-refresh-strategy`)
- **Does the API expose operator-level identity in thread events?** → No. The station field in all API responses and stored records contains only the station identifier (e.g. PAINT_PREP_STAGE_6), never an operator ID, badge number, or name. The caller_subject in thread_query_audit_log records the JWT sub of the querying user but this table is restricted to security/compliance roles and is not exposed via any API endpoint. (`station-level-anonymisation-in-api`)
- **Which OAuth 2.0 flow is used for Quality Studio and Quality Lead callers vs. internal service callers?** → Quality Studio and Quality Lead callers use OAuth 2.0 OBO (On-Behalf-Of) tokens issued by BMW Entra ID — the JWT carries the user's identity and role claims. Internal service callers (UNS consumer, line-command-api) use OAuth 2.0 Client Credentials tokens with a thread-ingestor role claim. Both flows use BearerAuth (JWT). The api-gateway validates the JWT and enforces role-based access before forwarding to this service. (`bearer-auth-obo-for-user-context`)
- **How does the API meet the 10-second Process Replay load time requirement (AC-aa9ca19f)?** → The /replay endpoint executes a single Cypher traversal on Neo4j read replicas scoped to PAINT_PREP_STAGE, BOOTH_PARAMETER, and CLIMATE_DATA event types for the VIN. The completeness cache is read from PostgreSQL (sub-millisecond). The response is paginated at 200 samples per page. The Neo4j causal cluster is sized for p99 < 8s query time on a full paint prep thread (empirically validated during load testing). The api-gateway timeout for this endpoint is set to 12 seconds. (`process-replay-10s-sla-design`)
- **Where is the canonical list of 14 paint prep stage parameters defined and how does the API know which parameters are expected?** → The 14 prep stage names, their expected parameter keys (e.g. ecoat_voltage_v, phosphate_concentration_g_l, humidity_pct, temperature_c), and expected sample frequencies are defined in a static YAML configuration file (paint_prep_stage_config.yaml) loaded at service startup. This configuration drives completeness calculation. Changes to the configuration require a service deployment. (`14-prep-stage-parameter-definition`)
- **How is the thread_query_audit_log table kept append-only?** → The thread_query_audit_log table has no updated_at column and no UPDATE or DELETE grants for the application service account. The service account has INSERT and SELECT only. A PostgreSQL row-level security policy (RLS) prevents UPDATE and DELETE even for the service account. Periodic exports to the immutable-audit-log (Azure Blob WORM) are performed by a nightly job for 7-year retention. (`audit-log-append-only-enforcement`)
- **What format is used for VIN path parameters and how is it validated?** → VINs are 17-character strings conforming to ISO 3779 (characters A-H, J-N, P-R, T-Z, 0-9 — no I, O, Q). Validated by regex ^[A-HJ-NPR-Z0-9]{17}$ at the API layer and enforced by a CHECK constraint in PostgreSQL. Invalid VINs return 400 with error code VIN_FORMAT_INVALID. (`vin-id-format`)
- **How is the UNS Kafka consumer configured to meet the 5-second event-to-graph SLA?** → The vin-digital-thread-api deploys a Kafka consumer group (group.id=vin-digital-thread-api-ingestor) with 6 partitions on the factory.munich.uns.manufacturing.event.v1 topic. Each consumer instance processes events concurrently using asyncio. The PostgreSQL write (idempotency check + PENDING insert) and Neo4j write are performed in parallel where possible. Consumer lag is monitored via Prometheus and alerts fire if lag exceeds 1000 messages. (`kafka-consumer-group-design`)

## Contract

The endpoints, schemas, tables and events above are defined in `contracts/` in this repository. Implement them as written; a change to the contract is a change to the design.
