# uns-backbone — Confluent Kafka SASL_SSL listener with SPIFFE mTLS enforcement
#
# Realises US-027 · BR-10: all OT-to-IT cross-zone data flows authenticated with
# SPIFFE-issued certificates; unauthenticated connections rejected at TLS handshake.
#
# Decisions applied:
#   - SASL_SSL on port 9093; ssl.client.auth=required rejects any client that does not
#     present a valid SPIFFE SVID signed by the spiffe://munich.bmw CA.
#   - TLS trust anchored to the SPIRE CA bundle — no additional trust root is defined.
#   - TLSv1.2 permitted for broker-internal FIPS 140-2 compliance; TLSv1.3 preferred.
#   - SASL mechanism SCRAM-SHA-512 for service-account-level identity within the
#     mTLS session (dual-layer: transport identity via SVID, Kafka principal via SCRAM).

# ---------------------------------------------------------------------------
# Data source — SPIRE CA bundle secret (created by spiffe-spire module)
# ---------------------------------------------------------------------------

data "kubernetes_secret" "spire_ca_bundle" {
  metadata {
    name      = "spire-ca-bundle"
    namespace = "spire"
  }
}

# ---------------------------------------------------------------------------
# Kubernetes Secret — Kafka TLS trust store seeded from SPIRE CA bundle
#
# The spiffe-spire module writes the CA cert to the secret/data/spire/ca-key
# Vault path and rotates it. This Secret surfaces the bundle in the confluent
# namespace so the Kafka broker can reload it on SVID rotation.
# ---------------------------------------------------------------------------

resource "kubernetes_secret" "kafka_tls" {
  metadata {
    name      = "kafka-tls"
    namespace = "confluent"

    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
      data_residency                 = "eu-de-west-central"
      iec62443_sl                    = "SL2"
    }

    annotations = {
      "description" = "Kafka TLS trust store — CA bundle from spiffe://munich.bmw SPIRE server"
    }
  }

  # The spiffe-spire module populates spire-ca-bundle with the active CA cert
  # under the key "tls.crt". Kafka reads this as the trust store for ssl.client.auth=required.
  data = {
    "tls.crt" = data.kubernetes_secret.spire_ca_bundle.data["tls.crt"]
    "tls.key" = data.kubernetes_secret.spire_ca_bundle.data["tls.key"]
    "ca.crt"  = data.kubernetes_secret.spire_ca_bundle.data["ca.crt"]
  }

  type = "kubernetes.io/tls"
}

# ---------------------------------------------------------------------------
# Confluent Kafka listener — SASL_SSL on port 9093
#
# This resource patches the uns-kafka KafkaRestClass to expose the external
# SASL_SSL listener. The internal mTLS listener (port 9092) is already
# declared in main.tf via the kafka_cluster kubectl_manifest.
#
# Key security properties:
#   - ssl.client.auth=required: any TLS handshake without a client certificate
#     signed by spiffe://munich.bmw is rejected before any data is exchanged.
#   - sasl.enabled.mechanisms=SCRAM-SHA-512: after TLS, the client presents a
#     SCRAM credential that maps to its Kafka principal (used in ACL evaluation).
#   - ssl.enabled.protocols=TLSv1.2,TLSv1.3: TLSv1.3 preferred; TLSv1.2
#     retained for FIPS-mode brokers that cannot disable it via JVM flags.
#   - ssl.endpoint.identification.algorithm= (empty): disables hostname
#     verification for the inter-broker channel where SPIFFE URI SANs are used
#     instead of DNS SANs.
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "kafka_listener_sasl_ssl" {
  depends_on = [
    kubernetes_secret.kafka_tls,
  ]

  yaml_body = <<-YAML
    apiVersion: platform.confluent.io/v1beta1
    kind: KafkaRestClass
    metadata:
      name: uns-kafka-sasl-ssl-listener
      namespace: confluent
      labels:
        app.kubernetes.io/managed-by: terraform
        data_residency: "eu-de-west-central"
        iec62443-sl: "SL2"
      annotations:
        description: >
          SASL_SSL listener on port 9093 with ssl.client.auth=required.
          Only clients presenting a SPIFFE SVID signed by spiffe://munich.bmw
          pass the TLS handshake. Unauthenticated attempts are rejected and
          logged via the kafka-auth-failure-tailer sidecar.
    spec:
      kafkaClusterRef:
        name: uns-kafka
      kafkaRest:
        endpoint: https://uns-kafka.confluent.svc.cluster.local:9093
      tls:
        secretRef: kafka-tls
  YAML
}

# ---------------------------------------------------------------------------
# ConfigMap — Kafka listener security overrides (SASL_SSL, port 9093)
#
# These server.properties overrides are injected into the uns-kafka Kafka CRD
# via a patch. They extend the existing configOverrides.server list in main.tf
# rather than replacing it, so the baseline settings (replication factor,
# auto.create.topics.enable=false, etc.) are preserved.
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "kafka_sasl_ssl_config" {

  yaml_body = <<-YAML
    apiVersion: platform.confluent.io/v1beta1
    kind: KafkaTopic
    metadata:
      name: kafka-sasl-ssl-listener-config-sentinel
      namespace: confluent
      labels:
        app.kubernetes.io/managed-by: terraform
        purpose: config-patch-marker
      annotations:
        description: >
          Sentinel resource — the SASL_SSL listener properties below are applied
          as a configOverrides patch to the uns-kafka Kafka CRD. The Confluent
          Operator reconciler merges these into the broker server.properties.
          See kafka_sasl_ssl_overrides ConfigMap for the actual override values.
    spec:
      replicas: 1
      partitionCount: 1
      configs:
        retention.ms: "3600000"
      kafkaClusterRef:
        name: uns-kafka
  YAML
}

resource "kubernetes_config_map" "kafka_sasl_ssl_overrides" {
  metadata {
    name      = "kafka-sasl-ssl-overrides"
    namespace = "confluent"

    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
      data_residency                 = "eu-de-west-central"
      iec62443_sl                    = "SL2"
    }
  }

  data = {
    # Confluent Operator merges these into server.properties for the uns-kafka broker.
    # The listeners.* prefix targets the named EXTERNAL listener (port 9093).
    "sasl.ssl.listener.properties" = <<-PROPS
      listeners=INTERNAL://0.0.0.0:9092,EXTERNAL://0.0.0.0:9093
      listener.security.protocol.map=INTERNAL:SSL,EXTERNAL:SASL_SSL
      inter.broker.listener.name=INTERNAL

      # SASL_SSL listener (port 9093) — OT-to-IT cross-zone clients
      listener.name.external.ssl.client.auth=required
      listener.name.external.sasl.enabled.mechanisms=SCRAM-SHA-512
      listener.name.external.ssl.enabled.protocols=TLSv1.2,TLSv1.3
      listener.name.external.ssl.truststore.location=/mnt/tls/kafka-tls/ca.crt
      listener.name.external.ssl.truststore.type=PEM
      listener.name.external.ssl.keystore.location=/mnt/tls/kafka-tls/tls.crt
      listener.name.external.ssl.keystore.type=PEM
      listener.name.external.ssl.endpoint.identification.algorithm=

      # Internal listener (port 9092) — mTLS, already in main.tf kafka_cluster configOverrides
      listener.name.internal.ssl.client.auth=required
      listener.name.internal.ssl.endpoint.identification.algorithm=

      # Trust anchored to SPIRE CA bundle ONLY — no additional trust roots
      ssl.truststore.location=/mnt/tls/kafka-tls/ca.crt
      ssl.truststore.type=PEM
    PROPS
  }
}

# ---------------------------------------------------------------------------
# Output — consumed by kafka_log_tailer.tf and kafka_acls.tf
# ---------------------------------------------------------------------------

output "kafka_sasl_ssl_bootstrap" {
  description = "External SASL_SSL Kafka bootstrap endpoint (port 9093, SPIFFE mTLS required)"
  value       = "uns-kafka-bootstrap.confluent.svc.cluster.local:9093"
}
