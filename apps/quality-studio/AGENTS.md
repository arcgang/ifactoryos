# quality-studio

LLD component `cmp:quality-studio` · React 18, TypeScript, OpenTelemetry SDK, BMW Entra ID OIDC · seeded from React

Role-based console for Paint Process Engineers and Quality Gate Validators — defect analysis, process replay, gate certification, digital birth certificate

## Stories

- `US-013`
- `US-014`
- `US-015`
- `US-016`
- `US-017`
- `US-018`
- `US-021`
- `US-022`
- `US-023`
- `US-024`

## Endpoints

- `POST /rest/api/3/issue`

## Acceptance criteria

- AC-aa9ca19f (US-013): Given the engineer selects a defect VIN and clicks 'Launch Process Replay', when the replay loads, then all 14 prep stage parameters, booth parameters, and climate data are displayed as aligned time-series on a single scrollable timeline within 10 seconds
- AC-2479bfe0 (US-013): Given the replay is loaded, when the engineer scrubs the timeline to any timestamp, then all parameter values at that timestamp are shown in a data panel alongside the stage name and the VIN's position in the prep tunnel at that moment
- AC-27f43d8d (US-013): Given a prep stage has missing parameter data (OQ-6 partial traceability scenario), when the replay loads, then the missing stage is shown as a grey gap with a 'Data Unavailable' label and the completeness percentage for the VIN is displayed at the top of the replay view
- AC-4265eaed (US-014): Given the engineer selects a defect class (dust, orange peel, run, crater, or pinhole) and clicks 'Run DefectGenome', when the model returns results, then the root-cause probability distribution is displayed as a ranked list showing the top 5 contributing factors with their probability scores and the supporting VIN count for each factor
- AC-2fd1c105 (US-014): Given the DefectGenome model confidence for a root cause is below a configurable threshold (default: 60%), when the result is displayed, then a 'Low Confidence' badge is shown and the rule-based Defect Advisor v1 result is displayed alongside as a fallback
- AC-dbcad11c (US-014): Given the engineer assigns a root cause from the DefectGenome output, when they click 'Create Corrective Action', then a Jira ticket is pre-populated with the cluster ID, root-cause probability, affected VIN list, and recommended prep stage adjustment, requiring only a single confirm click to submit
- AC-9c199988 (US-015): Given DefectGenome detects 3 or more VINs with the same root-cause signature above 80% confidence within any rolling 4-hour window, when the cluster threshold is crossed, then an in-app alert is pushed to the engineer's Quality Studio session within 5 minutes of the third VIN being classified
- AC-df44b79e (US-015): Given the cluster alert fires, when the engineer opens the alert, then the alert shows the cluster ID, the number of affected VINs, the root-cause signature, the confidence score, and a direct link to the Process Replay for the most recent affected VIN
- AC-f44bc9b9 (US-015): Given the engineer acknowledges the cluster alert, when they assign a corrective action, then the alert status updates to 'In Progress' and the cluster is flagged in the Defect Dashboard with the corrective action Jira ticket ID
- AC-7e3c424d (US-016): Given a vehicle completes its final gate station and all 5 gate results are received via OpCenter, when the validator opens Gate Command for that VIN, then the unified health score is displayed within 10 seconds showing pass/conditional/fail per gate with the raw measurement values
- AC-f201c288 (US-016): Given any gate result shows a conditional pass (measurement outside tolerance but within rework threshold), when the validator views the health score, then the anomalous measurement is highlighted with the tolerance band, the actual value, and the historical distribution for that measurement at that gate
- AC-3f0328c7 (US-016): Given the validator clicks 'Certify', when the certification is confirmed, then the Certificate Studio generates the Digital Birth Certificate and the event is logged with the validator's user ID, VIN, gate IDs, health score snapshot, and timestamp—all within 5 minutes of the vehicle completing the final gate station
- AC-61ec7342 (US-017): Given a vehicle is in Gate Command and one or more gate results have not yet been received from OpCenter, when the validator views the health score, then the missing gate results are shown as 'Pending' with a spinner and the 'Certify' button is disabled with a tooltip explaining which results are outstanding
- AC-653c90e8 (US-017): Given all 5 gate results are received, when the completeness indicator updates to 100%, then the 'Certify' button becomes enabled and the validator receives a visual confirmation that the health score is complete
- AC-202159f2 (US-017): Given a gate station times out and never sends a result (e.g., NVH station network failure), when the timeout threshold (configurable, default 10 minutes) is exceeded, then the validator receives an alert identifying the failed station and a 'Manual Override' option that requires a typed justification and supervisor approval before certification proceeds
- AC-0ed51f1f (US-018): Given the validator clicks 'Certify' in Gate Command, when Certificate Studio generates the Digital Birth Certificate, then the certificate contains the VIN, gate results snapshot, health score, certifying validator ID, timestamp, and a Hyperledger Fabric transaction hash within 60 seconds of the certify action
- AC-c040ff38 (US-018): Given a Digital Birth Certificate is issued, when the Fabric anchor is queried, then the certificate hash is retrievable and matches the certificate content, confirming tamper-evidence
- AC-fb3fae1c (US-018): Given the validator requests a shift certificate export, when they click 'Export Shift Summary', then a PDF is generated listing all certificates issued in the shift with VINs, timestamps, and Fabric anchor hashes, downloadable within 30 seconds
- AC-ffcb028e (US-021): Given a Paint Engineer or Quality Validator performs any tracked meaningful action in Quality Studio (Process Replay launch, DefectGenome query, root cause assignment, certify action, or Certificate Studio open), when the action completes, then an event is emitted to the instrumentation layer within 1 second containing the user ID, role, action type, VIN (where applicable), and timestamp
- AC-9da98f63 (US-021): Given a user logs into Quality Studio but performs no meaningful action within the session, when the WAU calculation runs, then that session is excluded from the active user count—a login-only session does not count as an active day for any persona
- AC-77d7d8c2 (US-021): Given a certificate is issued via Quality Studio, when the event is emitted, then the event includes the Hyperledger Fabric transaction hash to enable A7 (≥90% certs via Quality Studio) measurement by cross-referencing with the Fabric anchor count
- AC-5abf4071 (US-022): Given a user with the 'Production Planner' role attempts to access Line Command or Quality Studio, when the access request is made, then the system returns a 403 Forbidden response and logs the unauthorised access attempt in the audit trail
- AC-639e34c8 (US-022): Given a user with the 'Assembly Associate' role logs into Line Command, when they access the AR Workbench, then they can view and interact with AR work instructions and torque logging but cannot access Command Center OEE data or Handover Builder
- AC-9303b18b (US-022): Given a new user is provisioned, when their role is assigned in the identity provider, then their console access is enforced within 5 minutes of role assignment without requiring a platform restart
- AC-04d1d046 (US-023): Given any user action that mutates a production sequence, quality certificate, or VIN digital record, when the action is committed, then an audit log entry is written within 1 second containing the user ID, action type, affected entity ID, before/after state hash, and UTC timestamp
- AC-094f88b4 (US-023): Given an audit log entry is written, when the log is queried at any future time, then the entry cannot be modified or deleted—any query returning a modified entry is treated as a system integrity failure
- AC-a3d97564 (US-023): Given the audit log retention policy, when log entries are older than the configured retention period (minimum 7 years for regulatory compliance), then entries are archived to cold storage rather than deleted, and remain queryable within 24 hours of an archive retrieval request
- AC-edd6ce81 (US-024): Given a user's browser or device language is set to German (de-DE), when they open any console, then all UI labels, alert messages, error messages, and work instruction text are displayed in German
- AC-e41c448a (US-024): Given a user switches their language preference from German to English within the console settings, when the preference is saved, then all UI content updates to English within 2 seconds without requiring a page reload
- AC-1a65ae08 (US-024): Given a new alert or work instruction is added to the system, when it is published, then both German and English translations must be present before the content is visible to end users—content with only one language translation is blocked from publication

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

- **data: kafka schema registry data quality gate** → Confluent Schema Registry (Avro/Protobuf) as the primary data quality gate at UNS ingest — unregistered schemas rejected, DLQ with alerting for poison messages (`kafka-schema-registry-data-quality-gate`)
- **Where is RBAC enforced for quality-studio access?** → Console-level RBAC is enforced at the api-gateway using JWT role claims from BMW Entra ID. The SPA reads role claims from the decoded JWT to conditionally render UI elements. Field-level security is deferred to Horizon 2. (`console-rbac-enforced-at-gateway`)
- **Where does quality-studio fetch Process Replay data from?** → quality-studio calls GET /api/v1/vin/{vin}/thread via the api-gateway, which routes to vin-digital-thread (Neo4j). The SPA renders the returned time-series data as an aligned scrollable timeline. Missing stages are rendered as grey gaps with 'Data Unavailable' labels and a completeness percentage. (`process-replay-data-source`)
- **How does quality-studio handle DefectGenome low-confidence results?** → The SPA reads the confidence score from the cluster query response. If below the configurable threshold (default 60%, fetched from GET /models/confidence-threshold), it renders a 'Low Confidence' badge and displays the Rule-Based Defect Advisor v1 fallback result alongside the ML result. (`defect-genome-confidence-threshold-ui`)
- **How are real-time defect cluster alerts delivered to the quality-studio session?** → quality-studio establishes a Server-Sent Events (SSE) or WebSocket connection through the api-gateway to receive push notifications from the observability-stack/defect-genome event stream. The alert must arrive within 5 minutes of the 3rd VIN being classified (US-015 AC). (`cluster-alert-delivery-mechanism`)
- **How does quality-studio detect when all 5 gate results are available?** → The SPA polls GET /gate-results?vin={vin} at a configurable interval (default 5s) until all 5 gate types (dim, leak, elec, nvh, adas) are present or the timeout threshold (default 10 minutes, configurable) is exceeded. The Certify button is disabled until completeness = 100%. (`gate-result-completeness-polling`)
- **How is the shift certificate PDF export generated?** → The SPA fetches the list of certified VINs for the shift from GET /gate-results?shiftId={shiftId}&certified=true, then generates the PDF client-side using a React-to-PDF library (within the React 18 + TypeScript stack). The PDF includes VINs, timestamps, and Fabric anchor hashes. Target: downloadable within 30 seconds. (`certificate-studio-pdf-export`)
- **How does quality-studio emit adoption events to the observability-stack?** → The SPA emits adoption events by calling POST to the observability-stack's adoptionevent endpoint via the api-gateway within 1 second of each meaningful action (Process Replay launch, DefectGenome query, root cause assigned, certify, Certificate Studio open). Login-only sessions are excluded from WAU by the observability-stack's session filter. (`adoption-event-emission-client-side`)
- **How is German/English localisation implemented in quality-studio?** → React i18next is used for internationalisation. Language bundles for de-DE and en-GB are bundled with the SPA. The user's browser language is detected on load; manual preference is stored in localStorage and applied without page reload (within 2 seconds). Content with only one language translation is blocked from publication at the content management layer. (`i18n-react-i18next`)
- **How is GDPR/Works Council PII anonymisation enforced in adoption events emitted by quality-studio?** → Adoption events include userId (pseudonymised employee ID, not name), role, action type, VIN (where applicable), and timestamp. No biometric, gaze, or operator-name data is included in any emitted event. Analytics aggregation to station/shift level is enforced by the observability-stack, not the SPA. (`pii-anonymisation-in-adoption-events`)
- **How is supervisor approval for Manual Override implemented in the SPA?** → When a gate station timeout is detected, the SPA shows a 'Manual Override' modal requiring a typed justification. The SPA sends an approval request notification (via the api-gateway to a notification service or in-app to a supervisor session). The Certify button remains disabled until supervisor approval is received. The override justification and supervisor ID are included in the certificate issue request and audit log entry. (`manual-override-supervisor-approval-flow`)
- **Does quality-studio support offline operation in MVP?** → No. Offline-first PWA capability is explicitly deferred to Horizon 2 per the journey pain points. The SPA requires network connectivity to the api-gateway for all operations. No service worker or local cache for quality-studio data is implemented in MVP. (`no-offline-capability-mvp`)
- **What is the scope of the Hyperledger Fabric integration in MVP?** → The Fabric anchor is an internal demo in MVP. The Digital Birth Certificate is anchored to the BMW-operated permissioned Fabric ledger and the fabricTxHash is included in the certificate and adoption events. W3C Verifiable Credential external presentation is Horizon 2 scope — no stubs are built. (`hyperledger-fabric-internal-demo-only`)

## Contract

The endpoints, schemas, tables and events above are defined in `contracts/` in this repository. Implement them as written; a change to the contract is a change to the design.
