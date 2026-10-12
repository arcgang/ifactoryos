# uns-backbone — Kafka ACL matrix SC-002 as confluent_kafka_acl resources
#
# Realises US-027 · BR-10: IEC 62443 SL2 zone-and-conduit model; only explicitly
# listed principals may perform the stated operation; all others receive implicit DENY.
#
# SC-002 matrix (authoritative):
#   opcenter-sync-service  WRITE  factory.munich.opcenter.andon.alert.v1
#   opcenter-sync-service  WRITE  factory.munich.opcenter.oee.snapshot.v1
#   line-command-api       READ   factory.munich.opcenter.andon.alert.v1
#   line-command-api       READ   factory.munich.opcenter.oee.snapshot.v1
#   line-command-api       READ   factory.munich.line-command.adoption.v1
#   line-command-api       WRITE  factory.munich.line-command.maintenance-dispatch.v1
#   defect-genome          READ   bmw.munich.paint.defect.events.v1
#   defect-genome          WRITE  bmw.munich.defect.cluster.alert.v1
#   quality-studio         READ   bmw.munich.defect.cluster.alert.v1
#   planning-cockpit       WRITE  factory.munich.line-command.adoption.v1
#   edge-{shop}-{index}    WRITE  spBv1.0/munich-i4/DDATA/{zone}/{device}  (Prefixed pattern)
#   All others             DENY   (implicit — no confluent_kafka_acl means no access)

terraform {
  required_providers {
    confluent = {
      source  = "confluentinc/confluent"
      version = "~> 1.76"
    }
  }
}

# ---------------------------------------------------------------------------
# Provider reference — Confluent Cloud / on-prem Kafka REST API
# Credentials are injected by Vault agent injector; never hardcoded.
# ---------------------------------------------------------------------------

variable "confluent_kafka_id" {
  type        = string
  description = "Confluent Kafka cluster ID (lkc-xxxxx for Confluent Cloud, or on-prem cluster ID)"
}

variable "confluent_rest_endpoint" {
  type        = string
  description = "Confluent Kafka REST endpoint (https://pkc-xxxxx.confluent.svc.cluster.local:8090)"
  default     = "https://uns-kafka.confluent.svc.cluster.local:8090"
}

variable "confluent_kafka_api_key" {
  type        = string
  sensitive   = true
  description = "Kafka REST API key — injected by Vault agent; never hardcoded"
}

variable "confluent_kafka_api_secret" {
  type        = string
  sensitive   = true
  description = "Kafka REST API secret — injected by Vault agent; never hardcoded"
}

# ---------------------------------------------------------------------------
# Locals — SC-002 ACL matrix
#
# Each entry becomes one confluent_kafka_acl resource. The key is used as the
# Terraform resource label; it must be unique and contain only [a-z0-9_-].
# Edge gateway ACLs are derived separately from the gateways_map local so that
# adding a shop or gateway requires no manual ACL edits.
# ---------------------------------------------------------------------------

locals {
  sc002_acls = [
    {
      key           = "opcenter-sync-service-write-andon"
      principal     = "User:opcenter-sync-service"
      operation     = "WRITE"
      topic_name    = "factory.munich.opcenter.andon.alert.v1"
      pattern_type  = "LITERAL"
    },
    {
      key           = "opcenter-sync-service-write-oee"
      principal     = "User:opcenter-sync-service"
      operation     = "WRITE"
      topic_name    = "factory.munich.opcenter.oee.snapshot.v1"
      pattern_type  = "LITERAL"
    },
    {
      key           = "line-command-api-read-andon"
      principal     = "User:line-command-api"
      operation     = "READ"
      topic_name    = "factory.munich.opcenter.andon.alert.v1"
      pattern_type  = "LITERAL"
    },
    {
      key           = "line-command-api-read-oee"
      principal     = "User:line-command-api"
      operation     = "READ"
      topic_name    = "factory.munich.opcenter.oee.snapshot.v1"
      pattern_type  = "LITERAL"
    },
    {
      key           = "line-command-api-read-adoption"
      principal     = "User:line-command-api"
      operation     = "READ"
      topic_name    = "factory.munich.line-command.adoption.v1"
      pattern_type  = "LITERAL"
    },
    {
      key           = "line-command-api-write-maintenance-dispatch"
      principal     = "User:line-command-api"
      operation     = "WRITE"
      topic_name    = "factory.munich.line-command.maintenance-dispatch.v1"
      pattern_type  = "LITERAL"
    },
    {
      key           = "defect-genome-read-paint-defects"
      principal     = "User:defect-genome"
      operation     = "READ"
      topic_name    = "bmw.munich.paint.defect.events.v1"
      pattern_type  = "LITERAL"
    },
    {
      key           = "defect-genome-write-cluster-alert"
      principal     = "User:defect-genome"
      operation     = "WRITE"
      topic_name    = "bmw.munich.defect.cluster.alert.v1"
      pattern_type  = "LITERAL"
    },
    {
      key           = "quality-studio-read-cluster-alert"
      principal     = "User:quality-studio"
      operation     = "READ"
      topic_name    = "bmw.munich.defect.cluster.alert.v1"
      pattern_type  = "LITERAL"
    },
    {
      key           = "planning-cockpit-write-adoption"
      principal     = "User:planning-cockpit"
      operation     = "WRITE"
      topic_name    = "factory.munich.line-command.adoption.v1"
      pattern_type  = "LITERAL"
    },
  ]

  # Five shops; two gateways per shop (01 = primary, 02 = standby).
  # These match the gateways_map in edge-gateways/main.tf.
  edge_shops = ["inbound", "body", "paint", "assembly", "quality"]

  edge_gateways = flatten([
    for shop in local.edge_shops : [
      { shop = shop, node_id = "edge-${shop}-01" },
      { shop = shop, node_id = "edge-${shop}-02" },
    ]
  ])

  edge_gateways_map = {
    for gw in local.edge_gateways :
    gw.node_id => gw
  }
}

# ---------------------------------------------------------------------------
# SC-002 ACL resources — one confluent_kafka_acl per matrix row
# ---------------------------------------------------------------------------

resource "confluent_kafka_acl" "sc002" {
  for_each = {
    for acl in local.sc002_acls :
    acl.key => acl
  }

  kafka_cluster {
    id = var.confluent_kafka_id
  }

  rest_endpoint = var.confluent_rest_endpoint

  resource_type = "TOPIC"
  resource_name = each.value.topic_name
  pattern_type  = each.value.pattern_type
  principal     = each.value.principal
  host          = "*"
  operation     = each.value.operation
  permission    = "ALLOW"

  credentials {
    key    = var.confluent_kafka_api_key
    secret = var.confluent_kafka_api_secret
  }
}

# ---------------------------------------------------------------------------
# Edge gateway ACLs — WRITE to spBv1.0/munich-i4/DDATA/{zone}/{device}
#
# Each gateway principal (User:edge-{shop}-{index}) receives a PREFIXED ALLOW
# on spBv1.0/munich-i4/DDATA/{shop}/ so it can write any device subtopic under
# its own shop zone. Cross-zone writes are denied by implicit DENY (no matching
# ALLOW exists for a different shop prefix).
#
# The Prefixed pattern_type means:
#   User:edge-inbound-01  →  WRITE  spBv1.0/munich-i4/DDATA/inbound/   ✓
#   User:edge-inbound-01  →  WRITE  spBv1.0/munich-i4/DDATA/body/      ✗ (implicit DENY)
# ---------------------------------------------------------------------------

resource "confluent_kafka_acl" "edge_gateway_write_ddata" {
  for_each = local.edge_gateways_map

  kafka_cluster {
    id = var.confluent_kafka_id
  }

  rest_endpoint = var.confluent_rest_endpoint

  resource_type = "TOPIC"
  # Prefixed pattern: covers spBv1.0/munich-i4/DDATA/{shop}/<any-device-subtopic>
  resource_name = "spBv1.0/munich-i4/DDATA/${each.value.shop}/"
  pattern_type  = "PREFIXED"
  principal     = "User:${each.key}"
  host          = "*"
  operation     = "WRITE"
  permission    = "ALLOW"

  credentials {
    key    = var.confluent_kafka_api_key
    secret = var.confluent_kafka_api_secret
  }
}

# ---------------------------------------------------------------------------
# Output — ACL count for independent verification
# ---------------------------------------------------------------------------

output "sc002_acl_count" {
  description = "Number of SC-002 confluent_kafka_acl resources (10 service + 10 edge gateway)"
  value       = length(local.sc002_acls) + length(local.edge_gateways)
}
