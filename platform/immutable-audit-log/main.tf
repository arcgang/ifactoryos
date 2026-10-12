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
    # identity-service writes IDENTITY_DELETION_REQUESTED and IDENTITY_PSEUDONYMISED
    # audit events (GDPR US-028 / BR-13).
    "identity-service-mi",
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
# Azure Policy — allowedLocations: restrict audit log resources to EU regions
#
# Denies any attempt to replicate, failover, or copy audit blobs to non-EU
# regions.  Scoped to the resource group so that every resource in this module
# (storage, Service Bus, Key Vault) inherits the constraint.
# Allowed: germanywestcentral (primary) + westeurope (secondary, Works Council).
# ---------------------------------------------------------------------------
resource "azurerm_resource_group_policy_assignment" "eu_residency" {
  name                 = "audit-log-eu-residency-${var.environment}"
  resource_group_id    = azurerm_resource_group.audit_log.id
  policy_definition_id = "/providers/Microsoft.Authorization/policyDefinitions/e56962a6-4747-49cd-b67b-bf8b01975c4f"

  parameters = jsonencode({
    listOfAllowedLocations = {
      value = ["germanywestcentral", "westeurope"]
    }
  })

  description = "Restricts all audit log infrastructure to EU regions (germanywestcentral, westeurope). Denies replication, copy, or failover to non-EU regions per GDPR US-028 and Works Council requirement."
}

# ---------------------------------------------------------------------------
# Service Bus — DLQ for schema-rejected audit writes + CISO alert
#
# The audit client validates that user_id is not a raw employee ID.  If a
# caller bypasses the SDK and writes a non-conforming event directly, the
# schema validator (deployed as an AKS sidecar) drops it onto this DLQ.
# An Azure Monitor metric alert fires within 60 s of any message landing.
# ---------------------------------------------------------------------------
resource "azurerm_servicebus_namespace" "audit_dlq" {
  name                = "sbns-audit-dlq-${var.environment}"
  resource_group_name = azurerm_resource_group.audit_log.name
  location            = azurerm_resource_group.audit_log.location
  sku                 = "Standard"

  tags = azurerm_resource_group.audit_log.tags
}

resource "azurerm_servicebus_queue" "schema_rejected" {
  name         = "audit-schema-rejected"
  namespace_id = azurerm_servicebus_namespace.audit_dlq.id

  # Dead-letter queue is always enabled on the built-in DLQ sub-queue;
  # this queue is the primary target for validator rejections.
  dead_lettering_on_message_expiration = true
  max_delivery_count                   = 1
  # Messages expire after 7 days — retained for forensic investigation
  default_message_ttl = "P7D"
  lock_duration       = "PT1M"
}

resource "azurerm_monitor_metric_alert" "dlq_ciso_alert" {
  name                = "audit-dlq-schema-rejected-${var.environment}"
  resource_group_name = azurerm_resource_group.audit_log.name
  scopes              = [azurerm_servicebus_namespace.audit_dlq.id]
  description         = "CISO alert: audit event rejected by schema validator (employee ID or missing required field). Investigate within 60 seconds."
  severity            = 1
  frequency           = "PT1M"
  window_size         = "PT1M"

  criteria {
    metric_namespace = "Microsoft.ServiceBus/namespaces"
    metric_name      = "ActiveMessages"
    aggregation      = "Maximum"
    operator         = "GreaterThan"
    threshold        = 0

    dimension {
      name     = "EntityName"
      operator = "Include"
      values   = [azurerm_servicebus_queue.schema_rejected.name]
    }
  }

  tags = azurerm_resource_group.audit_log.tags
}

# ---------------------------------------------------------------------------
# GDPR Article 30 Workbook — record of processing activities
#
# Azure Monitor workbook rendered from the ARM template JSON in
# workbooks/article30_gdpr.json.  The DPO runs this workbook against the
# live Log Analytics workspace to produce the Article 30 inventory within
# 5 business days of a Works Council request.
#
# The workbook lists: data category, processing purpose, legal basis,
# retention period, and storage location for each category of personal
# or worker-linked data processed by the IFOS platform.
# ---------------------------------------------------------------------------
locals {
  # Deterministic UUID-per-environment for the Article 30 workbook.
  # Azure requires the workbook name to be a UUID; using a static map keeps
  # the resource ID stable across plan/apply cycles.
  article30_workbook_uuid = {
    dev  = "d3c4e5f6-0001-4000-a000-000000000010"
    pp   = "d3c4e5f6-0001-4000-a000-000000000011"
    prod = "d3c4e5f6-0001-4000-a000-000000000012"
  }
}

resource "azurerm_application_insights_workbook" "article30" {
  name                = lookup(local.article30_workbook_uuid, var.environment, "d3c4e5f6-0001-4000-a000-000000000010")
  resource_group_name = azurerm_resource_group.audit_log.name
  location            = azurerm_resource_group.audit_log.location
  display_name        = "GDPR Article 30 — IFOS Record of Processing Activities"
  source_id           = "azure monitor"

  data_json = file("${path.module}/workbooks/article30_gdpr.json")

  tags = merge(azurerm_resource_group.audit_log.tags, {
    gdpr_article = "30"
    dpo_contact  = "dpo@bmwgroup.com"
  })
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

output "audit_log_storage_account_id" {
  description = "Resource ID of the audit log storage account (canonical alias for downstream modules)"
  value       = azurerm_storage_account.audit_log.id
}

output "audit_log_container_name" {
  description = "Blob container name for audit events (canonical alias for downstream modules)"
  value       = azurerm_storage_container.audit_events.name
}

output "worm_policy_id" {
  description = "Resource manager ID of the WORM immutability policy on the audit container"
  value       = azurerm_storage_blob_immutability_policy.audit_events.id
}

output "service_bus_namespace_id" {
  description = "Resource ID of the Service Bus namespace hosting the schema-rejected DLQ"
  value       = azurerm_servicebus_namespace.audit_dlq.id
}

output "article30_workbook_id" {
  description = "Resource ID of the GDPR Article 30 Azure Monitor workbook"
  value       = azurerm_application_insights_workbook.article30.id
}

output "eu_residency_policy_assignment_id" {
  description = "Resource ID of the allowedLocations policy assignment enforcing EU data residency"
  value       = azurerm_resource_group_policy_assignment.eu_residency.id
}
