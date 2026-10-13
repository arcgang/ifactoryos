/// Background retry worker for queued VIN sequence publish jobs.
/// Every `retry_interval` seconds, picks up jobs with next_retry_at <= now,
/// sets status=retrying, calls OpCenter, and transitions to published or re-queues.
use std::sync::Arc;
use std::time::Duration;

use chrono::Utc;
use deadpool_postgres::Pool;
use rdkafka::producer::FutureProducer;
use tracing::{error, info, warn};
use uuid::Uuid;

use crate::{
    config::Settings,
    db::{claim_retry_jobs, mark_sync_job_published, mark_sync_job_queued},
    kafka::{publish, TOPIC_SEQUENCE_PUBLISHED, TOPIC_SEQUENCE_PUBLISH_FAILED},
    models::{SequencePublishedEvent, SequencePublishFailedEvent},
};

const RETRY_BATCH_SIZE: i64 = 50;

pub fn spawn_retry_worker(settings: Arc<Settings>, pool: Pool, producer: Arc<FutureProducer>) {
    tokio::spawn(async move {
        let interval = settings.retry_interval();
        loop {
            tokio::time::sleep(interval).await;
            if let Err(e) = run_retry_batch(&settings, &pool, &producer).await {
                error!(error = %e, "retry worker batch failed");
            }
        }
    });
}

async fn run_retry_batch(
    settings: &Settings,
    pool: &Pool,
    producer: &FutureProducer,
) -> anyhow::Result<()> {
    let client = pool.get().await?;
    let now = Utc::now();

    let jobs = claim_retry_jobs(&client, now, RETRY_BATCH_SIZE).await?;

    if jobs.is_empty() {
        return Ok(());
    }

    info!("retry worker: processing {} eligible jobs", jobs.len());

    let http = reqwest::Client::builder()
        .timeout(Duration::from_secs(settings.http_timeout_secs))
        .build()?;

    for row in &jobs {
        let sync_job_id: Uuid = row.get("sync_job_id");
        let sequence_plan_id: Uuid = row.get("sequence_plan_id");
        let attempt_count: i32 = row.get("attempt_count");
        let correlation_id = Uuid::new_v4();

        info!(%sync_job_id, %sequence_plan_id, attempt_count, "retrying sequence publish");

        let url = format!("{}/opcenter/sequence/{}", settings.opcenter_rest_url, sequence_plan_id);

        let result = http
            .post(&url)
            .header("X-API-Key", &settings.planning_cockpit_api_key)
            .header("X-Correlation-Id", correlation_id.to_string())
            .header("X-Sync-Job-Id", sync_job_id.to_string())
            .send()
            .await;

        match result {
            Ok(resp) if resp.status().is_success() => {
                let body: serde_json::Value = resp
                    .json()
                    .await
                    .unwrap_or_else(|_| serde_json::json!({}));
                let fallback_id = sequence_plan_id.to_string();
                let opcenter_sequence_id = body["sequenceId"]
                    .as_str()
                    .unwrap_or(&fallback_id)
                    .to_string();
                let published_at = Utc::now();

                mark_sync_job_published(
                    &client,
                    sync_job_id,
                    &opcenter_sequence_id,
                    None,
                    published_at,
                )
                .await
                .unwrap_or_else(|e| warn!(error = %e, "mark_sync_job_published in retry failed"));

                let event = SequencePublishedEvent {
                    event_id: Uuid::new_v4(),
                    event_type: "opcenter.sequence.published".to_string(),
                    sync_job_id,
                    sequence_plan_id,
                    opcenter_sequence_id: opcenter_sequence_id.clone(),
                    source: "planning-cockpit".to_string(),
                    published_at: published_at.to_rfc3339(),
                    correlation_id,
                };

                if let Err(e) = publish(
                    producer,
                    TOPIC_SEQUENCE_PUBLISHED,
                    &sync_job_id.to_string(),
                    &event,
                    correlation_id,
                )
                .await
                {
                    warn!(error = %e, "retry worker: sequence.published Kafka publish failed");
                }

                info!(%sync_job_id, %opcenter_sequence_id, "retry succeeded — published");
            }

            Ok(resp) => {
                // OpCenter still unavailable or returned non-success status.
                let status = resp.status();
                let text = resp.text().await.unwrap_or_default();
                let msg = format!("HTTP {status}: {text}");
                let next_retry_at =
                    Utc::now() + chrono::Duration::seconds(settings.retry_interval_secs as i64);

                warn!(
                    %sync_job_id,
                    attempt_count,
                    error = %msg,
                    "retry failed — rescheduling"
                );

                mark_sync_job_queued(&client, sync_job_id, next_retry_at, Some(&msg))
                    .await
                    .unwrap_or_else(|e| warn!(error = %e, "mark_sync_job_queued in retry failed"));

                let event = SequencePublishFailedEvent {
                    event_id: Uuid::new_v4(),
                    event_type: "opcenter.sequence.publish_failed".to_string(),
                    sync_job_id,
                    sequence_plan_id,
                    status: "queued".to_string(),
                    opcenter_error: Some(msg),
                    retry_scheduled_at: Some(next_retry_at.to_rfc3339()),
                    correlation_id,
                };

                if let Err(e) = publish(
                    producer,
                    TOPIC_SEQUENCE_PUBLISH_FAILED,
                    &sync_job_id.to_string(),
                    &event,
                    correlation_id,
                )
                .await
                {
                    warn!(error = %e, "retry worker: publish_failed Kafka event failed");
                }
            }

            Err(e) => {
                let msg = e.to_string();
                let next_retry_at =
                    Utc::now() + chrono::Duration::seconds(settings.retry_interval_secs as i64);

                warn!(%sync_job_id, error = %msg, "retry: connection error — rescheduling");

                mark_sync_job_queued(&client, sync_job_id, next_retry_at, Some(&msg))
                    .await
                    .unwrap_or_else(|e| warn!(error = %e, "mark_sync_job_queued failed"));
            }
        }
    }

    Ok(())
}
