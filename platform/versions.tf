terraform {
  required_version = ">= 1.6"

  required_providers {
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.53"
    }
  }
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "entra_tenant_id" {
  type        = string
  description = "Azure / Entra ID tenant ID; read from ARM_TENANT_ID environment variable"
}

variable "audit_log_endpoint" {
  type        = string
  description = "Base URL of the immutable-audit-log service; read from AUDIT_LOG_ENDPOINT environment variable"
}
