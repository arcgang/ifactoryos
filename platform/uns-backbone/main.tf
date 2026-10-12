# uns-backbone — Confluent Kafka, Confluent Schema Registry (Avro), MQTT Broker (HiveMQ/EMQX), Dead-Letter Queue
# LLD component cmp:uns-backbone. Provision and configure it here; nothing in this module is applied
# until a pull request adds the provider resources and the plan is reviewed.

variable "environment" {
  type = string
}

# Topics the architecture's AsyncAPI contracts publish and subscribe to.
locals {
  topics = [
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
    "factory.munich.vin-thread.torque.feedback.v1"
  ]
}
