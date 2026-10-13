terraform {
  required_version = ">= 1.6"

  required_providers {
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.53"
    }
    postgresql = {
      source  = "cyrilgdn/postgresql"
      version = "~> 1.22"
    }
  }
}

# Provider configured in root module (platform/) via environment variables at apply time:
#   ARM_CLIENT_ID, ARM_CLIENT_SECRET, ARM_TENANT_ID
# The service principal requires:
#   Application.ReadWrite.All
#   Policy.ReadWrite.ConditionalAccess
#   GroupMember.ReadWrite.All
# Secrets are injected by HashiCorp Vault agent injector — never hardcoded here.
# See .env.example at repo root for required variable names.
