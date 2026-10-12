# Run ifactoryos locally

Prerequisites: Docker Desktop, Node 20+, Python 3.11+.

```sh
git clone https://github.com/arcgang/ifactoryos.git && cd ifactoryos
make up        # the local platform: kafka, schema-registry, mqtt, neo4j, oidc, vault, azurite, otel-lgtm, citus
make install   # every component's dependencies
make test      # every component's tests
make help      # everything else
```

## Platform (docker compose)

| Service | Stands in for | Connect with |
|---|---|---|
| kafka — single-node Kafka (KRaft), topics auto-created | uns-backbone | `KAFKA_BOOTSTRAP_SERVERS=localhost:9092` |
| schema-registry — Confluent Schema Registry against the local Kafka | uns-backbone | `SCHEMA_REGISTRY_URL=http://localhost:8085` |
| mqtt — Mosquitto, anonymous, for local publishing | uns-backbone, edge-gateways | `MQTT_URL=mqtt://localhost:1883` |
| neo4j — Neo4j 5 community | vin-digital-thread | `NEO4J_URI=bolt://localhost:7687`<br>`NEO4J_USER=neo4j`<br>`NEO4J_PASSWORD=localpassword` |
| oidc — mock OAuth2/OIDC server standing in for the identity provider — any login succeeds | bmw-entra-id | `OIDC_ISSUER_URL=http://localhost:8090/default`<br>`OIDC_CLIENT_ID=local`<br>`OIDC_CLIENT_SECRET=local` |
| vault — Vault in dev mode — in-memory, unsealed, NOT for real secrets | hashicorp-vault | `VAULT_ADDR=http://localhost:8200`<br>`VAULT_TOKEN=local-root` |
| azurite — Azurite blob emulator (no WORM / object lock) | immutable-audit-log | `AZURE_STORAGE_CONNECTION_STRING=UseDevelopmentStorage=true` |
| otel-lgtm — Grafana + Prometheus + Tempo + Loki with an OTLP endpoint, in one container | observability-stack | `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` |
| citus — single-node Citus (coordinator only) | postgresql-citus | `CITUS_URL=postgresql://postgres:local@localhost:5434/app` |

The connection settings are in `.env.example` (copied to `.env` by `make up`).

### Not run locally

No faithful local stand-in — stub or mock these in tests and point at a shared environment for integration:

- api-gateway (IBM API Connect, IBM DataPower Gateway, OAuth 2.0 / OIDC)
- spiffe-spire (SPIFFE/SPIRE, X.509 SVIDs, mTLS)
- jira-integration (Jira REST API v3, OAuth 2.0)

## Components

| Component | Path | Run | Notes |
|---|---|---|---|
| planning-cockpit | `apps/planning-cockpit` | `make dev-planning-cockpit` | `make test-planning-cockpit` |
| line-command | `apps/line-command` | `make dev-line-command` | `make test-line-command` |
| quality-studio | `apps/quality-studio` | `make dev-quality-studio` | `make test-quality-studio` |
| opcenter-sync-service | `services/opcenter-sync-service` | — | no toolchain yet — the first task for this component lays it down |
| sequence-optimizer | `services/sequence-optimizer` | `make dev-sequence-optimizer` | `make test-sequence-optimizer` |
| defect-genome | `services/defect-genome` | `make dev-defect-genome` | `make test-defect-genome` |
| hyperledger-fabric | `services/hyperledger-fabric` | — | no toolchain yet — the first task for this component lays it down |
| timescaledb | `services/timescaledb` | — | no toolchain yet — the first task for this component lays it down |
| line-command-api | `services/line-command-api` | `make dev-line-command-api` | `make test-line-command-api` |
| vin-digital-thread-api | `services/vin-digital-thread-api` | `make dev-vin-digital-thread-api` | `make test-vin-digital-thread-api` |
| hyperledger-fabric-api | `services/hyperledger-fabric-api` | `make dev-hyperledger-fabric-api` | `make test-hyperledger-fabric-api` |
