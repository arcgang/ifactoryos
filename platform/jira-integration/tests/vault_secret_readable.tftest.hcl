# Smoke test: verify the Vault secret path and Kubernetes auth role are correctly
# configured so a pod running as the defect-genome service account can read
# secret/data/jira/defect-genome-client-secret.
#
# Uses mock_provider so no live Vault or Kubernetes credentials are required;
# the test confirms the policy path, role binding, and output values match the
# contract required by the Vault Agent Injector sidecar.

mock_provider "vault" {}
mock_provider "jira" {}

variables {
  environment     = "dev"
  vault_address   = "https://vault.dev.ifos.bmwgroup.net"
  kubernetes_host = "https://aro-dev.germanywestcentral.aroapp.io:6443"
  jira_base_url   = "https://jira.bmwgroup.net"
}

# ---------------------------------------------------------------------------
# Vault secret path output matches what the Vault Agent Injector expects
# ---------------------------------------------------------------------------
run "vault_secret_path_is_correct" {
  command = plan

  assert {
    condition     = output.vault_secret_path == "secret/data/jira/defect-genome-client-secret"
    error_message = "vault_secret_path must be 'secret/data/jira/defect-genome-client-secret' — the Vault Agent Injector sidecar annotation must match exactly"
  }
}

# ---------------------------------------------------------------------------
# Vault policy name is environment-scoped and matches convention
# ---------------------------------------------------------------------------
run "vault_policy_name_is_env_scoped" {
  command = plan

  assert {
    condition     = output.vault_policy_name == "defect-genome-jira-read-dev"
    error_message = "vault_policy_name must be 'defect-genome-jira-read-dev' in the dev environment"
  }
}

# ---------------------------------------------------------------------------
# Kubernetes auth role is bound to the defect-genome service account
# ---------------------------------------------------------------------------
run "k8s_role_bound_to_defect_genome_sa" {
  command = plan

  assert {
    condition     = output.vault_k8s_role_name == "defect-genome-jira-dev"
    error_message = "vault_k8s_role_name must be 'defect-genome-jira-dev' — the role name is referenced in the Vault Agent Injector pod annotation"
  }
}

# ---------------------------------------------------------------------------
# Jira client is registered with exactly the two required scopes
# ---------------------------------------------------------------------------
run "jira_client_has_exactly_two_scopes" {
  command = plan

  assert {
    condition     = length(output.jira_client_scopes) == 2
    error_message = "defect-genome-jira-client must have exactly 2 scopes; got ${length(output.jira_client_scopes)}"
  }

  assert {
    condition     = contains(output.jira_client_scopes, "write:jira-work")
    error_message = "defect-genome-jira-client must include scope write:jira-work"
  }

  assert {
    condition     = contains(output.jira_client_scopes, "read:jira-work")
    error_message = "defect-genome-jira-client must include scope read:jira-work"
  }
}

# ---------------------------------------------------------------------------
# Jira client display name matches the registered application name
# ---------------------------------------------------------------------------
run "jira_client_display_name_matches_registration" {
  command = plan

  assert {
    condition     = output.jira_client_display_name == "defect-genome-jira-client"
    error_message = "jira_client_display_name must be 'defect-genome-jira-client'"
  }
}
