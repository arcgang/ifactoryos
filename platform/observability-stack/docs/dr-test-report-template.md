---
# DR Test Report — machine-readable YAML front-matter
# CI validates completeness by checking that all required fields below are non-empty
# before accepting this file as a PR artifact.
#
# Required fields (CI will reject the report if any are left as null or ""):
report_meta:
  drill_id: ""                        # e.g. 2026-10-12-001 — must match DR_DRILL_ID used in dr-annotate.sh
  report_version: "1.0"
  report_status: ""                   # draft | final

timing:
  failure_declaration_utc: ""         # ISO 8601, e.g. 2026-10-12T08:00:00Z
  service_confirmation_utc: ""        # ISO 8601, e.g. 2026-10-12T11:45:00Z
  actual_rto_minutes: null            # numeric, e.g. 225 — must be ≤ 240 for RTO met
  last_uns_message_before_failure_utc: ""   # ISO 8601 — from Grafana stat panel "Last UNS Message Timestamp Before Failure"
  first_uns_message_after_recovery_utc: ""  # ISO 8601 — from Grafana stat panel "First UNS Message Timestamp After Recovery"
  actual_rpo_minutes: null            # numeric, e.g. 12 — must be ≤ 15 for RPO met

slo_assessment:
  rto_met: null                       # true | false
  rpo_met: null                       # true | false
  grafana_dashboard_uid: "dr-t10-slo-001"
  grafana_snapshot_url: ""            # URL to exported dashboard snapshot retained for 13 months (NFR-18)

sign_off:
  platform_lead_name: ""              # Full name of Platform Lead
  platform_lead_sign_off_date: ""     # ISO 8601 date, e.g. 2026-10-13
---

# DR Test Report

**Drill ID:** <!-- drill_id -->  
**Date of drill:** <!-- failure_declaration_utc date part -->  
**Report status:** <!-- draft | final -->

---

## 1. Executive Summary

<!-- One paragraph summarising the outcome: was RTO met? was RPO met? any blockers? -->

---

## 2. Timing Summary

| Measurement | Planned | Actual |
|---|---|---|
| Failure declaration (UTC) | — | <!-- failure_declaration_utc --> |
| Service confirmation (UTC) | — | <!-- service_confirmation_utc --> |
| **Actual RTO** | ≤ 240 min (4 h) | <!-- actual_rto_minutes --> min |
| Last UNS message before failure (UTC) | — | <!-- last_uns_message_before_failure_utc --> |
| First UNS message after recovery (UTC) | — | <!-- first_uns_message_after_recovery_utc --> |
| **Actual RPO** | ≤ 15 min | <!-- actual_rpo_minutes --> min |

> **RTO met:** <!-- rto_met -->  
> **RPO met:** <!-- rpo_met -->

---

## 3. Steps Executed

Fill in each row. Add rows if additional steps were performed.

| # | Step description | Planned duration | Actual start (UTC) | Actual end (UTC) | Actual duration | Outcome |
|---|---|---|---|---|---|---|
| 1 | Trigger simulated failure (ArgoCD app-of-apps sync disabled; Kafka MirrorMaker 2 paused) | 5 min | | | | |
| 2 | Confirm failure declared via smoke test and dr-annotate.sh failure_declared | 2 min | | | | |
| 3 | Initiate Velero restore of TimescaleDB and PostgreSQL/Citus from latest hourly snapshot | 30 min | | | | |
| 4 | Initiate ArgoCD sync to DR cluster (Germany North) | 20 min | | | | |
| 5 | Validate SPIRE trust bundle propagation to DR workloads | 10 min | | | | |
| 6 | Validate Kafka MirrorMaker 2 replication resume on DR cluster | 15 min | | | | |
| 7 | Validate HiveMQ MQTT broker accepting connections from edge gateways on DR cluster | 10 min | | | | |
| 8 | Run full DR smoke test suite (platform/uns-backbone/tests/test_dr_smoke.py) | 15 min | | | | |
| 9 | Confirm first UNS DDATA message received on DR Kafka topic | 5 min | | | | |
| 10 | Record service_confirmed via dr-annotate.sh service_confirmed | 2 min | | | | |
| 11 | Validate RTO and RPO panels on Grafana DR T10 SLO dashboard | 5 min | | | | |
| 12 | Export Grafana snapshot and attach URL to this report | 5 min | | | | |

---

## 4. Deviations from Runbook

For each deviation, record:
- **Step affected** — the step number from Section 3
- **Expected behaviour** — what the runbook prescribed
- **Actual behaviour** — what happened instead
- **Root-cause note** — brief diagnosis or hypothesis
- **Remediation** — corrective action taken or filed as follow-up ticket

| # | Step | Expected | Actual | Root cause | Remediation |
|---|---|---|---|---|---|
| 1 | | | | | |

_If no deviations: enter "None"._

---

## 5. Observations and Follow-up Actions

<!-- Bullet list of observations that do not constitute deviations but warrant attention -->

---

## 6. Grafana Evidence

- **Dashboard UID:** `dr-t10-slo-001`
- **Snapshot URL:** <!-- grafana_snapshot_url -->
- **Snapshot retention:** 13 months (NFR-18)

Screenshot or link to the RTO and RPO panels showing actual values against thresholds.

---

## 7. Sign-off

This report documents the DR drill identified above. All required fields have been
populated and the results reviewed.

| Role | Name | Date |
|---|---|---|
| Platform Lead | <!-- platform_lead_name --> | <!-- platform_lead_sign_off_date --> |

_By completing the sign-off date, the Platform Lead confirms that the steps above were
executed, the actual RTO and RPO values are accurate, and any deviations have been
documented with a root-cause note._
