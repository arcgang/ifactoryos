# api-gateway — IBM API Connect, IBM DataPower Gateway, OAuth 2.0 / OIDC
# LLD component cmp:api-gateway. Provision and configure it here; nothing in this module is applied
# until a pull request adds the provider resources and the plan is reviewed.

variable "environment" {
  type = string
}

locals {
  # Base path for OpenAPI 3.1 contract files (relative to this module).
  contracts_dir = "${path.module}/contracts/openapi"

  # Rate-limit plan per API (requests/minute), matching x-rate-limit in each spec.
  rate_limit_plans = {
    sequence_optimisation     = "120rpm"
    constraint_radar          = "120rpm"
    line_command_oee          = "120rpm"
    ar_work_instruction       = "300rpm"
    shift_handover            = "60rpm"
    process_replay            = "60rpm"
    defect_genome             = "60rpm"
    gate_certification        = "60rpm"
    digital_birth_certificate = "60rpm"
    adoption_instrumentation  = "600rpm"
  }
}

# ---------------------------------------------------------------------------
# IBM API Connect — one ibm_apic_api resource per OpenAPI 3.1 contract
# ---------------------------------------------------------------------------

resource "ibm_apic_api" "sequence_optimisation" {
  name            = "sequence-optimisation"
  display_name    = "Sequence Optimisation API"
  version         = "1.0.0"
  spec_file       = "${local.contracts_dir}/sequence-optimisation-v1.yaml"
  environment     = var.environment
  rate_limit_plan = local.rate_limit_plans["sequence_optimisation"]
  stub_generation = true
}

resource "ibm_apic_api" "constraint_radar" {
  name            = "constraint-radar"
  display_name    = "Constraint Radar API"
  version         = "1.0.0"
  spec_file       = "${local.contracts_dir}/constraint-radar-v1.yaml"
  environment     = var.environment
  rate_limit_plan = local.rate_limit_plans["constraint_radar"]
  stub_generation = true
}

resource "ibm_apic_api" "line_command_oee" {
  name            = "line-command-oee"
  display_name    = "Line Command OEE API"
  version         = "1.0.0"
  spec_file       = "${local.contracts_dir}/line-command-oee-v1.yaml"
  environment     = var.environment
  rate_limit_plan = local.rate_limit_plans["line_command_oee"]
  stub_generation = true
}

resource "ibm_apic_api" "ar_work_instruction" {
  name            = "ar-work-instruction"
  display_name    = "AR Work Instruction API"
  version         = "1.0.0"
  spec_file       = "${local.contracts_dir}/ar-work-instruction-v1.yaml"
  environment     = var.environment
  rate_limit_plan = local.rate_limit_plans["ar_work_instruction"]
  stub_generation = true
}

resource "ibm_apic_api" "shift_handover" {
  name            = "shift-handover"
  display_name    = "Shift Handover API"
  version         = "1.0.0"
  spec_file       = "${local.contracts_dir}/shift-handover-v1.yaml"
  environment     = var.environment
  rate_limit_plan = local.rate_limit_plans["shift_handover"]
  stub_generation = true
}

resource "ibm_apic_api" "process_replay" {
  name            = "process-replay"
  display_name    = "Process Replay API"
  version         = "1.0.0"
  spec_file       = "${local.contracts_dir}/process-replay-v1.yaml"
  environment     = var.environment
  rate_limit_plan = local.rate_limit_plans["process_replay"]
  stub_generation = true
}

resource "ibm_apic_api" "defect_genome" {
  name            = "defect-genome"
  display_name    = "Defect Genome API"
  version         = "1.0.0"
  spec_file       = "${local.contracts_dir}/defect-genome-v1.yaml"
  environment     = var.environment
  rate_limit_plan = local.rate_limit_plans["defect_genome"]
  stub_generation = true
}

resource "ibm_apic_api" "gate_certification" {
  name            = "gate-certification"
  display_name    = "Gate Certification API"
  version         = "1.0.0"
  spec_file       = "${local.contracts_dir}/gate-certification-v1.yaml"
  environment     = var.environment
  rate_limit_plan = local.rate_limit_plans["gate_certification"]
  stub_generation = true
}

resource "ibm_apic_api" "digital_birth_certificate" {
  name            = "digital-birth-certificate"
  display_name    = "Digital Birth Certificate API"
  version         = "1.0.0"
  spec_file       = "${local.contracts_dir}/digital-birth-certificate-v1.yaml"
  environment     = var.environment
  rate_limit_plan = local.rate_limit_plans["digital_birth_certificate"]
  stub_generation = true
}

resource "ibm_apic_api" "adoption_instrumentation" {
  name            = "adoption-instrumentation"
  display_name    = "Adoption Instrumentation API"
  version         = "1.0.0"
  spec_file       = "${local.contracts_dir}/adoption-instrumentation-v1.yaml"
  environment     = var.environment
  rate_limit_plan = local.rate_limit_plans["adoption_instrumentation"]
  stub_generation = true
}

# ---------------------------------------------------------------------------
# IBM API Connect Developer Portal — single catalogue for all internal consumers
# Horizon 2 teams generate client stubs directly without contacting Horizon 1.
# ---------------------------------------------------------------------------

resource "ibm_apic_developer_portal" "ifactoryos" {
  name        = "ifactoryos-developer-portal"
  environment = var.environment
  title       = "iFactoryOS Developer Portal"
  description = "Internal developer portal for iFactoryOS inter-console APIs."

  apis = [
    ibm_apic_api.sequence_optimisation.id,
    ibm_apic_api.constraint_radar.id,
    ibm_apic_api.line_command_oee.id,
    ibm_apic_api.ar_work_instruction.id,
    ibm_apic_api.shift_handover.id,
    ibm_apic_api.process_replay.id,
    ibm_apic_api.defect_genome.id,
    ibm_apic_api.gate_certification.id,
    ibm_apic_api.digital_birth_certificate.id,
    ibm_apic_api.adoption_instrumentation.id,
  ]

  # Enforces the 180-day minimum support window from versioning-policy.md.
  # Deprecation and Sunset headers are set automatically on the previous
  # major version's responses when a new major version is registered.
  deprecation_policy = {
    minimum_support_days       = 180
    grace_period_days          = 30
    block_new_subscriptions_at = 180
  }
}
