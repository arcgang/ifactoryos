# jira-integration

**Technology:** Jira REST API v3, OAuth 2.0

**LLD component:** `cmp:jira-integration`

Corrective action integration — Quality Studio pre-populates Jira tickets from DefectGenome cluster results; single confirm click to submit; no PII transmitted

## OAuth 2.0 application registration

The module provisions `defect-genome-jira-client` as a Jira OAuth 2.0 application using the Client Credentials grant (service-to-service, no user context). Scopes:

| Scope | Purpose |
|---|---|
| `write:jira-work` | Create and update corrective-action Jira issues |
| `read:jira-work` | Read issue state to enforce idempotency (HTTP 409 on duplicate) |

No user-identity scopes are requested. The corrective-action payload transmits **VIN, cluster ID, and probability score only** — no operator-identifiable data (GDPR / Works Council §87 BetrVG).

## HashiCorp Vault secret

The Jira OAuth 2.0 client credentials are stored at:

```
secret/data/jira/defect-genome-client-secret
```

Fields written to this path:

| Field | Description |
|---|---|
| `client_id` | OAuth 2.0 client ID issued by Jira |
| `client_secret` | OAuth 2.0 client secret (rotated every 90 days per EA-SEC-002) |
| `jira_base_url` | Base URL of the Jira instance |
| `scopes` | Space-delimited scope string: `write:jira-work read:jira-work` |

### Vault Agent Injector sidecar

The `defect-genome` pod uses the Vault Agent Injector to mount the secret at startup. Add the following annotations to the `defect-genome` Kubernetes pod spec:

```yaml
annotations:
  vault.hashicorp.com/agent-inject: "true"
  vault.hashicorp.com/role: "defect-genome-jira-<environment>"
  vault.hashicorp.com/agent-inject-secret-jira-client: "secret/data/jira/defect-genome-client-secret"
  vault.hashicorp.com/agent-inject-template-jira-client: |
    {{- with secret "secret/data/jira/defect-genome-client-secret" -}}
    JIRA_CLIENT_ID={{ .Data.data.client_id }}
    JIRA_CLIENT_SECRET={{ .Data.data.client_secret }}
    JIRA_BASE_URL={{ .Data.data.jira_base_url }}
    JIRA_SCOPES={{ .Data.data.scopes }}
    {{- end }}
```

The injected file is written to `/vault/secrets/jira-client` inside the pod. The `defect-genome` service reads `JIRA_CLIENT_ID` and `JIRA_CLIENT_SECRET` from this file at startup.

### Access control

- **Vault policy:** `defect-genome-jira-read-<environment>` — `read` on `secret/data/jira/defect-genome-client-secret` only; no write or delete.
- **Kubernetes auth role:** `defect-genome-jira-<environment>` — bound to service account `defect-genome` in namespace `defect-genome`.
- **Token TTL:** 1 hour; max 24 hours. The Vault agent renews automatically.

### Rotation schedule

The client secret is rotated every **90 days** per EA-SEC-002. KV-v2 `delete_version_after` is set to 2160 h (90 days) and `max_versions` is 5, so five previous versions are retained for emergency rollback. The rotation is performed by the platform secrets-rotation service account, which holds a separate Vault token with `update` capability — the `defect-genome` service account has read-only access and cannot trigger rotation.

## Environment variables

See `.env.example` for the variables consumed by this module and by the `defect-genome` service at runtime.

## Smoke tests

```sh
cd platform/jira-integration
terraform test -filter=tests/vault_secret_readable.tftest.hcl
```

All five assertions run in `plan` mode with a mock Vault provider — no live Vault or Kubernetes credentials required.
