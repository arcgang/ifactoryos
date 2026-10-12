# line-command-api

LLD component `cmp:line-command-api` · Python · seeded from Python

Backend for line-command — serves GET /api/v1/ar/instructions/{vin}

## Stories

- `US-007`
- `US-008`
- `US-009`
- `US-010`
- `US-011`
- `US-012`
- `US-020`

## Endpoints

- `POST /handover`
- `GET /handovers`
- `POST /torque/log`
- `POST /ar/sessions`
- `GET /torque/logs`
- `GET /line-command/andon/alerts`
- `POST /ar/expert-calls`
- `GET /ar/instructions/{vin}`
- `GET /handovers/{handoverId}`
- `POST /ar/expert-calls/{callId}/end`
- `POST /andon/alerts/{alertId}/acknowledge`
- `GET /api/v1/ar/instructions/{vin}`
- `POST /api/v1/torque/log`
- `POST /api/v1/handover`

## Entities

- `ent:andon-alert`
- `ent:ar-instruction-cache`
- `ent:ar-instruction-failure-log`
- `ent:ar-session`
- `ent:expert-call`
- `ent:shift-handover`
- `ent:torque-log`
- `ent:works-council-config`

## Events

- `evt:bmw.ifos.munich.uns.andon.alerts.v1`
- `evt:bmw.ifos.munich.uns.torque.plc-trace.v1`
- `evt:bmw.ifos.munich.line-command.adoption.v1`
- `evt:bmw.ifos.munich.line-command.expert-call.ended.v1`
- `evt:bmw.ifos.munich.line-command.torque.submitted.v1`
- `evt:bmw.ifos.munich.line-command.ar-session.started.v1`
- `evt:bmw.ifos.munich.line-command.handover.submitted.v1`
- `evt:bmw.ifos.munich.line-command.torque.out-of-tolerance.v1`

## Acceptance criteria

- AC-5be353b7 (US-007): Given the Shift Lead logs into Line Command at shift start, when the Command Center loads, then OEE values for all 4 weld lines are displayed with data no older than 30 seconds, alongside all open predictive Andon alerts sorted by time-to-predicted-failure ascending
- AC-183ec0b0 (US-007): Given a predictive Andon alert is active, when the Shift Lead views the Command Center, then each alert shows the affected station, the predicted failure type, the minutes remaining to predicted failure (countdown updating every 60 seconds), and the recommended action
- AC-6a6f12c4 (US-007): Given the outgoing Shift Lead has completed a Handover Builder summary, when the incoming Shift Lead opens Command Center, then the handover notes are displayed as the first item in the alert queue with a 'New Handover' badge until acknowledged
- AC-7cf0baef (US-008): Given the Shift Lead selects a weld cell from the Command Center, when Station Detail opens, then the live weld current and voltage time-series traces from the Beckhoff PLC are displayed for the last 60 minutes with the predictive alert threshold overlaid as a reference line
- AC-20f08ac3 (US-008): Given a predictive alert is active for the selected station, when the Shift Lead views Station Detail, then the alert threshold crossing point is highlighted on the trace with the timestamp and the predicted time to failure
- AC-5a1d1b22 (US-008): Given the Shift Lead clicks 'Dispatch Maintenance' from Station Detail, when the action is confirmed, then the dispatch event is logged in the audit trail with the Shift Lead's user ID, station ID, alert ID, and timestamp
- AC-e37bcf34 (US-009): Given the Shift Lead opens Handover Builder at shift end, when they complete the handover form, then all open predictive Andon alerts from the current shift are pre-populated as line items requiring a disposition note (resolved / escalated / monitoring)
- AC-5f4b73cb (US-009): Given the Shift Lead submits the handover, when the incoming Shift Lead opens Command Center, then the handover summary is displayed with OEE delta (current shift vs. 30-day average per weld line), open alert dispositions, and any VINs flagged for quality review
- AC-04cd3711 (US-009): Given a handover is submitted, when the Plant Manager views the daily production review, then the handover record is available as a structured log entry with submitting Shift Lead ID, shift start/end timestamps, and all linked alert IDs
- AC-00bc6b07 (US-010): Given a vehicle arrives at the Marriage or Final station, when the overhead conveyor RFID is read by the AR Workbench, then the VIN-specific holographic overlay loads within 5 seconds showing the correct option codes, torque specifications, and step sequence for that vehicle's exact configuration
- AC-1b1b1284 (US-010): Given the vehicle is a rare variant (e.g., M Performance package), when the overlay loads, then the variant-specific steps are highlighted with a visual delta indicator showing what differs from the standard i4 configuration
- AC-775df01c (US-010): Given the AR Workbench fails to load the VIN-specific overlay within 10 seconds, when the associate is waiting, then a fallback screen displays the VIN and a QR code linking to the current digital instruction PDF, and the failure is logged in the audit trail
- AC-13685a24 (US-011): Given the associate completes a torque tightening at a Marriage or Final station, when the Beckhoff PLC records the torque/angle trace, then the value is published to the UNS and linked to the VIN digital thread within 2 seconds, and the AR overlay step indicator turns green
- AC-69c77dfd (US-011): Given a torque value is outside the specified tolerance for the VIN's option code, when the PLC records the out-of-tolerance value, then the AR overlay step indicator turns red and an alert is raised in Line Command for the Shift Lead within 30 seconds
- AC-b734032a (US-011): Given the UNS is temporarily unavailable, when a torque tightening occurs, then the value is cached locally on the edge gateway and published to the UNS within 60 seconds of connectivity restoration, with the associate's AR overlay showing a 'Pending Sync' indicator
- AC-7293efd6 (US-012): Given the associate taps the 'Call Expert' button in the AR Workbench and video streaming is Works Council-approved, when the call connects, then the remote expert can see the associate's field of view within 10 seconds and can place holographic annotations visible to the associate in their AR overlay
- AC-48bbc2a6 (US-012): Given the associate taps the 'Call Expert' button and only the voice-only fallback is active (Works Council has not approved video streaming), when the call connects, then an audio channel is established within 10 seconds and the associate can describe the issue verbally; no video or gaze data is transmitted
- AC-e30375d9 (US-012): Given a remote expert call ends (video or voice-only), when the session closes, then the call duration, VIN, station ID, call mode (video/voice), and expert user ID are logged in the VIN digital thread audit trail with no biometric or gaze data recorded
- AC-fd1f00c6 (US-020): Given a Shift Lead or Assembly Associate performs any tracked meaningful action in Line Command (alert acknowledgment, handover submission, AR session start, or torque log submission), when the action completes, then an event is emitted to the instrumentation layer within 1 second containing the user ID, role, action type, station ID, and timestamp
- AC-19b649cd (US-020): Given a user logs into Line Command but performs no meaningful action (no alert acknowledgment, no handover submission, no AR session start, no torque log submission) within the session, when the WAU calculation runs, then that session is excluded from the active user count—a login-only session does not count as an active day
- AC-9c6cd996 (US-020): Given an AR Workbench session starts, when the session event is emitted, then the event includes the VIN, device type (HoloLens 2 or iPad), and station ID to enable device adoption analysis; no biometric or gaze data is included in any emitted event

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

- **Should GET /ar/instructions/{vin} return a 200 with fallback data or a 202 when the overlay cannot be fully resolved within the SLA?** → Return 200 with the full overlay when resolved within 5 s p95. Return 202 with an ArInstructionFallback payload (VIN + PDF QR code) when the overlay cannot be resolved. The AR Workbench client must also call POST /ar/sessions/{sessionId}/overlay-failure to log the failure to the audit trail. (`ar-overlay-response-strategy`)
- **How is idempotency enforced for POST /torque/log given that edge gateways may replay cached records after UNS reconnection?** → The plcEventId field (a unique PLC-generated event identifier) is used as the idempotency key. A UNIQUE index on torque_log.plc_event_id ensures duplicate submissions return 200 with the existing record rather than creating a duplicate. (`torque-log-idempotency-key`)
- **How is idempotency enforced for POST /handover?** → The client generates a UUID handoverId before submission. The server uses this as the PRIMARY KEY. A duplicate POST with the same handoverId returns 200 with the existing record. (`handover-idempotency-key`)
- **How does the API determine whether to return VIDEO or VOICE_ONLY call mode for expert calls?** → The works_council_config table holds a single-row configuration record with video_streaming_approved boolean. The API reads this at call initiation time. The config is updated only via GitOps (ArgoCD) to ensure auditability. (`works-council-call-mode-determination`)
- **How is the §87 BetrVG / Works Council requirement for no biometric or gaze data enforced at the API layer?** → All request schemas for AR sessions and expert calls explicitly exclude gaze, eye-tracking, and biometric fields. The ar_session and expert_call_session tables have no columns for such data. The adoption event schema enumerates only permitted fields (VIN, deviceType, stationId, actionType, role, stationId, timestamp). API validation rejects any unexpected fields via strict schema validation. (`no-biometric-data-in-any-record`)
- **Should the adoption event contain the full user ID or an anonymised station-level reference?** → The adoption event contains the full userId (from the OBO JWT) for WAU counting purposes. The observability-stack is responsible for anonymising to station/shift level in analytics exports. The line-command-api does not anonymise the userId in the event payload because WAU requires per-user distinct counting. (`adoption-event-user-id-anonymisation`)
- **How are cached AR instruction overlays invalidated when instruction versions change?** → The ar_instruction_cache table stores instruction_version and valid_until. On GET /ar/instructions/{vin}, the API checks the cache: if the cached version matches the current version from the VIN digital thread and valid_until is in the future, the cache is served. Otherwise the overlay is re-fetched and the cache is updated. valid_until is set to 4 hours by default (one shift). (`ar-instruction-cache-invalidation`)
- **What token type is expected in the BearerAuth header?** → OAuth 2.0 OBO (On-Behalf-Of) JWT issued by BMW Entra ID, validated at the api-gateway before reaching line-command-api. The JWT contains the user's role claims (SHIFT_LEAD, ASSEMBLY_ASSOCIATE, PLANT_MANAGER, EDGE_GATEWAY). The api-gateway enforces console-level RBAC; line-command-api enforces operation-level role checks. (`bearer-auth-obo-token`)
- **Should torque logs arrive via the REST POST /torque/log endpoint or via Kafka consumption from the UNS?** → Both paths are supported. The primary path is Kafka consumption from uns.torque.log (edge gateway → UNS → line-command-api consumer). The REST POST /torque/log endpoint is provided as a secondary path for direct submission (e.g. from the AR Workbench client when the Kafka path is unavailable or for manual correction). Both paths share the same idempotency logic on plcEventId. (`torque-log-kafka-vs-rest`)
- **How is EU data residency enforced for line-command-api data stores?** → All PostgreSQL tables are deployed on the postgresql-citus cluster in Azure Germany West Central. Table comments include data_residency=EU. No cross-region replication to non-EU regions is configured. DR secondary is Azure Germany North (also EU). Kafka topics on the UNS backbone are on-premises Munich. (`eu-data-residency-enforcement`)
- **Where is the AR overlay failure audit record written — to the local ar_overlay_failure table, the immutable audit log, or both?** → Both. The ar_overlay_failure table provides fast queryable access for operational monitoring. The immutable-audit-log receives a POST /api/v1/audit/event for the 7-year WORM retention required by regulatory records. The ar_overlay_failure table is the operational copy; the immutable audit log is the compliance copy. (`overlay-failure-audit-trail`)
- **Should AR instruction payloads be cached in the API layer, and if so for how long?** → Yes. Cache resolved VIN instruction payloads in ar_instruction_cache with a 5-minute TTL. Background job purges expired rows. Cache miss triggers a call to vin-digital-thread-api. (`ar-instruction-cache-ttl`)
- **Where is torque tolerance validation performed — in the API or delegated to vin-digital-thread-api?** → Tolerance validation is performed in line-command-api. The AR instruction payload (fetched from cache or vin-digital-thread-api) includes specNm and toleranceNm per fastener point. The API compares the submitted torqueNm against these values and emits TorqueOutOfToleranceEvent if exceeded. (`torque-tolerance-validation-location`)
- **How is the Works Council video streaming approval enforced — client-side or server-side?** → Server-side only. The works_council_config table stores the video_streaming_approved flag per plant. The API reads this flag when creating an expert_call and sets call_mode accordingly. The client cannot override this. (`works-council-video-flag-server-side`)
- **What data is explicitly excluded from expert call audit log entries?** → Biometric data and gaze tracking data are explicitly excluded from all expert_call records, ExpertCallEndedEvent payloads, and VIN digital thread audit entries. The schema includes a biometricDataIncluded: false constant field as a machine-readable compliance assertion. (`expert-call-no-biometric-data`)
- **How is the 1-second adoption event emission SLO (AC-fd1f00c6) achieved?** → Adoption events are published to Kafka synchronously within the API request handler before the HTTP response is returned. The Kafka producer uses acks=1 (leader acknowledgment) to balance durability and latency. The observability-stack consumer has ≤1-hour data lag SLO for WAU queries. (`adoption-event-within-1s`)
- **What data is excluded from adoption events to satisfy Works Council and GDPR requirements?** → No biometric data, gaze tracking data, or operator-identifiable analytics data is included in any AdoptionEvent or ArSessionStartedEvent. Events include userId (BMW Entra ID subject) for WAU calculation but the observability-stack anonymises to station/shift level in all analytics exports. (`adoption-event-no-biometric-data`)
- **How is duplicate handover submission prevented (e.g. double-click or network retry)?** → The client generates a UUID idempotency_key per handover submission. The API enforces UNIQUE (idempotency_key) and UNIQUE (shift_id, line_id) on shift_handover. A duplicate returns 409. (`handover-idempotency`)
- **What HTTP status code and response body is returned when the AR overlay cannot be loaded within the timeout?** → HTTP 504 with ArInstructionFallbackResponse body containing the VIN, fallbackPdfQrUrl, failureLoggedAt, and correlationId. The failure is also written to ar_instruction_failure_log and the immutable audit log. (`ar-overlay-504-fallback-response`)
- **Is UNS publish for torque logs synchronous or asynchronous within the API request?** → Asynchronous. The API writes to torque_log with uns_publish_status='queued', returns 201/202 to the caller, then publishes to Kafka. A background worker updates uns_publish_status to 'published' on Kafka ack. The 2-second UNS linkage SLO (AC-13685a24) is met by the Kafka consumer latency, not the HTTP response. (`torque-log-uns-publish-async`)
- **Does line-command-api receive torque data via HTTP POST from the edge gateway or by consuming from Kafka?** → Both paths are supported. The primary path is Kafka consumption from uns.torque.plc-trace.v1 (published by edge-gateways via UNS). The POST /api/v1/torque/log endpoint is the secondary path for direct submission (e.g. from the AR Workbench client or edge gateway HTTP fallback). Both paths share the same idempotency_key deduplication. (`plc-trace-kafka-consumption`)
- **Does line-command-api own the Andon alert source data or consume it from another component?** → line-command-api consumes AndonAlertEvents from the UNS Kafka topic (published by opcenter-sync-service) and materialises them into the local andon_alert table. The API owns the acknowledgement/disposition state. The source alert data originates in opcenter-sync-service. (`andon-alert-consumed-not-owned`)

## Contract

The endpoints, schemas, tables and events above are defined in `contracts/` in this repository. Implement them as written; a change to the contract is a change to the design.
