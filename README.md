# ifactoryos

iFactoryOS — every component of the architecture

| Component | Path | Technology | Seed |
|---|---|---|---|
| planning-cockpit | `apps/planning-cockpit` | React 18, TypeScript, OpenTelemetry SDK, BMW Entra ID OIDC | React |
| line-command | `apps/line-command` | React 18, TypeScript, Microsoft HoloLens 2 (WebXR), iPad Pro (ARKit), OpenTelemetry SDK, BMW Entra ID OIDC | React |
| quality-studio | `apps/quality-studio` | React 18, TypeScript, OpenTelemetry SDK, BMW Entra ID OIDC | React |
| opcenter-sync-service | `services/opcenter-sync-service` | Rust, Siemens OpCenter 10.2 REST/GraphQL API, Kafka producer, SPIFFE/SPIRE mTLS | blank |
| sequence-optimizer | `services/sequence-optimizer` | KServe, Python, scikit-optimize, MLflow, watsonx.governance | Python |
| defect-genome | `services/defect-genome` | KServe, Python, scikit-learn, DBSCAN, XGBoost, MLflow, watsonx.governance | Python |
| hyperledger-fabric | `services/hyperledger-fabric` | Hyperledger Fabric 2.5, Go chaincode | blank |
| api-gateway | `platform/api-gateway` | IBM API Connect, IBM DataPower Gateway, OAuth 2.0 / OIDC | blank |
| uns-backbone | `platform/uns-backbone` | Confluent Kafka, Confluent Schema Registry (Avro), MQTT Broker (HiveMQ/EMQX), Dead-Letter Queue | blank |
| edge-gateways | `platform/edge-gateways` | Siemens SIMATIC IPC, Beckhoff ADS/ADS-UDP, MQTT Sparkplug B, SPIFFE/SPIRE SVID | blank |
| vin-digital-thread | `platform/vin-digital-thread` | Neo4j 5.x, Cypher, RFID correlation_id injection | blank |
| bmw-entra-id | `platform/bmw-entra-id` | Azure Active Directory / Entra ID, OIDC, OAuth 2.0 | blank |
| spiffe-spire | `platform/spiffe-spire` | SPIFFE/SPIRE, X.509 SVIDs, mTLS | blank |
| hashicorp-vault | `platform/hashicorp-vault` | HashiCorp Vault, Vault Agent Injector, Kubernetes auth method | blank |
| immutable-audit-log | `platform/immutable-audit-log` | Azure Blob Storage (WORM/Object Lock), AES-256 encryption | blank |
| observability-stack | `platform/observability-stack` | Prometheus, Grafana, OpenTelemetry, IBM Instana | blank |
| timescaledb | `platform/timescaledb` | TimescaleDB 2.x, PostgreSQL 15 | blank |
| postgresql-citus | `platform/postgresql-citus` | PostgreSQL 15, Citus 12.x | blank |
| jira-integration | `platform/jira-integration` | Jira REST API v3, OAuth 2.0 | blank |

Part of the **ifactoryos** estate (mono layout). Contracts: `contracts/` in this repository.

Stacks: React, Python.
