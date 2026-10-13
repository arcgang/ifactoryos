use crate::config::Settings;
use rdkafka::config::ClientConfig;
use rdkafka::message::OwnedHeaders;
use rdkafka::producer::{FutureProducer, FutureRecord};
use serde::Serialize;
use std::time::Duration;
use tracing::{error, instrument};

pub const TOPIC_OEE_UPDATED: &str = "bmw.munich.opcenter.oee.updated";
pub const TOPIC_ANDON_ALERT: &str = "bmw.munich.opcenter.andon.alert";
pub const TOPIC_SEQUENCE_PUBLISHED: &str = "bmw.munich.opcenter.sequence.published";
pub const TOPIC_SEQUENCE_PUBLISH_FAILED: &str = "bmw.munich.opcenter.sequence.publish_failed";

/// Create the process-lifetime Kafka producer. Called once at startup.
pub fn create_producer(settings: &Settings) -> anyhow::Result<FutureProducer> {
    let producer: FutureProducer = ClientConfig::new()
        .set("bootstrap.servers", &settings.kafka_bootstrap_servers)
        .set("security.protocol", "SASL_SSL")
        .set("sasl.mechanism", "SCRAM-SHA-512")
        .set("sasl.username", &settings.kafka_sasl_username)
        .set("sasl.password", &settings.kafka_sasl_password)
        // Delivery guarantees: all in-sync replicas + idempotent producer
        .set("acks", "all")
        .set("enable.idempotence", "true")
        .set("retries", "5")
        .set("retry.backoff.ms", "200")
        .set("message.timeout.ms", "10000")
        .create()?;
    Ok(producer)
}

/// Publish a serialisable payload to `topic` and return the partition offset.
/// Explicit 10-second timeout on every produce call.
#[instrument(skip(producer, payload), fields(%topic))]
pub async fn publish<T: Serialize>(
    producer: &FutureProducer,
    topic: &str,
    key: &str,
    payload: &T,
    correlation_id: uuid::Uuid,
) -> anyhow::Result<i64> {
    let body = serde_json::to_vec(payload)?;
    let corr_str = correlation_id.to_string();

    let headers = OwnedHeaders::new()
        .add("correlation_id", corr_str.as_str())
        .add("content-type", "application/json");

    let record = FutureRecord::to(topic)
        .key(key)
        .payload(body.as_slice())
        .headers(headers);

    match producer.send(record, Duration::from_secs(10)).await {
        Ok((_partition, offset)) => {
            tracing::debug!(%topic, offset, "Kafka message delivered");
            Ok(offset)
        }
        Err((err, _)) => {
            error!(%topic, error = %err, "Kafka delivery failed");
            Err(anyhow::anyhow!("kafka delivery failed on {topic}: {err}"))
        }
    }
}
