# hyperledger-fabric-api

LLD component `cmp:hyperledger-fabric-api` · Python · seeded from Python

API in front of hyperledger-fabric — serves POST /api/v1/certificate/issue

## Stories

- `US-018`
- `US-021`

## Endpoints

- `GET /certificates`
- `POST /certificate/issue`
- `GET /certificate/{certificateId}`
- `GET /certificate/{certificateId}/verify`
- `POST /api/v1/certificate/issue`

## Entities

- `ent:fabric-anchor-attempt`
- `ent:fabric-certificate`

## Events

- `evt:bmw.ifos.munich.fabric.certificate.issued.v1`
- `evt:bmw.ifos.munich.fabric.certificate.verify-failed.v1`

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

## Settled decisions

- **How should the API handle duplicate certificate issue requests (e.g., quality-studio retries after a network timeout)?** → The caller (quality-studio) generates a stable UUID certificateId before submission. The API treats certificateId as an idempotency key: if a record with the same certificateId already exists and the certificateHash matches, return 200 with the existing Fabric transaction hash. If certificateId exists but certificateHash differs, return 409 Conflict. (`certificate-id-idempotency-key`)
- **Should the certificate hash be computed by hyperledger-fabric-api or by the calling service (quality-studio)?** → The SHA-256 certificateHash is computed by quality-studio over the canonical certificate payload before submission and included in the POST body. hyperledger-fabric-api stores and anchors this hash without recomputing it. (`certificate-hash-computed-by-caller`)
- **What timeout should be applied to the Hyperledger Fabric transaction commit before returning 504?** → 55 seconds. The API returns 504 if the Fabric commit has not confirmed within 55 seconds, leaving 5 seconds of buffer within the 60-second end-to-end SLA (AC-0ed51f1f). (`fabric-timeout-threshold-55s`)
- **Should certificate metadata be stored only on the Fabric ledger or also mirrored in a local PostgreSQL table?** → Mirrored in the local fabric_certificate table (postgresql-citus). The Fabric ledger is the tamper-evident anchor; the local DB is the queryable operational store for list, filter, and shift export operations. (`local-db-mirror-of-fabric-ledger`)
- **How does the observability-stack distinguish certificates issued via quality-studio from legacy certificates for the A7 metric (≥90% certs via Quality Studio)?** → Every certificate issue request includes a sourceSystem field ('quality-studio' | 'legacy'). This is stored in fabric_certificate and included in the CertificateIssuedEvent payload. The observability-stack computes A7 = count(sourceSystem='quality-studio') / count(*) over the measurement window. (`source-system-field-for-a7-metric`)
- **What pagination style should the /certificates list endpoint use?** → Cursor-based pagination using an opaque cursor (base64-encoded issued_at + certificate_id tuple) with a default limit of 50 and maximum of 200, per the platform conventions. (`cursor-pagination-on-list-endpoint`)
- **How does quality-studio authenticate to hyperledger-fabric-api given the mTLS + SPIFFE/SPIRE requirement?** → The API gateway enforces JWT Bearer token validation (BMW Entra ID OIDC, OBO flow for user-context calls). At the network layer, the ARO service mesh enforces mTLS with SPIFFE/SPIRE-issued X.509 SVIDs for all pod-to-pod communication. The API itself validates the Bearer JWT for RBAC (CERTIFICATE_ISSUER / CERTIFICATE_READER roles). (`mtls-spiffe-service-to-service-auth`)
- **When must the CertificateIssuedEvent be published to Kafka relative to the Fabric commit?** → The event must be published within 1 second of the Fabric commit confirmation, satisfying AC-ffcb028e (event emitted within 1 second of action completion). The Kafka publish is performed synchronously in the same request handler after the local DB insert, before returning the HTTP response. (`kafka-event-within-1s-of-anchor`)
- **How are Fabric submission failures and retries tracked for SLO monitoring and audit?** → Every Fabric submission attempt is recorded in fabric_anchor_attempt with status (pending/committed/failed/timeout), latency_ms, and error details. This table is the source of truth for Fabric SLO dashboards and supports the immutable audit log requirement. (`fabric-anchor-attempt-audit-table`)
- **How long should certificate records be retained in the local PostgreSQL store?** → 7 years, matching the immutable-audit-log retention policy and the EU Battery Passport compliance pathway referenced in US-018. Records are never deleted; a data_region column confirms EU residency for GDPR Article 30 compliance. (`certificate-retention-7-years`)
- **Should hash verification against the Fabric ledger be performed continuously in the background or on-demand via the verify endpoint?** → On-demand via GET /certificate/{certificateId}/verify. Background continuous verification is deferred to Horizon 2. The verify endpoint is called by quality-studio when a validator opens Certificate Studio to inspect a specific certificate. (`verify-endpoint-on-demand-not-background`)
- **How does the Python service communicate with the Hyperledger Fabric peer nodes?** → Using the official Hyperledger Fabric Python SDK (fabric-sdk-py) over gRPC/TLS with mTLS. The Fabric peer TLS certificates and the service's client certificate are injected by the HashiCorp Vault agent injector at pod startup. No static credentials are stored in the container image. (`python-fabric-sdk-grpc-mtls`)
- **Should shiftId be required on the certificate issue request?** → shiftId is optional (nullable) on the issue request. If omitted, the certificate is still anchored but cannot be included in shift export queries. quality-studio is expected to always supply shiftId in normal operation. (`shift-id-optional-not-required`)

## Contract

The endpoints, schemas, tables and events above are defined in `contracts/` in this repository. Implement them as written; a change to the contract is a change to the design.
