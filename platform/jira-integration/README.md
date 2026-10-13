# jira-integration

**Technology:** Jira REST API v3, OAuth 2.0 (Atlassian)
**LLD component:** `cmp:jira-integration`

Provisions the IFOS-QUALITY Jira project, the `Corrective Action` issue type, and four DefectGenome-specific custom fields. Stores the OAuth 2.0 credentials in HashiCorp Vault for injection into the `defect-genome` service at runtime.

## What this module creates

| Resource | Description |
|---|---|
| `jira_project.ifos_quality` | IFOS-QUALITY project (`IFOSQUALITY` key) |
| `jira_issue_type.corrective_action` | `Corrective Action` issue type |
| `jira_issue_type_scheme.ifos_quality_scheme` | Issue type scheme linking the above to IFOS-QUALITY |
| `jira_custom_field.defect_genome_cluster_id` | `DefectGenome Cluster ID` — textfield (UUID) |
| `jira_custom_field.root_cause_probability` | `Root Cause Probability` — float (0.0–1.0) |
| `jira_custom_field.affected_vin_count` | `Affected VIN Count` — float (integer at runtime) |
| `jira_custom_field.recommended_prep_stage_adjustment` | `Recommended Prep Stage Adjustment` — textfield |
| `vault_kv_secret_v2.jira_quality_studio_client_secret` | Vault KV v2 secret at `secret/jira/quality-studio-client-secret` |
| `vault_policy.defect_genome_jira_read` | Vault policy granting `read`/`list` on `secret/data/jira/quality-studio-client-secret` |
| `vault_kubernetes_auth_backend_role.defect_genome` | Vault Kubernetes auth role binding the policy to the `defect-genome` service account |

## Manual pre-step: OAuth 2.0 app registration

The `jira_oauth2_client_id` and `jira_oauth2_client_secret` variables require an OAuth 2.0 app
registered **before** running `terraform apply`. This is a manual one-time operation per environment.

### Steps

1. Log in to the [Atlassian Developer Console](https://developer.atlassian.com/console/myapps/)
   using the BMW Group Atlassian admin account.

2. Click **Create** → **OAuth 2.0 integration**.
   - **App name:** `ifos-defect-genome-<environment>` (e.g. `ifos-defect-genome-prod`)
   - **Description:** `DefectGenome corrective-action integration for iFactoryOS paint quality engineering.`

3. Under **Permissions**, add the following scopes:
   - `write:jira-work` — required to create Corrective Action issues via `POST /rest/api/3/issue`
   - `read:jira-work` — required to read issue status after creation

4. Under **Authorization**, select **OAuth 2.0 (3LO)** → add a callback URL if prompted
   (not required for Client Credentials flow; any placeholder URL is accepted).

5. Copy the **Client ID** and **Client Secret** from the app's **Settings** page.

6. Store the secret in HashiCorp Vault:
   ```sh
   vault kv put secret/jira/quality-studio-client-secret \
     client_id="<client-id>" \
     client_secret="<client-secret>"
   ```

7. Pass them to Terraform at apply time via environment variables:
   ```sh
   export TF_VAR_jira_oauth2_client_id="<client-id>"
   export TF_VAR_jira_oauth2_client_secret="<client-secret>"
   ```
   Never hardcode these values in any Terraform file or commit them to source control.

## Manual post-apply step: write the secret into Vault

`terraform apply` creates the KV v2 **metadata** path and the Vault policy, but it does **not**
write the `client_id` / `client_secret` values — doing so would embed them in Terraform state.
After every apply, a Vault admin must write the secret values manually:

```sh
vault kv put secret/jira/quality-studio-client-secret \
  client_id="<atlassian-developer-console-client-id>" \
  client_secret="<atlassian-developer-console-client-secret>"
```

The `defect-genome` Vault Agent Injector sidecar reads this path on pod startup.
Until this step is completed, pods will start but the Jira client will fail to authenticate.

## Variables

| Variable | Required | Sensitive | Description |
|---|---|---|---|
| `environment` | yes | no | Deployment environment (`dev`, `pp`, `prod`) |
| `vault_addr` | yes | no | HashiCorp Vault address |
| `jira_base_url` | yes | no | Jira Cloud base URL |
| `jira_admin_token` | yes | **yes** | Jira Admin API token (Vault-injected) |
| `jira_oauth2_client_id` | yes | no | OAuth 2.0 client ID (manual pre-step above) |
| `jira_oauth2_client_secret` | yes | **yes** | OAuth 2.0 client secret (manual pre-step above) |
| `vault_k8s_auth_path` | no | no | Mount path of the Vault Kubernetes auth method (default: `kubernetes`) |
| `defect_genome_namespace` | no | no | Kubernetes namespace for the `defect-genome` service account (default: `defect-genome`) |

All sensitive variables must be supplied via `TF_VAR_*` environment variables injected by
the Vault Agent at apply time. See `.env.example` at the repo root for the full list.

## Outputs

| Output | Description |
|---|---|
| `vault_secret_path` | Vault KV v2 path consumed by defect-genome Vault Agent Injector |
| `jira_project_key` | Jira project key (`IFOSQUALITY`) — maps to `JIRA_PROJECT_KEY` env var |
| `jira_issue_type_id` | Issue type ID for `Corrective Action` — passed in `POST /rest/api/3/issue` |
| `custom_field_cluster_id` | Jira field ID for `DefectGenome Cluster ID` — maps to `JIRA_FIELD_CLUSTER_ID` |
| `custom_field_root_cause_probability` | Jira field ID for `Root Cause Probability` — maps to `JIRA_FIELD_ROOT_CAUSE_PROBABILITY` |
| `custom_field_affected_vin_count` | Jira field ID for `Affected VIN Count` — maps to `JIRA_FIELD_AFFECTED_VIN_COUNT` |
| `custom_field_prep_stage_adjustment` | Jira field ID for `Recommended Prep Stage Adjustment` — maps to `JIRA_FIELD_PREP_STAGE_ADJUSTMENT` |

## PII compliance

No field definition, description, or label in this module references operator names, employee IDs,
or raw VINs. Field values stored in Jira at runtime are limited to:

- **DefectGenome Cluster ID** — opaque UUID; no operator or vehicle identity data
- **Root Cause Probability** — float 0.0–1.0; XGBoost model output only
- **Affected VIN Count** — aggregate integer count; no individual VINs stored in Jira
- **Recommended Prep Stage Adjustment** — free-text process recommendation; no PII

This design complies with GDPR and Works Council §87 BetrVG requirements: no personally-identifiable
operator data is transmitted to Jira at any point.

## Verification

```sh
cd platform/jira-integration
terraform init
terraform validate
terraform test
```

`terraform test` runs all assertions in `tests/jira_rest_contract.tftest.hcl` using mock providers —
no live Vault or Jira credentials are required.
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
