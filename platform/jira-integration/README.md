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
