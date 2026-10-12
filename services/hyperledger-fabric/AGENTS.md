# hyperledger-fabric

LLD component `cmp:hyperledger-fabric` · Hyperledger Fabric 2.5, Go chaincode

Permissioned blockchain ledger anchoring Digital Birth Certificates — VIN, gate results, health score, validator ID, timestamp, Fabric transaction hash within 60s of certify

## Stories

- `US-018`
- `US-021`

## Acceptance criteria

- AC-0ed51f1f (US-018): Given the validator clicks 'Certify' in Gate Command, when Certificate Studio generates the Digital Birth Certificate, then the certificate contains the VIN, gate results snapshot, health score, certifying validator ID, timestamp, and a Hyperledger Fabric transaction hash within 60 seconds of the certify action
- AC-c040ff38 (US-018): Given a Digital Birth Certificate is issued, when the Fabric anchor is queried, then the certificate hash is retrievable and matches the certificate content, confirming tamper-evidence
- AC-fb3fae1c (US-018): Given the validator requests a shift certificate export, when they click 'Export Shift Summary', then a PDF is generated listing all certificates issued in the shift with VINs, timestamps, and Fabric anchor hashes, downloadable within 30 seconds
- AC-ffcb028e (US-021): Given a Paint Engineer or Quality Validator performs any tracked meaningful action in Quality Studio (Process Replay launch, DefectGenome query, root cause assignment, certify action, or Certificate Studio open), when the action completes, then an event is emitted to the instrumentation layer within 1 second containing the user ID, role, action type, VIN (where applicable), and timestamp
- AC-9da98f63 (US-021): Given a user logs into Quality Studio but performs no meaningful action within the session, when the WAU calculation runs, then that session is excluded from the active user count—a login-only session does not count as an active day for any persona
- AC-77d7d8c2 (US-021): Given a certificate is issued via Quality Studio, when the event is emitted, then the event includes the Hyperledger Fabric transaction hash to enable A7 (≥90% certs via Quality Studio) measurement by cross-referencing with the Fabric anchor count

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
