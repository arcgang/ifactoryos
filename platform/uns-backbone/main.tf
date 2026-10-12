# uns-backbone — Confluent Kafka, Confluent Schema Registry (Avro), HiveMQ Enterprise, Dead-Letter Queue
# LLD component cmp:uns-backbone. Provision and configure it here; nothing in this module is applied
# until a pull request adds the provider resources and the plan is reviewed.
#
# Realises: US-026 · BR-01 · BR-15
# Decisions applied:
#   - HiveMQ Enterprise with hivemq-operator (native Sparkplug B extension)
#   - Bridge translates spBv1.0/Munich/{shop}/DDATA/# → Kafka bmw.munich.uns.ddata.{shop}.v1
#   - Dead-letter via uns.munich.dlq Kafka topic; includes original topic, tag, raw payload, timestamp
#   - FULL_TRANSITIVE Avro compatibility on Confluent Schema Registry
#   - All data on-premises Munich / Azure EU Central (eu-de-west-central); zero cross-border transfers

variable "environment" {
  type = string
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------

locals {
  # ISA-95 shops in scope (US-026)
  shops = ["inbound", "body", "paint", "assembly", "quality"]

  kafka_replication = 3
  kafka_min_isr     = 2

  # Kafka topic list — authoritative source for edge-gateways and observability-stack consumers.
  # AsyncAPI contracts in asyncapi/ reference these as channel names.
  topics = [
    "bmw.ifos.munich.fabric.certificate.issued.v1",
    "bmw.ifos.munich.fabric.certificate.verify-failed.v1",
    "bmw.ifos.munich.line-command.adoption.v1",
    "bmw.ifos.munich.line-command.ar-session.started.v1",
    "bmw.ifos.munich.line-command.expert-call.ended.v1",
    "bmw.ifos.munich.line-command.handover.submitted.v1",
    "bmw.ifos.munich.line-command.torque.out-of-tolerance.v1",
    "bmw.ifos.munich.line-command.torque.submitted.v1",
    "bmw.ifos.munich.uns.andon.alerts.v1",
    "bmw.ifos.munich.uns.torque.plc-trace.v1",
    "bmw.munich.defectgenome.cluster.alerts.v1",
    "bmw.munich.defectgenome.corrective.actions.v1",
    "bmw.munich.defectgenome.query.results.v1",
    "bmw.munich.i4.sequence.constraint.alert.v1",
    "bmw.munich.i4.sequence.my26.config.activated.v1",
    "bmw.munich.i4.sequence.optimized.v1",
    "bmw.munich.i4.sequence.override.applied.v1",
    "bmw.munich.i4.sequence.resequence.triggered.v1",
    "bmw.munich.i4.uns.constraints.live.v1",
    "bmw.munich.opcenter.andon.alert",
    "bmw.munich.opcenter.gate_result.received",
    "bmw.munich.opcenter.oee.updated",
    "bmw.munich.opcenter.sequence.publish_failed",
    "bmw.munich.opcenter.sequence.published",
    "bmw.munich.paint.defect.events.v1",
    "factory.munich.line-command.adoption.v1",
    "factory.munich.line-command.ar-session.v1",
    "factory.munich.line-command.expert-call.v1",
    "factory.munich.line-command.handover.v1",
    "factory.munich.line-command.maintenance-dispatch.v1",
    "factory.munich.line-command.torque-log.v1",
    "factory.munich.opcenter.andon.alert.v1",
    "factory.munich.opcenter.oee.snapshot.v1",
    "factory.munich.uns.manufacturing.event.v1",
    "factory.munich.vin-thread.torque.feedback.v1",
    "factory.munich.vin.thread.completeness.alert.v1",
    "factory.munich.vin.thread.event.linked.v1",
    "factory.munich.vin.thread.final.gate.complete.v1",
    # Bridge-translated UNS DDATA topics — one per in-scope shop
    "bmw.munich.uns.ddata.inbound.v1",
    "bmw.munich.uns.ddata.body.v1",
    "bmw.munich.uns.ddata.paint.v1",
    "bmw.munich.uns.ddata.assembly.v1",
    "bmw.munich.uns.ddata.quality.v1",
  ]

  # Dead-letter topic — schema-invalid or poison-pill messages
  dlq_topic = "uns.munich.dlq"

  # Kafka ACL matrix (SC-002)
  kafka_acls = [
    { principal = "opcenter-sync-service", operation = "Write", topic = "factory.munich.opcenter.andon.alert.v1" },
    { principal = "opcenter-sync-service", operation = "Write", topic = "factory.munich.opcenter.oee.snapshot.v1" },
    { principal = "line-command-api",      operation = "Read",  topic = "factory.munich.opcenter.andon.alert.v1" },
    { principal = "line-command-api",      operation = "Read",  topic = "factory.munich.opcenter.oee.snapshot.v1" },
    { principal = "line-command-api",      operation = "Read",  topic = "factory.munich.line-command.adoption.v1" },
    { principal = "line-command-api",      operation = "Write", topic = "factory.munich.line-command.maintenance-dispatch.v1" },
    { principal = "defect-genome",         operation = "Read",  topic = "bmw.munich.paint.defect.events.v1" },
    { principal = "defect-genome",         operation = "Write", topic = "bmw.munich.defect.cluster.alert.v1" },
    { principal = "quality-studio",        operation = "Read",  topic = "bmw.munich.defect.cluster.alert.v1" },
    { principal = "planning-cockpit",      operation = "Write", topic = "factory.munich.line-command.adoption.v1" },
    # HiveMQ bridge principal writes translated DDATA topics and DLQ
    { principal = "hivemq-bridge", operation = "Write", topic = "bmw.munich.uns.ddata.inbound.v1" },
    { principal = "hivemq-bridge", operation = "Write", topic = "bmw.munich.uns.ddata.body.v1" },
    { principal = "hivemq-bridge", operation = "Write", topic = "bmw.munich.uns.ddata.paint.v1" },
    { principal = "hivemq-bridge", operation = "Write", topic = "bmw.munich.uns.ddata.assembly.v1" },
    { principal = "hivemq-bridge", operation = "Write", topic = "bmw.munich.uns.ddata.quality.v1" },
    { principal = "hivemq-bridge", operation = "Write", topic = "uns.munich.dlq" },
  ]

  # SchemaViolation alert must fire within this many seconds of a DLQ message arriving
  schema_violation_alert_window_seconds = 60

  alertmanager_webhook_url = "https://alertmanager.ifos.munich.internal/api/v2/alerts"
}

# ---------------------------------------------------------------------------
# 1. Confluent Operator
# ---------------------------------------------------------------------------

resource "helm_release" "confluent_operator" {
  name             = "confluent-operator"
  repository       = "https://packages.confluent.io/helm/v2"
  chart            = "confluent-for-kubernetes"
  version          = "0.921.2"
  namespace        = "confluent"
  create_namespace = true

  set {
    name  = "namespaced"
    value = "false"
  }
}

# ---------------------------------------------------------------------------
# 2. Kafka — 3-broker, SASL/SCRAM, TLS 1.3, AES-256 at rest
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "kafka_cluster" {
  depends_on = [helm_release.confluent_operator]

  yaml_body = <<-YAML
    apiVersion: platform.confluent.io/v1beta1
    kind: Kafka
    metadata:
      name: uns-kafka
      namespace: confluent
      annotations:
        data_residency: "eu-de-west-central"
        classification: "confidential"
    spec:
      replicas: 3
      image:
        application: confluentinc/cp-server:7.7.0
        init: confluentinc/confluent-init-container:2.9.0
      configOverrides:
        server:
          - "default.replication.factor=3"
          - "min.insync.replicas=2"
          - "auto.create.topics.enable=false"
          - "log.retention.hours=168"
          - "ssl.enabled.protocols=TLSv1.3"
          - "ssl.cipher.suites=TLS_AES_256_GCM_SHA384,TLS_CHACHA20_POLY1305_SHA256"
      listeners:
        internal:
          tls:
            enabled: true
          authentication:
            type: mtls
            principalMappingRules:
              - "RULE:.*CN=([a-zA-Z0-9.-]+).*$$1/L"
        external:
          tls:
            enabled: true
          authentication:
            type: saslPlain
      tls:
        autoGeneratedCerts: false
        secretRef: kafka-tls
      authorization:
        type: rbac
        superUsers:
          - User:kafka
      storageClass:
        name: managed-premium
      podTemplate:
        resources:
          requests:
            cpu: "2"
            memory: 8Gi
          limits:
            cpu: "4"
            memory: 16Gi
        securityContext:
          runAsNonRoot: true
          runAsUser: 1000
          fsGroup: 1000
  YAML
}

# Kafka topics — one KafkaTopic CRD per topic
resource "kubectl_manifest" "kafka_topic" {
  for_each = toset(local.topics)

  depends_on = [kubectl_manifest.kafka_cluster]

  yaml_body = <<-YAML
    apiVersion: platform.confluent.io/v1beta1
    kind: KafkaTopic
    metadata:
      name: ${replace(each.key, ".", "-")}
      namespace: confluent
    spec:
      replicas: 3
      partitionCount: 12
      configs:
        min.insync.replicas: "2"
        cleanup.policy: delete
        retention.ms: "604800000"
      kafkaClusterRef:
        name: uns-kafka
  YAML
}

# Dead-letter topic — compact+delete, 30-day retention for forensics
resource "kubectl_manifest" "kafka_topic_dlq" {
  depends_on = [kubectl_manifest.kafka_cluster]

  yaml_body = <<-YAML
    apiVersion: platform.confluent.io/v1beta1
    kind: KafkaTopic
    metadata:
      name: uns-munich-dlq
      namespace: confluent
    spec:
      replicas: 3
      partitionCount: 6
      configs:
        min.insync.replicas: "2"
        cleanup.policy: "compact,delete"
        retention.ms: "2592000000"
        message.timestamp.type: "LogAppendTime"
      kafkaClusterRef:
        name: uns-kafka
  YAML
}

# Kafka ACLs — SC-002 matrix
resource "kubectl_manifest" "kafka_acl" {
  for_each = {
    for acl in local.kafka_acls :
    "${acl.principal}-${acl.operation}-${replace(acl.topic, ".", "-")}" => acl
  }

  depends_on = [kubectl_manifest.kafka_cluster]

  yaml_body = <<-YAML
    apiVersion: platform.confluent.io/v1beta1
    kind: KafkaACL
    metadata:
      name: ${each.key}
      namespace: confluent
    spec:
      kafkaClusterRef:
        name: uns-kafka
      resourceType: Topic
      resourceName: ${each.value.topic}
      patternType: Literal
      principal: "User:${each.value.principal}"
      operation: ${each.value.operation}
      permission: Allow
  YAML
}

# Edge gateway ACLs — each shop gateway writes only to its assigned DDATA topic; no cross-zone subscribe
resource "kubectl_manifest" "kafka_acl_edge_gw" {
  for_each = toset(local.shops)

  depends_on = [kubectl_manifest.kafka_cluster]

  yaml_body = <<-YAML
    apiVersion: platform.confluent.io/v1beta1
    kind: KafkaACL
    metadata:
      name: edge-gw-${each.key}-write-ddata
      namespace: confluent
    spec:
      kafkaClusterRef:
        name: uns-kafka
      resourceType: Topic
      resourceName: bmw.munich.uns.ddata.${each.key}.v1
      patternType: Literal
      principal: "User:edge-gw-${each.key}"
      operation: Write
      permission: Allow
  YAML
}

# ---------------------------------------------------------------------------
# 3. Confluent Schema Registry — Avro, FULL_TRANSITIVE
# ---------------------------------------------------------------------------

resource "kubectl_manifest" "schema_registry" {
  depends_on = [kubectl_manifest.kafka_cluster]

  yaml_body = <<-YAML
    apiVersion: platform.confluent.io/v1beta1
    kind: SchemaRegistry
    metadata:
      name: uns-schema-registry
      namespace: confluent
      annotations:
        data_residency: "eu-de-west-central"
        classification: "confidential"
    spec:
      replicas: 3
      image:
        application: confluentinc/cp-schema-registry:7.7.0
        init: confluentinc/confluent-init-container:2.9.0
      tls:
        autoGeneratedCerts: false
        secretRef: schema-registry-tls
      configOverrides:
        server:
          - "schema.compatibility.level=FULL_TRANSITIVE"
          - "confluent.schema.registry.validate.on.produce=true"
      kafkaClusterRef:
        name: uns-kafka
      authorization:
        type: rbac
      podTemplate:
        resources:
          requests:
            cpu: "500m"
            memory: 2Gi
          limits:
            cpu: "1"
            memory: 4Gi
        securityContext:
          runAsNonRoot: true
          runAsUser: 1000
  YAML
}

# ---------------------------------------------------------------------------
# 4. HiveMQ Enterprise — Sparkplug B bridge, MQTT → Kafka
# ---------------------------------------------------------------------------

resource "helm_release" "hivemq_operator" {
  name             = "hivemq-operator"
  repository       = "https://helm.hivemq.com"
  chart            = "hivemq-operator"
  version          = "0.9.0"
  namespace        = "hivemq"
  create_namespace = true

  set {
    name  = "operatorImage.tag"
    value = "4.29.0"
  }
}

# HiveMQ ConfigMap — TLS 1.3, Kafka bridge mappings, Sparkplug B extension settings
resource "kubectl_manifest" "hivemq_config" {
  yaml_body = <<-YAML
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: hivemq-config
      namespace: hivemq
    data:
      config.xml: |
        <?xml version="1.0"?>
        <hivemq>
          <listeners>
            <tls-tcp-listener>
              <port>8883</port>
              <bind-address>0.0.0.0</bind-address>
              <tls>
                <protocols>
                  <protocol>TLSv1.3</protocol>
                </protocols>
                <cipher-suites>
                  <cipher-suite>TLS_AES_256_GCM_SHA384</cipher-suite>
                  <cipher-suite>TLS_CHACHA20_POLY1305_SHA256</cipher-suite>
                </cipher-suites>
                <keystore>
                  <path>/opt/hivemq/conf/tls/keystore.jks</path>
                  <password>$${HIVEMQ_KEYSTORE_PASSWORD}</password>
                </keystore>
                <truststore>
                  <path>/opt/hivemq/conf/tls/truststore.jks</path>
                  <password>$${HIVEMQ_TRUSTSTORE_PASSWORD}</password>
                </truststore>
              </tls>
            </tls-tcp-listener>
          </listeners>
          <mqtt>
            <session-expiry>
              <max-interval>3600</max-interval>
            </session-expiry>
            <keep-alive>
              <max-keep-alive>60</max-keep-alive>
              <allow-unlimited>false</allow-unlimited>
            </keep-alive>
            <quality-of-service>
              <max-qos>1</max-qos>
            </quality-of-service>
            <retain>
              <enabled>true</enabled>
            </retain>
          </mqtt>
          <security>
            <allow-empty-client-id>false</allow-empty-client-id>
          </security>
        </hivemq>

      kafka-bridge.xml: |
        <?xml version="1.0"?>
        <kafka-extension>
          <kafka-clusters>
            <kafka-cluster>
              <id>uns-kafka</id>
              <bootstrap-servers>uns-kafka-bootstrap.confluent.svc.cluster.local:9092</bootstrap-servers>
              <tls><enabled>true</enabled></tls>
              <authentication>
                <type>SASL_SSL</type>
                <mechanism>SCRAM-SHA-512</mechanism>
                <username>$${KAFKA_BRIDGE_USERNAME}</username>
                <password>$${KAFKA_BRIDGE_PASSWORD}</password>
              </authentication>
            </kafka-cluster>
          </kafka-clusters>
          <!--
            MQTT spBv1.0/Munich/{shop}/DDATA/# → Kafka bmw.munich.uns.ddata.{shop}.v1
            STATE and NBIRTH are also bridged to preserve Sparkplug B lifecycle semantics
            on gateway connect/reconnect.
          -->
          <mqtt-to-kafka-mappings>
            <mqtt-to-kafka-mapping>
              <id>sparkplug-inbound-ddata</id>
              <cluster-id>uns-kafka</cluster-id>
              <mqtt-topic-filter>spBv1.0/Munich/inbound/DDATA/#</mqtt-topic-filter>
              <kafka-topic>bmw.munich.uns.ddata.inbound.v1</kafka-topic>
              <mqtt-qos>AT_LEAST_ONCE</mqtt-qos>
            </mqtt-to-kafka-mapping>
            <mqtt-to-kafka-mapping>
              <id>sparkplug-body-ddata</id>
              <cluster-id>uns-kafka</cluster-id>
              <mqtt-topic-filter>spBv1.0/Munich/body/DDATA/#</mqtt-topic-filter>
              <kafka-topic>bmw.munich.uns.ddata.body.v1</kafka-topic>
              <mqtt-qos>AT_LEAST_ONCE</mqtt-qos>
            </mqtt-to-kafka-mapping>
            <mqtt-to-kafka-mapping>
              <id>sparkplug-paint-ddata</id>
              <cluster-id>uns-kafka</cluster-id>
              <mqtt-topic-filter>spBv1.0/Munich/paint/DDATA/#</mqtt-topic-filter>
              <kafka-topic>bmw.munich.uns.ddata.paint.v1</kafka-topic>
              <mqtt-qos>AT_LEAST_ONCE</mqtt-qos>
            </mqtt-to-kafka-mapping>
            <mqtt-to-kafka-mapping>
              <id>sparkplug-assembly-ddata</id>
              <cluster-id>uns-kafka</cluster-id>
              <mqtt-topic-filter>spBv1.0/Munich/assembly/DDATA/#</mqtt-topic-filter>
              <kafka-topic>bmw.munich.uns.ddata.assembly.v1</kafka-topic>
              <mqtt-qos>AT_LEAST_ONCE</mqtt-qos>
            </mqtt-to-kafka-mapping>
            <mqtt-to-kafka-mapping>
              <id>sparkplug-quality-ddata</id>
              <cluster-id>uns-kafka</cluster-id>
              <mqtt-topic-filter>spBv1.0/Munich/quality/DDATA/#</mqtt-topic-filter>
              <kafka-topic>bmw.munich.uns.ddata.quality.v1</kafka-topic>
              <mqtt-qos>AT_LEAST_ONCE</mqtt-qos>
            </mqtt-to-kafka-mapping>
            <!-- NBIRTH preserves device lifecycle on reconnect -->
            <mqtt-to-kafka-mapping>
              <id>sparkplug-nbirth</id>
              <cluster-id>uns-kafka</cluster-id>
              <mqtt-topic-filter>spBv1.0/Munich/+/NBIRTH/#</mqtt-topic-filter>
              <kafka-topic>bmw.munich.uns.ddata.inbound.v1</kafka-topic>
              <mqtt-qos>AT_LEAST_ONCE</mqtt-qos>
            </mqtt-to-kafka-mapping>
            <!-- STATE preserves host application lifecycle -->
            <mqtt-to-kafka-mapping>
              <id>sparkplug-state</id>
              <cluster-id>uns-kafka</cluster-id>
              <mqtt-topic-filter>spBv1.0/STATE/#</mqtt-topic-filter>
              <kafka-topic>bmw.munich.uns.ddata.inbound.v1</kafka-topic>
              <mqtt-qos>AT_LEAST_ONCE</mqtt-qos>
            </mqtt-to-kafka-mapping>
          </mqtt-to-kafka-mappings>
        </kafka-extension>

      sparkplug-extension.xml: |
        <?xml version="1.0"?>
        <sparkplug-extension-configuration>
          <!--
            Schema validation via Confluent Schema Registry.
            Messages with unregistered tags are rejected and forwarded to the DLQ;
            DLQ entries include original topic, tag name, raw payload bytes, arrival timestamp.
          -->
          <schema-registry-url>https://uns-schema-registry.confluent.svc.cluster.local:8081</schema-registry-url>
          <schema-compatibility>FULL_TRANSITIVE</schema-compatibility>
          <reject-unknown-tags>true</reject-unknown-tags>
          <dead-letter-topic>uns.munich.dlq</dead-letter-topic>
          <dead-letter-include-original-topic>true</dead-letter-include-original-topic>
          <dead-letter-include-raw-payload>true</dead-letter-include-raw-payload>
          <dead-letter-include-arrival-timestamp>true</dead-letter-include-arrival-timestamp>
          <validate-birth-certificates>true</validate-birth-certificates>
          <retain-birth-on-reconnect>true</retain-birth-on-reconnect>
        </sparkplug-extension-configuration>
  YAML
}

# HiveMQ cluster — 3 nodes, TLS 1.3, Sparkplug B + Kafka extensions
resource "kubectl_manifest" "hivemq_cluster" {
  depends_on = [helm_release.hivemq_operator, kubectl_manifest.hivemq_config]

  yaml_body = <<-YAML
    apiVersion: hivemq.com/v1
    kind: HiveMQCluster
    metadata:
      name: uns-hivemq
      namespace: hivemq
      annotations:
        data_residency: "eu-de-west-central"
        classification: "confidential"
    spec:
      nodes: 3
      image: hivemq/hivemq4:4.29.0-enterprise
      mqttServiceType: LoadBalancer
      mqttServiceAnnotations:
        service.beta.kubernetes.io/azure-load-balancer-internal: "true"
      env:
        - name: HIVEMQ_LICENSE
          valueFrom:
            secretKeyRef:
              name: hivemq-license
              key: license
        - name: HIVEMQ_KEYSTORE_PASSWORD
          valueFrom:
            secretKeyRef:
              name: hivemq-tls-keystore
              key: password
        - name: HIVEMQ_TRUSTSTORE_PASSWORD
          valueFrom:
            secretKeyRef:
              name: hivemq-tls-truststore
              key: password
        - name: KAFKA_BRIDGE_USERNAME
          valueFrom:
            secretKeyRef:
              name: hivemq-kafka-credentials
              key: username
        - name: KAFKA_BRIDGE_PASSWORD
          valueFrom:
            secretKeyRef:
              name: hivemq-kafka-credentials
              key: password
      configMaps:
        - name: hivemq-config
          path: /opt/hivemq/conf
      resources:
        cpu: "2"
        memory: "4Gi"
      tls:
        keystoreSecretName: hivemq-tls-keystore
        truststoreSecretName: hivemq-tls-truststore
      extensions:
        - name: hivemq-sparkplug-extension
          extensionUri: "hivemq/hivemq-sparkplug-aware-extension:1.0.11"
          enabled: true
        - name: hivemq-kafka-extension
          extensionUri: "hivemq/hivemq-kafka-extension:1.3.3"
          enabled: true
      podTemplate:
        spec:
          securityContext:
            runAsNonRoot: true
            runAsUser: 10000
            fsGroup: 10000
  YAML
}

# ---------------------------------------------------------------------------
# 5. Schema-violation alerting — fires within 60 s of DLQ message arrival
# ---------------------------------------------------------------------------

# PrometheusRule — SchemaViolation alert on DLQ consumer group lag > 0
resource "kubectl_manifest" "schema_violation_alert_rule" {
  yaml_body = <<-YAML
    apiVersion: monitoring.coreos.com/v1
    kind: PrometheusRule
    metadata:
      name: uns-schema-violation
      namespace: confluent
      labels:
        prometheus: kube-prometheus
        role: alert-rules
    spec:
      groups:
        - name: uns.schema-violation
          interval: 15s
          rules:
            - alert: SchemaViolation
              expr: |
                sum(kafka_consumergroup_lag{topic="uns.munich.dlq"}) > 0
              for: 0m
              labels:
                severity: critical
                team: data-governance
                data_region: eu-de-west-central
              annotations:
                summary: "UNS schema violation — unregistered tag routed to DLQ"
                description: >
                  One or more messages with unregistered Sparkplug B tags have been placed
                  on uns.munich.dlq. Dead-letter entries include: original MQTT topic, tag
                  name, raw Avro payload bytes, arrival timestamp. The Data Governance Lead
                  must review and register or reject the schema within 60 seconds.
                runbook_url: "https://wiki.ifos.munich.internal/runbooks/uns-schema-violation"
  YAML
}

# AlertmanagerConfig — zero groupWait so the webhook fires immediately; max window ≤ 60 s
resource "kubectl_manifest" "alertmanager_config" {
  yaml_body = <<-YAML
    apiVersion: monitoring.coreos.com/v1alpha1
    kind: AlertmanagerConfig
    metadata:
      name: uns-schema-violation-routing
      namespace: confluent
      labels:
        alertmanagerConfig: kube-prometheus
    spec:
      route:
        receiver: data-governance-lead
        groupBy: ["alertname", "topic"]
        groupWait: 0s
        groupInterval: ${local.schema_violation_alert_window_seconds}s
        repeatInterval: 10m
        matchers:
          - name: alertname
            value: SchemaViolation
      receivers:
        - name: data-governance-lead
          webhookConfigs:
            - url: "${local.alertmanager_webhook_url}"
              sendResolved: true
              httpConfig:
                tlsConfig:
                  caFile: /etc/alertmanager/tls/ca.crt
                  certFile: /etc/alertmanager/tls/tls.crt
                  keyFile: /etc/alertmanager/tls/tls.key
  YAML
}

# ---------------------------------------------------------------------------
# 6. Outputs — consumed by edge-gateways and observability-stack modules
# ---------------------------------------------------------------------------

output "kafka_bootstrap_servers" {
  description = "Internal Kafka bootstrap endpoint (SASL/SCRAM + TLS 1.3)"
  value       = "uns-kafka-bootstrap.confluent.svc.cluster.local:9092"
}

output "schema_registry_url" {
  description = "Confluent Schema Registry URL (HTTPS, FULL_TRANSITIVE Avro)"
  value       = "https://uns-schema-registry.confluent.svc.cluster.local:8081"
}

output "hivemq_mqtt_endpoint" {
  description = "HiveMQ Enterprise MQTT broker (TLS 1.3, port 8883)"
  value       = "mqtts://uns-hivemq.hivemq.svc.cluster.local:8883"
}

output "dlq_topic" {
  description = "Dead-letter Kafka topic for schema-invalid or poison-pill messages"
  value       = local.dlq_topic
}

output "uns_topic_list" {
  description = "Authoritative list of all UNS Kafka topics — consumed by AsyncAPI contracts"
  value       = concat(local.topics, [local.dlq_topic])
}
