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
        # Trust domain is munich.bmw — consumed from spiffe-spire module (not introduced here)
        spiffe_id    = "spiffe://munich.bmw/edge-gateways/${shop}/01"
      },
      {
        shop         = shop
        node_id      = "edge-${shop}-02"
        node_index   = "02"
        role         = "standby"
        spiffe_id    = "spiffe://munich.bmw/edge-gateways/${shop}/02"
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

  # OT VLAN CIDRs — one /24 per shop, matching the Ansible inventory host addresses.
  # Trust domain and CIDRs are consumed from spiffe-spire decisions; not introduced here.
  ot_vlan_cidrs = {
    inbound  = "10.200.10.0/24"
    body     = "10.200.20.0/24"
    paint    = "10.200.30.0/24"
    assembly = "10.200.40.0/24"
    quality  = "10.200.50.0/24"
  }

  # IT DMZ — fixed service IPs for MQTT broker and SPIRE Workload API
  mqtt_broker_ip    = "10.100.1.10"  # uns-hivemq.hivemq.svc.cluster.local
  spire_workload_ip = "10.100.1.20"  # spire-workload-api.spire.svc.cluster.local
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
      spiffeIDTemplate: "spiffe://munich.bmw/edge-gateways/${each.value.shop}/${each.value.node_index}"
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
# 4. VLAN segmentation — each gateway sits in the OT VLAN (10.200.x.0/24),
#    isolated from the IT DMZ except TCP/8883 (MQTT) and TCP/8081 (SPIRE Workload API).
#    Modelled as Kubernetes NetworkPolicy; physical VLAN enforcement is via the
#    Cisco/Palo Alto firewall rule CM-013 declared in the firewall_rule resource below.
# ---------------------------------------------------------------------------

# NetworkPolicy — per-shop OT VLAN isolation.
# Allows only TCP/8883 (MQTT/TLS) to the MQTT broker and TCP/8081 (gRPC)
# to the SPIRE Workload API; all other egress from OT pods is denied.
resource "kubectl_manifest" "ot_vlan_network_policy" {
  for_each = local.ot_vlan_cidrs

  yaml_body = <<-YAML
    apiVersion: networking.k8s.io/v1
    kind: NetworkPolicy
    metadata:
      name: ot-vlan-egress-${each.key}
      namespace: edge-gateways
      labels:
        shop: ${each.key}
        zone: ot
        iec62443-sl: "SL2"
        app.kubernetes.io/managed-by: terraform
      annotations:
        firewall-rule: "CM-013"
        description: "OT Zone VLAN isolation — allows only TCP/8883 to MQTT broker and TCP/8081 to SPIRE Workload API per IEC 62443 SL2"
    spec:
      podSelector:
        matchLabels:
          shop: ${each.key}
          zone: ot
      policyTypes:
        - Egress
      egress:
        # CM-013: OT Edge Gateway → MQTT Broker (Plant IT DMZ), TCP/8883, ALLOW
        - ports:
            - protocol: TCP
              port: 8883
          to:
            - ipBlock:
                cidr: ${local.mqtt_broker_ip}/32
        # FW-IN-005: OT Edge Gateway → SPIRE Workload API, TCP/8081, ALLOW
        - ports:
            - protocol: TCP
              port: 8081
          to:
            - ipBlock:
                cidr: ${local.spire_workload_ip}/32
  YAML
}

# Firewall rule CM-013 as code — modelled as a ConfigMap annotation artifact.
# Physical enforcement is applied by the Palo Alto/Cisco firewall via GitOps pipeline.
# This resource records the rule definition so it is version-controlled and auditable.
resource "kubectl_manifest" "firewall_rule_cm013" {
  yaml_body = <<-YAML
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: firewall-rule-cm013
      namespace: edge-gateways
      labels:
        firewall-rule-id: "CM-013"
        zone-from: ot
        zone-to: it-dmz
        app.kubernetes.io/managed-by: terraform
        iec62443-sl: "SL2"
      annotations:
        description: "IEC 62443 SL2 conduit CM-013: OT Edge Gateways to MQTT Broker"
        iec62443-control: "SC-004,SC-017"
        audit-reference: "US-027,BR-10"
    data:
      rule-id: "CM-013"
      source-zone: "OT Zone VLAN"
      source-cidrs: "10.200.10.0/24,10.200.20.0/24,10.200.30.0/24,10.200.40.0/24,10.200.50.0/24"
      destination-zone: "Plant IT DMZ"
      destination-host: "uns-hivemq.hivemq.svc.cluster.local"
      destination-ip: "${local.mqtt_broker_ip}"
      protocol: "TCP"
      port: "8883"
      action: "ALLOW"
      authentication: "mTLS with SPIFFE SVID (SPIRE-issued X.509)"
      tls-version: "TLSv1.3"
      log-on-deny: "true"
      iec62443-sl: "SL2"
      sc-004: "Zone separation enforced: OT VLAN cannot reach IT DMZ except via this conduit"
      sc-017: "mTLS with SPIFFE/SPIRE SVID — zero static certificates; automated rotation"
  YAML
}

# ---------------------------------------------------------------------------
# 4c. SPIRE Workload API client configuration — Ansible ConfigMap for sidecar
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "spire_workload_client_config" {
  yaml_body = <<-YAML
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: spire-workload-client-config
      namespace: edge-gateways
      labels:
        app: simatic-edge-runtime
        zone: ot
        app.kubernetes.io/managed-by: terraform
      annotations:
        description: "SPIRE Workload API client config for OT edge gateways — consumed by Ansible svid-watcher.service"
    data:
      spire-agent-socket: "/run/spire/sockets/agent.sock"
      spire-server-address: "spire-workload-api.spire.svc.cluster.local:8081"
      trust-domain: "munich.bmw"
      svid-cert-path: "/run/spire/svid/svid.pem"
      svid-key-path: "/run/spire/svid/svid_key.pem"
      spire-bundle-path: "/run/spire/bundle/bundle.pem"
      svid-ttl-seconds: "3600"
      rotate-at-half-ttl: "true"
  YAML
}

# ---------------------------------------------------------------------------
# 5. Gateway runtime namespace
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
# 6. Outputs — consumed by tests and observability-stack
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
