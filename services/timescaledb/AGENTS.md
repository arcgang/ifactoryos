# timescaledb

LLD component `cmp:timescaledb` · TimescaleDB 2.x, PostgreSQL 15

Raw OT telemetry time-series store — 500K+ UNS tags, hypertable partitioning by time, feeds Grafana SLO dashboards, AES-256 at rest, EU data residency

## Stories

- `US-026`
- `US-030`

## Entities

- `ent:ottelemetryevent`

## Acceptance criteria

- AC-c2f97ee1 (US-026): Given all 10 edge gateways are operational, when telemetry is published from any in-scope shop, then the message is encoded in MQTT Sparkplug B format, validated against the schema registry, and available to all UNS consumers within 1,000ms p99 of the originating sensor event
- AC-bb0c3cc5 (US-026): Given a new tag is published to the UNS without a registered schema, when the schema registry validates the message, then the message is rejected and a schema violation alert is raised to the Data Governance Lead within 60 seconds
- AC-968bac16 (US-026): Given the UNS availability is measured over any calendar month, when the measurement is taken, then availability is ≥99.95% as measured by the Prometheus SLO dashboard
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

## Contract

The endpoints, schemas, tables and events above are defined in `contracts/` in this repository. Implement them as written; a change to the contract is a change to the design.
