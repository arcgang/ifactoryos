# uns-backbone — Confluent Kafka, Confluent Schema Registry (Avro), MQTT Broker (HiveMQ/EMQX), Dead-Letter Queue
# LLD component cmp:uns-backbone. Provision and configure it here; nothing in this module is applied
# until a pull request adds the provider resources and the plan is reviewed.

variable "environment" {
  type = string
}

# Topics the architecture's AsyncAPI contracts publish and subscribe to.
locals {
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
    "factory.munich.vin.thread.final.gate.complete.v1"
  ]

  # Paths to AsyncAPI spec files relative to the repository root.
  # Each entry maps a logical name to the spec file; used by ibm_eem_topic_subscription.
  asyncapi_specs = {
    uns_andon_alerts          = "platform/uns-backbone/contracts/asyncapi/uns-andon-alerts-v1.yaml"
    uns_torque_plc_trace      = "platform/uns-backbone/contracts/asyncapi/uns-torque-plc-trace-v1.yaml"
    uns_oee_snapshot          = "platform/uns-backbone/contracts/asyncapi/uns-oee-snapshot-v1.yaml"
    uns_opcenter_events       = "platform/uns-backbone/contracts/asyncapi/uns-opcenter-events-v1.yaml"
    sequence_optimizer_events = "platform/uns-backbone/contracts/asyncapi/sequence-optimizer-events-v1.yaml"
    defect_genome_events      = "platform/uns-backbone/contracts/asyncapi/defect-genome-events-v1.yaml"
    line_command_events       = "platform/uns-backbone/contracts/asyncapi/line-command-events-v1.yaml"
    vin_thread_events         = "platform/uns-backbone/contracts/asyncapi/vin-thread-events-v1.yaml"
    fabric_certificate_events = "platform/uns-backbone/contracts/asyncapi/fabric-certificate-events-v1.yaml"
  }
}

# ---------------------------------------------------------------------------
# IBM Event Endpoint Management — developer portal
# Groups all AsyncAPI contracts under a single catalogue view accessible to
# internal Horizon 2 consumers. The asyncapi-generator download action is
# enabled so consumers can generate typed event-consumer stubs (Java, Python,
# Node.js) without contacting the Horizon 1 engineering team.
# ---------------------------------------------------------------------------
resource "ibm_eem_portal" "ifactoryos" {
  name        = "ifactoryos-${var.environment}"
  description = "iFactoryOS UNS Backbone — AsyncAPI 2.x event catalogue for Horizon 2 consumers"
  environment = var.environment

  # Enables the AsyncAPI Generator download action in the portal UI.
  # Horizon 2 consumers can select a target language and download a typed stub.
  asyncapi_generator_enabled = true

  # Target languages surfaced in the portal's stub-generation UI.
  asyncapi_generator_targets = [
    "nodejs",
    "java-spring-cloud-streams-template",
    "python-paho-mqtt",
  ]

  tags = {
    component      = "uns-backbone"
    data_residency = "eu-de-west-central"
    managed_by     = "terraform"
  }
}

# ---------------------------------------------------------------------------
# IBM Event Endpoint Management — one topic subscription per AsyncAPI spec file
# Each resource registers the spec path with IBM EEM so the portal surfaces
# the contract and its schema to Horizon 2 consumers.
# ---------------------------------------------------------------------------
resource "ibm_eem_topic_subscription" "uns_andon_alerts" {
  name          = "uns-andon-alerts-v1-${var.environment}"
  portal_id     = ibm_eem_portal.ifactoryos.id
  asyncapi_path = local.asyncapi_specs.uns_andon_alerts
  environment   = var.environment

  tags = {
    domain         = "uns"
    data_residency = "eu-de-west-central"
    managed_by     = "terraform"
  }
}

resource "ibm_eem_topic_subscription" "uns_torque_plc_trace" {
  name          = "uns-torque-plc-trace-v1-${var.environment}"
  portal_id     = ibm_eem_portal.ifactoryos.id
  asyncapi_path = local.asyncapi_specs.uns_torque_plc_trace
  environment   = var.environment

  tags = {
    domain         = "uns"
    data_residency = "eu-de-west-central"
    managed_by     = "terraform"
  }
}

resource "ibm_eem_topic_subscription" "uns_oee_snapshot" {
  name          = "uns-oee-snapshot-v1-${var.environment}"
  portal_id     = ibm_eem_portal.ifactoryos.id
  asyncapi_path = local.asyncapi_specs.uns_oee_snapshot
  environment   = var.environment

  tags = {
    domain         = "uns"
    data_residency = "eu-de-west-central"
    managed_by     = "terraform"
  }
}

resource "ibm_eem_topic_subscription" "uns_opcenter_events" {
  name          = "uns-opcenter-events-v1-${var.environment}"
  portal_id     = ibm_eem_portal.ifactoryos.id
  asyncapi_path = local.asyncapi_specs.uns_opcenter_events
  environment   = var.environment

  tags = {
    domain         = "opcenter"
    data_residency = "eu-de-west-central"
    managed_by     = "terraform"
  }
}

resource "ibm_eem_topic_subscription" "sequence_optimizer_events" {
  name          = "sequence-optimizer-events-v1-${var.environment}"
  portal_id     = ibm_eem_portal.ifactoryos.id
  asyncapi_path = local.asyncapi_specs.sequence_optimizer_events
  environment   = var.environment

  tags = {
    domain         = "sequence-optimizer"
    data_residency = "eu-de-west-central"
    managed_by     = "terraform"
  }
}

resource "ibm_eem_topic_subscription" "defect_genome_events" {
  name          = "defect-genome-events-v1-${var.environment}"
  portal_id     = ibm_eem_portal.ifactoryos.id
  asyncapi_path = local.asyncapi_specs.defect_genome_events
  environment   = var.environment

  tags = {
    domain         = "defect-genome"
    data_residency = "eu-de-west-central"
    managed_by     = "terraform"
  }
}

resource "ibm_eem_topic_subscription" "line_command_events" {
  name          = "line-command-events-v1-${var.environment}"
  portal_id     = ibm_eem_portal.ifactoryos.id
  asyncapi_path = local.asyncapi_specs.line_command_events
  environment   = var.environment

  tags = {
    domain         = "line-command"
    data_residency = "eu-de-west-central"
    managed_by     = "terraform"
  }
}

resource "ibm_eem_topic_subscription" "vin_thread_events" {
  name          = "vin-thread-events-v1-${var.environment}"
  portal_id     = ibm_eem_portal.ifactoryos.id
  asyncapi_path = local.asyncapi_specs.vin_thread_events
  environment   = var.environment

  tags = {
    domain         = "vin-digital-thread"
    data_residency = "eu-de-west-central"
    managed_by     = "terraform"
  }
}

resource "ibm_eem_topic_subscription" "fabric_certificate_events" {
  name          = "fabric-certificate-events-v1-${var.environment}"
  portal_id     = ibm_eem_portal.ifactoryos.id
  asyncapi_path = local.asyncapi_specs.fabric_certificate_events
  environment   = var.environment

  tags = {
    domain         = "hyperledger-fabric"
    data_residency = "eu-de-west-central"
    managed_by     = "terraform"
  }
}
