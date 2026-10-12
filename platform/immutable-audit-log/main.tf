# immutable-audit-log — Azure Blob Storage (WORM/Object Lock), AES-256 CMK encryption
# LLD component cmp:immutable-audit-log. Provision and configure it here; nothing in this module is applied
# until a pull request adds the provider resources and the plan is reviewed.
#
# Design decisions honoured here:
#   - WORM (time-based immutability, locked) with 7-year minimum retention
#   - Cold-storage archival after 7 years — no deletion permitted
#   - AES-256 SSE with customer-managed keys from HashiCorp Vault via Azure Key Vault
#   - Azure Germany West Central only (EU data residency hard constraint)
#   - RBAC: Storage Blob Data Contributor for app MIs, Storage Blob Data Reader for compliance MI

variable "environment" {
  type = string
}

# ---------------------------------------------------------------------------
# Resource group — Germany West Central, EU data residency
# ---------------------------------------------------------------------------
resource "azurerm_resource_group" "audit_log" {
  name     = "rg-immutable-audit-log-${var.environment}"
  location = "germanywestcentral"

  tags = {
    environment      = var.environment
    data_residency   = "eu-de-west-central"
    classification   = "confidential"
    component        = "cmp:immutable-audit-log"
  }
}

# ---------------------------------------------------------------------------
# Azure Key Vault — holds the CMK for SSE encryption
# Vault-managed key is rotated by HashiCorp Vault agent injector (max 90 days,
# per EA-SEC-002).  AKV is the Azure-platform credential store; HCP Vault is
# the application-layer store — settled decision security:hashicorp-vault.
# ---------------------------------------------------------------------------
resource "azurerm_key_vault" "audit_cmk" {
  name                        = "kv-audit-cmk-${var.environment}"
  resource_group_name         = azurerm_resource_group.audit_log.name
  location                    = azurerm_resource_group.audit_log.location
  tenant_id                   = data.azurerm_client_config.current.tenant_id
  sku_name                    = "premium"
  purge_protection_enabled    = true
  soft_delete_retention_days  = 90

  tags = azurerm_resource_group.audit_log.tags
}

data "azurerm_client_config" "current" {}

resource "azurerm_key_vault_key" "audit_cmk" {
  name         = "audit-log-cmk"
  key_vault_id = azurerm_key_vault.audit_cmk.id
  key_type     = "RSA"
  key_size     = 4096
  key_opts     = ["decrypt", "encrypt", "sign", "unwrapKey", "verify", "wrapKey"]

  rotation_policy {
    automatic {
      time_before_expiry = "P30D"
    }
    expire_after         = "P90D"
    notify_before_expiry = "P29D"
  }
}

# Grant the storage account identity access to the CMK
resource "azurerm_key_vault_access_policy" "storage_cmk" {
  key_vault_id = azurerm_key_vault.audit_cmk.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_storage_account.audit_log.identity[0].principal_id

  key_permissions = ["Get", "WrapKey", "UnwrapKey"]
}

# ---------------------------------------------------------------------------
# Storage account — ZRS, Germany West Central, TLS 1.2+, infrastructure encryption
# ---------------------------------------------------------------------------
resource "azurerm_storage_account" "audit_log" {
  name                            = "stauditlog${var.environment}"
  resource_group_name             = azurerm_resource_group.audit_log.name
  location                        = azurerm_resource_group.audit_log.location
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  infrastructure_encryption_enabled = true

  identity {
    type = "SystemAssigned"
  }

  tags = azurerm_resource_group.audit_log.tags
}

# AES-256 SSE with the CMK from Azure Key Vault
resource "azurerm_storage_account_customer_managed_key" "audit_cmk" {
  storage_account_id = azurerm_storage_account.audit_log.id
  key_vault_id       = azurerm_key_vault.audit_cmk.id
  key_name           = azurerm_key_vault_key.audit_cmk.name

  depends_on = [azurerm_key_vault_access_policy.storage_cmk]
}

# ---------------------------------------------------------------------------
# Blob container — WORM time-based immutability (locked, 7-year minimum)
# ---------------------------------------------------------------------------
resource "azurerm_storage_container" "audit_events" {
  name                  = "audit-events"
  storage_account_id    = azurerm_storage_account.audit_log.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "audit_log" {
  storage_account_id = azurerm_storage_account.audit_log.id

  rule {
    name    = "archive-after-7-years"
    enabled = true

    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["audit-events/"]
    }

    actions {
      base_blob {
        # Transition to Archive tier at 7 years (2555 days).
        # Standard rehydration provides ≤24 h query SLA (settled decision: data:worm-audit-log-7-year-retention).
        # No delete action — deletion is prohibited for regulatory compliance.
        tier_to_archive_after_days_since_creation_greater_than = 2555
      }
    }
  }
}

# WORM immutability policy — time-based, locked, 7-year minimum retention.
# The policy is locked so it cannot be shortened or removed; only extension is permitted.
resource "azurerm_storage_blob_immutability_policy" "audit_events" {
  storage_container_resource_manager_id = azurerm_storage_container.audit_events.resource_manager_id
  immutability_period_in_days           = 2555
  state                                 = "Locked"

  # Locked state means: cannot reduce period, cannot delete blobs within retention window,
  # satisfies SEC 17a-4(f) / IEC 62443 SL2 / GDPR Article 30 requirements.
  protected_append_writes_all_enabled = true
}

# ---------------------------------------------------------------------------
# RBAC assignments — app managed identities get Contributor; compliance reader gets Reader
# ---------------------------------------------------------------------------
locals {
  contributor_mi_names = [
    "planning-cockpit-mi",
    "line-command-mi",
    "quality-studio-mi",
  ]
}

data "azurerm_user_assigned_identity" "contributors" {
  for_each            = toset(local.contributor_mi_names)
  name                = each.key
  resource_group_name = azurerm_resource_group.audit_log.name
}

data "azurerm_user_assigned_identity" "compliance_reader" {
  name                = "compliance-reader-mi"
  resource_group_name = azurerm_resource_group.audit_log.name
}

resource "azurerm_role_assignment" "audit_contributors" {
  for_each             = data.azurerm_user_assigned_identity.contributors
  scope                = azurerm_storage_container.audit_events.resource_manager_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = each.value.principal_id
}

resource "azurerm_role_assignment" "audit_compliance_reader" {
  scope                = azurerm_storage_container.audit_events.resource_manager_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = data.azurerm_user_assigned_identity.compliance_reader.principal_id
}

# ---------------------------------------------------------------------------
# Outputs consumed by client SDKs
# ---------------------------------------------------------------------------
output "storage_account_name" {
  description = "Storage account name for audit-client configuration"
  value       = azurerm_storage_account.audit_log.name
}

output "audit_container_name" {
  description = "Container name that holds audit blobs"
  value       = azurerm_storage_container.audit_events.name
}

output "storage_account_id" {
  description = "Resource ID of the audit log storage account"
  value       = azurerm_storage_account.audit_log.id
}
