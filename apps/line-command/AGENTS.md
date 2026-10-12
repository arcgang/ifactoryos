# line-command

LLD component `cmp:line-command` · React 18, TypeScript, Microsoft HoloLens 2 (WebXR), iPad Pro (ARKit), OpenTelemetry SDK, BMW Entra ID OIDC · seeded from the React template

Role-based console for Shift Leads and Assembly Associates — OEE monitoring, predictive Andon, AR work instructions, shift handover

## Stories

- `US-007`
- `US-008`
- `US-009`
- `US-010`
- `US-011`
- `US-012`
- `US-020`
- `US-022`
- `US-023`
- `US-024`

## Endpoints

- `GET /api/v1/ar/instructions/{vin}`
- `POST /api/v1/torque/log`
- `POST /api/v1/handover`

## Events

- `evt:factory.munich.line-command.adoption.v1`
- `evt:factory.munich.line-command.ar-session.v1`
- `evt:factory.munich.line-command.torque-log.v1`
- `evt:factory.munich.line-command.handover.v1`
- `evt:factory.munich.line-command.maintenance-dispatch.v1`
- `evt:factory.munich.line-command.expert-call.v1`
- `evt:factory.munich.opcenter.oee.snapshot.v1`
- `evt:factory.munich.opcenter.andon.alert.v1`
- `evt:factory.munich.vin-thread.torque.feedback.v1`

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

- **Should line-command define its own inbound HTTP API endpoints?** → No. line-command is a client (kind=client). It calls available_endpoints exposed by backend services via api-gateway. The three interfaces listed in the component spec (GET /api/v1/ar/instructions/{vin}, POST /api/v1/torque/log, POST /api/v1/handover) are calls the client makes to backend services, not endpoints it hosts. openapi is therefore {}. (`client-owns-no-http-api`)
- **How does line-command communicate meaningful actions to the instrumentation and audit layers?** → line-command publishes structured events to UNS Kafka topics (via api-gateway OBO token flow for REST submissions and direct Kafka publish for adoption events). AsyncAPI 3.0.0 is the primary contract for all events published and consumed. (`asyncapi-as-primary-contract`)
- **How is a 'meaningful action' defined for WAU calculation in line-command?** → Meaningful actions are: alert_acknowledgment, handover_submission, ar_session_start, torque_log_submission, maintenance_dispatch. A kiosk_login-only session (no subsequent meaningful action) is excluded from WAU. The action_type enum in AdoptionEventPayload enforces this at the event schema level. (`adoption-event-meaningful-action-filter`)
- **What data is excluded from all emitted events to satisfy Works Council §87 BetrVG?** → No biometric data, gaze tracking data, or operator-identifiable performance metrics are included in any event payload. ArSessionEventPayload and ExpertCallEventPayload explicitly exclude these fields. Analytics exports are anonymised to station/shift level minimum. (`no-biometric-gaze-data-in-events`)
- **How does the AR Workbench determine whether to offer video or voice-only expert call?** → The GET /api/v1/ar/instructions/{vin} response (from vin-digital-thread) includes a video_approved boolean flag reflecting the current Works Council approval status. The client renders the appropriate call mode based on this flag. No video or gaze data is transmitted when video_approved=false. (`works-council-video-flag-in-ar-instructions`)
- **How does the AR Workbench serve instructions during network outage?** → At shift start, line-command pre-fetches and caches instructions for the next 10 VINs in the shift queue using the browser's Cache API / IndexedDB (React 18 PWA service worker). During outage, the cached overlay is served. Torque logs are queued in IndexedDB and published within 60 seconds of reconnection via the edge gateway. (`ar-overlay-offline-cache-strategy`)
- **What happens when the VIN-specific overlay fails to load within 10 seconds?** → If the overlay has not rendered within 10 seconds of RFID read, line-command displays a fallback screen showing the VIN and a QR code linking to the current digital instruction PDF. The failure is logged to immutable-audit-log via POST /api/v1/audit/event and an ArSessionEvent (overlay_load_timeout) is published. (`ar-overlay-load-timeout-fallback`)
- **How does the associate know if their torque log is successfully anchored in the digital thread?** → The TorqueFeedbackMessage consumed from the UNS backbone carries a sync_status field (synced | pending_sync | cached_local). line-command maps this to the AR overlay step indicator: green = synced, amber = pending_sync. The TorqueLogEventPayload also carries sync_status for the instrumentation record. (`torque-sync-status-indicator`)
- **How is user identity propagated in API calls from line-command to backend services?** → All API calls from line-command to backend services via api-gateway use OAuth 2.0 On-Behalf-Of (OBO) token flow. The user's JWT (obtained via OIDC Auth Code + PKCE from BMW Entra ID) is exchanged for a downstream OBO token that carries the user's identity and role claims for audit trail and RBAC enforcement. (`obo-token-flow-for-all-api-calls`)
- **Where is console-level RBAC enforced for line-command?** → Console-level RBAC is enforced at the api-gateway using the role claims in the JWT. Production Planners (role=production_planner) are denied access to line-command endpoints with 403 Forbidden. Assembly Associates (role=assembly_associate) are denied access to Command Center OEE and Handover Builder endpoints. Field-level security is deferred to Horizon 2. (`console-rbac-enforced-at-api-gateway`)
- **How is bilingual (de-DE / en-GB) support implemented without page reload?** → line-command uses react-i18next with lazy-loaded translation namespaces. Language preference is stored in localStorage and applied at React context level. Switching language triggers a context update that re-renders all components within 2 seconds without a page reload. New alerts and work instructions are blocked from publication by the backend if either de-DE or en-GB translation is absent. (`i18n-react-i18next-no-reload`)
- **How is the 'New Handover' badge displayed and cleared in the incoming Shift Lead's Command Center?** → When a HandoverSubmittedEvent is consumed, line-command stores the handover_id in local state (React context + localStorage for persistence across refresh). The badge is displayed until the incoming Shift Lead explicitly acknowledges the handover via POST /alerts/{alertId}/acknowledge (using the handover alert_id). Acknowledgment clears the badge and emits an AdoptionEvent (alert_acknowledgment). (`handover-new-badge-acknowledgment`)
- **How does line-command ensure OEE data displayed is no older than 30 seconds?** → line-command polls GET /oee/snapshots every 25 seconds (leaving 5 seconds for network latency). The OeeSnapshotPayload includes a captured_at timestamp; the client renders a staleness warning if captured_at is >30 seconds old. The 5-second p95 delivery SLO from opcenter-sync-service (NFR-16) ensures freshness under normal conditions. (`oee-data-freshness-30s-polling`)
- **How is the minutes-to-predicted-failure countdown updated every 60 seconds?** → The AndonAlertPayload includes predicted_failure_at (absolute UTC timestamp). line-command computes minutes_to_failure client-side as Math.ceil((predicted_failure_at - now) / 60000) and updates the display every 60 seconds via a React useInterval hook. The backend re-publishes updated AndonAlertMessages when the prediction changes; the client merges updates by alert_id. (`andon-countdown-60s-client-update`)
- **How is EU data residency enforced for events emitted by line-command?** → All Kafka topics are hosted on the on-premises UNS backbone at Munich plant or on ARO in Azure Germany West Central. The api-gateway and observability-stack are deployed in Azure Germany West Central. No event payload containing user_id or VIN is routed outside EU-hosted infrastructure. The client application itself is served from ARO in Azure Germany West Central. (`eu-data-residency-event-routing`)
- **How does line-command support both HoloLens 2 (WebXR) and iPad Pro (ARKit)?** → The AR Workbench is implemented as a React 18 + TypeScript PWA. On HoloLens 2 it uses the WebXR Device API for holographic overlay rendering. On iPad Pro it uses ARKit via a React Native WebView bridge or a dedicated ARKit-native module. The device_type field in ArSessionEventPayload captures which device was used for adoption analysis. The same VIN instruction payload is served to both devices. (`device-agnostic-webxr-arkit`)
- **How is line-command instrumented for observability?** → line-command uses the OpenTelemetry JavaScript SDK (browser distribution) to emit traces and custom metrics. All meaningful user actions emit an OTel span with user_id (hashed for privacy), action_type, station_id, and session_id attributes. Spans are exported to the observability-stack collector via OTLP/HTTPS. Web Vitals (LCP, FID, CLS) are captured for the <2.0s p75 cold-start LCP SLO. (`opentelemetry-sdk-instrumentation`)

## Contract

The endpoints, schemas, tables and events above are defined in `contracts/` in this repository. Implement them as written; a change to the contract is a change to the design.
