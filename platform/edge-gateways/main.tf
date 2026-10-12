# edge-gateways — Siemens SIMATIC IEC 62443 SL2, Beckhoff ADS/ADS-UDP, MQTT Sparkplug B
# LLD component cmp:edge-gateways. Realises US-026 · BR-01 · BR-15.
#
# Decisions applied:
#   - Terraform owns SPIRE registration entries, Kafka ACLs, and TLS certificate plumbing.
#   - Ansible owns gateway OS and application (SIMATIC Edge Runtime) configuration.
#   - 2 gateways per shop (10 total): primary + standby for IEC 62443 SL2 availability.
#   - mTLS via SPIFFE/SPIRE-issued SVIDs; SVID rotation handled by SPIRE agent, zero-interrupt.
#   - 60-second local buffer on connectivity loss; chronological replay on reconnect.
#   - MQTT Sparkplug B namespace: spBv1.0/Munich/{shop}/{edge_node_id}/{device_id}
#   - Kafka topic: bmw.munich.uns.ddata.{shop}.v1 (bridge handles translation in uns-backbone).

variable "environment" {
  type = string
}

variable "spire_server_address" {
  type        = string
  description = "SPIRE server address for SVID registration (e.g. spire-server.spire.svc.cluster.local:8081)"
  default     = "spire-server.spire.svc.cluster.local:8081"
}

variable "kafka_bootstrap_servers" {
  type        = string
  description = "Confluent Kafka bootstrap servers (consumed from uns-backbone output)"
  default     = "uns-kafka-bootstrap.confluent.svc.cluster.local:9092"
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------

locals {
  # Five in-scope shops; each gets a primary and standby gateway (IEC 62443 SL2).
  shops = ["inbound", "body", "paint", "assembly", "quality"]

  # 10 gateways: 2 per shop (01 = primary, 02 = standby)
  gateways = flatten([
    for shop in local.shops : [
      {
        shop         = shop
        node_id      = "edge-${shop}-01"
        node_index   = "01"
        role         = "primary"
        spiffe_id    = "spiffe://ifos.munich.internal/edge-gateways/${shop}/01"
      },
      {
        shop         = shop
        node_id      = "edge-${shop}-02"
        node_index   = "02"
        role         = "standby"
        spiffe_id    = "spiffe://ifos.munich.internal/edge-gateways/${shop}/02"
      },
    ]
  ])

  # Keyed map for for_each usage
  gateways_map = {
    for gw in local.gateways :
    gw.node_id => gw
  }

  # MQTT broker URI consumed from uns-backbone output.
  # Hardcoded to match uns-backbone output "hivemq_mqtt_endpoint" (TLS 1.3, port 8883).
  mqtt_broker_uri = "mqtts://uns-hivemq.hivemq.svc.cluster.local:8883"

  # Kafka DDATA topics — one per shop (consumed, not produced, by this module for ACL grants).
  ddata_topics = {
    for shop in local.shops :
    shop => "bmw.munich.uns.ddata.${shop}.v1"
  }
}

# ---------------------------------------------------------------------------
# 1. SPIRE registration entries — one per gateway node
#    Each entry grants the gateway a SVID scoped to its shop and node role.
#    The SVID is rotated by the SPIRE agent without interrupting the MQTT stream.
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "spire_registration_entry" {
  for_each = local.gateways_map

  yaml_body = <<-YAML
    apiVersion: spire.spiffe.io/v1alpha1
    kind: ClusterSPIFFEID
    metadata:
      name: edge-gw-${each.key}
      namespace: spire
    spec:
      spiffeIDTemplate: "spiffe://ifos.munich.internal/edge-gateways/${each.value.shop}/${each.value.node_index}"
      podSelector:
        matchLabels:
          app: simatic-edge-runtime
          shop: ${each.value.shop}
          node-id: ${each.value.node_id}
      ttl: 1h
      dnsNameTemplates:
        - "${each.value.node_id}.edge-gateways.ifos.munich.internal"
  YAML
}

# ---------------------------------------------------------------------------
# 2. Kafka write ACLs — each gateway principal writes only to its shop DDATA topic.
#    Principal CN matches SPIFFE SVID subject CN injected by SPIRE agent.
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "kafka_acl_edge_gw_write" {
  for_each = local.gateways_map

  yaml_body = <<-YAML
    apiVersion: platform.confluent.io/v1beta1
    kind: KafkaACL
    metadata:
      name: ${each.key}-write-ddata
      namespace: confluent
    spec:
      kafkaClusterRef:
        name: uns-kafka
      resourceType: Topic
      resourceName: ${local.ddata_topics[each.value.shop]}
      patternType: Literal
      principal: "User:${each.value.node_id}"
      operation: Write
      permission: Allow
  YAML
}

# Describe ACL — required for health-check topic metadata reads
resource "kubectl_manifest" "kafka_acl_edge_gw_describe" {
  for_each = local.gateways_map

  yaml_body = <<-YAML
    apiVersion: platform.confluent.io/v1beta1
    kind: KafkaACL
    metadata:
      name: ${each.key}-describe-ddata
      namespace: confluent
    spec:
      kafkaClusterRef:
        name: uns-kafka
      resourceType: Topic
      resourceName: ${local.ddata_topics[each.value.shop]}
      patternType: Literal
      principal: "User:${each.value.node_id}"
      operation: Describe
      permission: Allow
  YAML
}

# ---------------------------------------------------------------------------
# 3. Ansible inventory ConfigMap — consumed by the Ansible runner job.
#    The actual gateway OS/application configuration is in ansible/.
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "edge_gw_ansible_inventory" {
  yaml_body = <<-YAML
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: edge-gw-ansible-inventory
      namespace: edge-gateways
      labels:
        app: simatic-edge-runtime
        managed-by: terraform
    data:
      inventory.yaml: |
        all:
          children:
            inbound:
              hosts:
                edge-inbound-01:
                  ansible_host: 10.200.10.11
                  gateway_role: primary
                edge-inbound-02:
                  ansible_host: 10.200.10.12
                  gateway_role: standby
            body:
              hosts:
                edge-body-01:
                  ansible_host: 10.200.20.11
                  gateway_role: primary
                edge-body-02:
                  ansible_host: 10.200.20.12
                  gateway_role: standby
            paint:
              hosts:
                edge-paint-01:
                  ansible_host: 10.200.30.11
                  gateway_role: primary
                edge-paint-02:
                  ansible_host: 10.200.30.12
                  gateway_role: standby
            assembly:
              hosts:
                edge-assembly-01:
                  ansible_host: 10.200.40.11
                  gateway_role: primary
                edge-assembly-02:
                  ansible_host: 10.200.40.12
                  gateway_role: standby
            quality:
              hosts:
                edge-quality-01:
                  ansible_host: 10.200.50.11
                  gateway_role: primary
                edge-quality-02:
                  ansible_host: 10.200.50.12
                  gateway_role: standby
          vars:
            ansible_user: ifos-ansible
            ansible_ssh_private_key_file: /run/secrets/ansible-ssh-key
            ansible_become: true
  YAML
}

# ---------------------------------------------------------------------------
# 4. Gateway runtime namespace
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "edge_gw_namespace" {
  yaml_body = <<-YAML
    apiVersion: v1
    kind: Namespace
    metadata:
      name: edge-gateways
      labels:
        data_residency: "eu-de-west-central"
        classification: "internal"
        iec62443-sl: "SL2"
  YAML
}

# ---------------------------------------------------------------------------
# 5. Outputs — consumed by tests and observability-stack
# ---------------------------------------------------------------------------

output "mqtt_broker_uri" {
  description = "HiveMQ MQTT broker URI used by all edge gateways (TLS 1.3, mTLS SVID auth)"
  value       = local.mqtt_broker_uri
}

output "gateway_node_ids" {
  description = "List of all 10 gateway node IDs"
  value       = keys(local.gateways_map)
}

output "ddata_topics" {
  description = "Map of shop → Kafka DDATA topic"
  value       = local.ddata_topics
}
