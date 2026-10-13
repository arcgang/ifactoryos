# Security Review Gate — MQTT Sparkplug B Publisher (US-011)

## Purpose

This document is a **merge gate**. A security engineer must review and sign off
on the three items below **before** this pull request is merged. Sign-off is
recorded by adding a `Reviewed-by:` entry in this file and approving the PR.

The review covers the new SPIRE workload registration entry and Kafka ACLs
added in `platform/edge-gateways/main.tf` for the MQTT Sparkplug B publisher
workload (`workload: mqtt-sparkplug-publisher`).

---

## Items requiring sign-off

### (a) Trust domain is not expanded

The new `ClusterSPIFFEID` resource `mqtt-publisher-{node_id}` uses:

```
spiffeIDTemplate: "spiffe://munich.bmw/edge-gateways/{shop}/{node_index}"
```

This is **identical** to the existing gateway SVID template in:
- `platform/spiffe-spire/main.tf` — `spire_entry_edge_gateway` resource
- `platform/edge-gateways/main.tf` — `spire_registration_entry` resource

No new trust domain, no new root CA, no federation is introduced.
`federatesWith: []` and `admin: false` are explicit.

**Reviewer must confirm:** The `spiffeIDTemplate` stays within the single
approved trust domain `munich.bmw` and does not introduce a second root.

### (b) SVID rotation schedule is unaffected

The new entry sets `ttl: 1h`, matching the existing workload pattern:

| File | Resource | TTL |
|---|---|---|
| `platform/spiffe-spire/main.tf` | `spire_entry_edge_gateway` | `1h` (via `local.svid_ttl`) |
| `platform/spiffe-spire/main.tf` | `spire_entry_aro_workload` | `1h` (via `local.svid_ttl`) |
| `platform/edge-gateways/main.tf` | `spire_registration_entry` | `1h` |
| `platform/edge-gateways/main.tf` | `spire_entry_mqtt_publisher` | `1h` ← **this PR** |

The SPIRE server's `defaultX509SvidTTL: ${local.svid_ttl}` and the
`caTTL: 24h` in `platform/spiffe-spire/main.tf` are **not modified** by
this PR. The rotation interval (half-TTL = 1800 s) configured in
`svid-watcher.service` is unchanged.

**Reviewer must confirm:** SVID TTL is `1h`, consistent with all existing
entries. The SPIRE server global configuration in `platform/spiffe-spire/main.tf`
is not touched by this PR.

### (c) No new network ingress into the SL2 zone

The MQTT Sparkplug B publisher makes **outbound** connections only:

| Direction | Protocol | Port | Destination | Firewall rule |
|---|---|---|---|---|
| OT → IT DMZ | MQTT/TLS | 8883 | `uns-hivemq.hivemq.svc.cluster.local` | CM-013 (existing) |
| OT → IT DMZ | gRPC | 8081 | `spire-workload-api.spire.svc.cluster.local` | FW-IN-005 (existing) |

Both conduits are already permitted by the existing `ot_vlan_network_policy`
NetworkPolicy resources in `platform/edge-gateways/main.tf`. No new firewall
rules, no new ingress ports, no new services are exposed inside the OT VLAN.

The SVID is obtained by the `svid-watcher.service` sidecar via the SPIRE
Workload API gRPC stream (FW-IN-005). The publisher reads SVID bytes from
files on disk — it does not open a gRPC connection itself.

**Reviewer must confirm:** No new inbound ports are opened in the OT zone
(IEC 62443 SL2 boundary). The publisher is a pure OT-to-IT egress workload.

---

## Credential / secret hygiene

The publisher module (`platform/edge-gateways/mqtt_publisher/sparkplug_publisher.py`)
obtains its mTLS identity exclusively from SPIRE-managed files written by
`svid-watcher.service`:

- `SVID_CERT_PATH` (default `/run/spire/svid/svid.pem`)
- `SVID_KEY_PATH` (default `/run/spire/svid/svid_key.pem`)
- `SPIRE_BUNDLE_PATH` (default `/run/spire/bundle/bundle.pem`)

These paths are documented in `.env.example`. No static certificate,
private key, or connection string is embedded in source code or configuration.

---

## Sign-off

| Item | Status | Reviewer | Date |
|---|---|---|---|
| (a) Trust domain not expanded | PENDING | | |
| (b) SVID rotation schedule unaffected | PENDING | | |
| (c) No new network ingress | PENDING | | |

Replace `PENDING` with `APPROVED` and add your name and date once each item
is confirmed. This file must show all three `APPROVED` before the PR merges.
