# Zone-and-Conduit Model — iFactoryOS OT/IT Network Segmentation

**Standard:** IEC 62443 Security Level 2 (SL2)
**Scope:** OT Zone (edge gateways / PLCs) → On-Prem IT Zone (UNS backbone) → Cloud IT Zone (ARO workloads)
**Audit reference:** US-027 · BR-10 · External IEC 62443 SL2 audit October 1, 2025

---

## Three-Zone Boundary Diagram

```mermaid
graph TB
    subgraph OT_Zone["OT Zone — IEC 62443 SL2\nVLAN 10.200.x.0/24 per shop"]
        direction TB
        PLC1["Beckhoff PLC\n(ADS/ADS-UDP :48898)"]
        PLC2["Beckhoff PLC\n(ADS/ADS-UDP :48898)"]
        GW1["Siemens SIMATIC Edge GW\nedge-{shop}-01 (primary)\nSPIFFE ID: spiffe://munich.bmw/edge-gateways/{shop}/01"]
        GW2["Siemens SIMATIC Edge GW\nedge-{shop}-02 (standby)\nSPIFFE ID: spiffe://munich.bmw/edge-gateways/{shop}/02"]
        SPIRE_AGENT["SPIRE Agent sidecar\n/run/spire/sockets/agent.sock\nTPM 2.0 node attestation"]

        PLC1 -->|"ADS-UDP :48898\nProcess data (CoV)"| GW1
        PLC2 -->|"ADS-UDP :48898\nProcess data (CoV)"| GW2
        GW1 -.-|"Unix socket\nSVID fetch + rotation"| SPIRE_AGENT
        GW2 -.-|"Unix socket\nSVID fetch + rotation"| SPIRE_AGENT
    end

    subgraph IT_DMZ["On-Prem IT Zone — Plant IT DMZ\nIEC 62443 SL2"]
        direction TB
        MQTT["HiveMQ MQTT Broker\nuns-hivemq.hivemq.svc\n:8883 (TLS 1.3 mTLS)"]
        BRIDGE["HiveMQ Kafka Extension\nSparkplug B → Kafka bridge"]
        SPIRE_SERVER["SPIRE Server\nspire-workload-api.spire.svc\n:8081 (gRPC)\nCA key in HashiCorp Vault"]
        SR["Confluent Schema Registry\n:8081 (HTTPS)"]

        MQTT --> BRIDGE
        BRIDGE --> KAFKA
        SPIRE_SERVER -.-|"Vault AppRole\nCA private key"| VAULT["HashiCorp Vault\nsecret/data/spire/ca-key"]
    end

    subgraph KAFKA_LAYER["On-Prem IT Zone — Kafka / UNS Backbone"]
        KAFKA["Confluent Kafka\nbmw.munich.uns.ddata.{shop}.v1\nSASL/SCRAM + TLS"]
    end

    subgraph Cloud_Zone["Cloud IT Zone — Azure EU Central\nARO OpenShift 4.16"]
        direction TB
        OPCENTER["opcenter-sync-service\nSPIFFE ID: spiffe://munich.bmw/opcenter-sync-service"]
        PLANNING["planning-cockpit-backend\nSPIFFE ID: spiffe://munich.bmw/planning-cockpit-backend"]
        QUALITY["quality-studio-backend\nSPIFFE ID: spiffe://munich.bmw/quality-studio-backend"]
    end

    %% Conduit CM-013 — primary OT→IT crossing
    GW1 -->|"CM-013\nTCP :8883 mTLS\nSPIFFE SVID client cert\nTLS 1.3 only\nSC-004 · SC-017"| MQTT
    GW2 -->|"CM-013\nTCP :8883 mTLS\nSPIFFE SVID client cert\nTLS 1.3 only\nSC-004 · SC-017"| MQTT

    %% Conduit FW-IN-005 — SVID issuance
    SPIRE_AGENT -->|"FW-IN-005\nTCP :8081 gRPC\nWorkload API (SVID fetch)\nSC-017"| SPIRE_SERVER

    %% IT DMZ → Cloud conduit
    KAFKA -->|"Conduit IT→Cloud\nKafka TLS :9092\nmTLS SPIFFE SVID\nSC-004 · SC-017"| OPCENTER
    KAFKA -->|"Conduit IT→Cloud\nKafka TLS :9092\nmTLS SPIFFE SVID"| PLANNING
    KAFKA -->|"Conduit IT→Cloud\nKafka TLS :9092\nmTLS SPIFFE SVID"| QUALITY

    style OT_Zone fill:#fff3e0,stroke:#e65100,color:#000
    style IT_DMZ fill:#e8f5e9,stroke:#2e7d32,color:#000
    style KAFKA_LAYER fill:#e8f5e9,stroke:#2e7d32,color:#000
    style Cloud_Zone fill:#e3f2fd,stroke:#1565c0,color:#000
```

---

## Conduit Register

| Conduit ID | From | To | Protocol | Port | Auth Method | IEC 62443 Control | Direction |
|---|---|---|---|---|---|---|---|
| **CM-013** | OT Zone VLAN (10.200.x.0/24) | Plant IT DMZ — MQTT Broker | MQTT over TLS 1.3 | TCP/8883 | mTLS — SPIFFE X.509 SVID | SC-004, SC-017 | Egress (OT→IT) |
| **FW-IN-005** | OT Zone VLAN (10.200.x.0/24) | Plant IT DMZ — SPIRE Workload API | gRPC/TLS | TCP/8081 | SPIFFE Workload API (Unix socket relay) | SC-017 | Egress (OT→IT) |
| **IT→Cloud-001** | Plant IT DMZ — Kafka | Cloud IT Zone — ARO workloads | Kafka over TLS | TCP/9092 | mTLS — SPIFFE X.509 SVID | SC-004, SC-017 | Egress (IT→Cloud) |

---

## IEC 62443 SL2 Control Mapping

| Control | Description | Implementation |
|---|---|---|
| **SC-004** | Zone separation — no direct OT-to-Cloud path; all flows route through the IT DMZ conduit | NetworkPolicy `ot-vlan-egress-{shop}` blocks all egress except TCP/8883 (MQTT) and TCP/8081 (SPIRE). Physical enforcement via Palo Alto/Cisco firewall rule CM-013. |
| **SC-017** | Identification and authentication — mutual authentication on every conduit | SPIFFE/SPIRE-issued X.509 SVIDs on every TLS handshake. Certificates are TPM-attested (OT nodes) or k8s-psat-attested (IT nodes). 1-hour TTL, automated rotation at half-TTL via Workload API — no static certificates anywhere. |
| **SC-007** | Resource management — gateway availability | Primary + standby gateway per shop (10 total). 60-second local buffer survives connectivity loss; chronological FIFO replay on reconnect. |
| **SC-010** | Least privilege — Kafka ACLs | Each gateway SVID is authorised to write only to its own shop DDATA topic (`bmw.munich.uns.ddata.{shop}.v1`). |

---

## Trust Domain

All cross-zone identities share the single SPIFFE trust domain **`munich.bmw`** provisioned by the `spiffe-spire` module. No additional trust roots are introduced by this module.

- OT edge gateway SVID template: `spiffe://munich.bmw/edge-gateways/{shop}/{node_index}`
- MQTT broker SVID: `spiffe://munich.bmw/uns-backbone/mqtt-kafka-bridge`
- Kafka SVID: `spiffe://munich.bmw/uns-backbone/kafka`

Unauthenticated connection attempts to TCP/8883 are rejected at the TLS handshake by HiveMQ's mutual-TLS listener configuration (verified by `TestMtlsRejectsExpiredSvid` and `TestMtlsRejectsUntrustedCert` in `tests/test_mtls_spiffe.py`).
