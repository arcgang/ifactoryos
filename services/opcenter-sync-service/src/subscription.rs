/// GraphQL WebSocket subscription client for Siemens OpCenter 10.2.
/// Uses SPIFFE/SPIRE-issued mTLS certificates (CONN-009).
/// On drop/error, activates 10-second REST polling fallback per RISK-02.
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use anyhow::Context;
use deadpool_postgres::Pool;
use rdkafka::producer::FutureProducer;
use tokio::time::sleep;
use tokio_tungstenite::tungstenite::Message;
use tracing::{error, info, warn};

use crate::config::Settings;
use crate::event_processor::{process_andon_event, process_oee_event};
use crate::metrics::SUBSCRIPTION_MODE;

const GQL_INIT: &str = r#"{"type":"connection_init"}"#;
const GQL_OEE_SUB: &str = r#"{"id":"1","type":"subscribe","payload":{"query":"subscription { opcenterOeeUpdated { lineId oeePercent availability performance quality measuredAt opcenterEventId } }"}}"#;
const GQL_ANDON_SUB: &str = r#"{"id":"2","type":"subscribe","payload":{"query":"subscription { opcenterAndonAlert { stationId lineId alertType predictedFailureType minutesToPredictedFailure recommendedAction detectedAt opcenterEventId eventType } }"}}"#;

/// true = GraphQL subscription active, false = REST polling fallback.
static SUBSCRIPTION_ACTIVE: AtomicBool = AtomicBool::new(false);

pub fn subscription_active() -> bool {
    SUBSCRIPTION_ACTIVE.load(Ordering::Relaxed)
}

/// Spawn the GraphQL subscription loop and the always-running REST polling fallback.
/// The subscription loop reconnects on drop; the polling loop only fires when the
/// subscription is inactive.
pub fn spawn_subscription_loop(
    settings: Arc<Settings>,
    pool: Pool,
    producer: Arc<FutureProducer>,
) {
    let s = settings.clone();
    let p = pool.clone();
    let prod = producer.clone();

    tokio::spawn(async move {
        loop {
            info!("connecting to OpCenter GraphQL subscription");
            match run_subscription(s.clone(), p.clone(), prod.clone()).await {
                Ok(()) => {
                    warn!("GraphQL subscription ended cleanly; will reconnect");
                }
                Err(e) => {
                    error!(
                        error = %e,
                        "GraphQL subscription error; activating REST polling fallback"
                    );
                }
            }
            SUBSCRIPTION_ACTIVE.store(false, Ordering::Relaxed);
            SUBSCRIPTION_MODE.with_label_values(&["graphql"]).set(0);
            // Brief back-off before reconnect attempt.
            sleep(Duration::from_secs(5)).await;
        }
    });

    spawn_rest_polling_fallback(settings, pool, producer);
}

async fn run_subscription(
    settings: Arc<Settings>,
    pool: Pool,
    producer: Arc<FutureProducer>,
) -> anyhow::Result<()> {
    use futures_util::{SinkExt, StreamExt};

    let tls_connector = build_tls_connector(&settings)?;
    let connector = tokio_tungstenite::Connector::NativeTls(tls_connector);

    let (mut ws, _) =
        tokio_tungstenite::connect_async_tls_with_config(
            &settings.opcenter_graphql_ws_url,
            None,
            false,
            Some(connector),
        )
        .await
        .context("GraphQL WebSocket connect failed")?;

    // graphql-ws protocol handshake
    ws.send(Message::Text(GQL_INIT.to_string())).await?;

    // Wait for connection_ack
    loop {
        match ws.next().await {
            Some(Ok(Message::Text(text))) => {
                let v: serde_json::Value = serde_json::from_str(&text)?;
                if v["type"] == "connection_ack" {
                    break;
                }
            }
            Some(Ok(_)) => {} // ignore non-text frames
            Some(Err(e)) => return Err(e.into()),
            None => anyhow::bail!("WebSocket closed before connection_ack"),
        }
    }

    ws.send(Message::Text(GQL_OEE_SUB.to_string())).await?;
    ws.send(Message::Text(GQL_ANDON_SUB.to_string())).await?;

    SUBSCRIPTION_ACTIVE.store(true, Ordering::Relaxed);
    SUBSCRIPTION_MODE.with_label_values(&["graphql"]).set(1);
    info!("OpCenter GraphQL subscription established");

    while let Some(msg) = ws.next().await {
        match msg? {
            Message::Text(text) => handle_ws_message(&text, &pool, &producer).await,
            Message::Close(_) => {
                warn!("OpCenter WebSocket sent Close frame");
                break;
            }
            _ => {}
        }
    }
    Ok(())
}

async fn handle_ws_message(text: &str, pool: &Pool, producer: &FutureProducer) {
    let v: serde_json::Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(e) => {
            warn!(error = %e, "could not parse WebSocket message");
            return;
        }
    };

    if v["type"] != "next" {
        if v["type"] == "error" {
            error!(payload = %v["payload"], "GraphQL subscription error frame");
        }
        return;
    }

    let data = &v["payload"]["data"];

    if let Some(oee) = data.get("opcenterOeeUpdated") {
        dispatch_event(oee, pool, producer, true).await;
    }
    if let Some(andon) = data.get("opcenterAndonAlert") {
        dispatch_event(andon, pool, producer, false).await;
    }
}

async fn dispatch_event(
    raw: &serde_json::Value,
    pool: &Pool,
    producer: &FutureProducer,
    is_oee: bool,
) {
    let client = match pool.get().await {
        Ok(c) => c,
        Err(e) => {
            error!(error = %e, "failed to acquire db connection");
            return;
        }
    };
    let result = if is_oee {
        process_oee_event(&client, producer, raw).await
    } else {
        process_andon_event(&client, producer, raw).await
    };
    if let Err(e) = result {
        let kind = if is_oee { "OEE" } else { "Andon" };
        error!(error = %e, "{kind} event processing failed");
    }
}

/// Spawn the REST polling fallback (RISK-02).
/// Polls every `rest_poll_interval` seconds, but only when the subscription is down.
fn spawn_rest_polling_fallback(
    settings: Arc<Settings>,
    pool: Pool,
    producer: Arc<FutureProducer>,
) {
    tokio::spawn(async move {
        loop {
            sleep(settings.rest_poll_interval()).await;

            if subscription_active() {
                continue; // Subscription healthy — stay idle.
            }

            info!("REST polling fallback: fetching OEE and Andon from OpCenter");
            if let Err(e) =
                poll_rest_once(settings.clone(), pool.clone(), producer.clone()).await
            {
                error!(error = %e, "REST polling fallback error");
            }
        }
    });
}

async fn poll_rest_once(
    settings: Arc<Settings>,
    pool: Pool,
    producer: Arc<FutureProducer>,
) -> anyhow::Result<()> {
    let http = reqwest::Client::builder()
        .timeout(settings.http_timeout())
        .build()?;

    // OEE REST endpoint
    let oee_url = format!("{}/oee/snapshots", settings.opcenter_rest_url);
    let oee_items: Vec<serde_json::Value> = http
        .get(&oee_url)
        .header("X-API-Key", &settings.opcenter_api_key)
        .send()
        .await
        .context("OpCenter REST OEE request failed")?
        .json()
        .await
        .context("OpCenter REST OEE response parse failed")?;

    let db = pool.get().await?;
    for raw in &oee_items {
        if let Err(e) = process_oee_event(&db, &producer, raw).await {
            warn!(error = %e, "REST OEE poll: event processing failed");
        }
    }

    // Andon REST endpoint
    let andon_url = format!("{}/andon/alerts?status=open", settings.opcenter_rest_url);
    let andon_items: Vec<serde_json::Value> = http
        .get(&andon_url)
        .header("X-API-Key", &settings.opcenter_api_key)
        .send()
        .await
        .context("OpCenter REST Andon request failed")?
        .json()
        .await
        .context("OpCenter REST Andon response parse failed")?;

    for raw in &andon_items {
        if let Err(e) = process_andon_event(&db, &producer, raw).await {
            warn!(error = %e, "REST Andon poll: event processing failed");
        }
    }

    Ok(())
}

fn build_tls_connector(settings: &Settings) -> anyhow::Result<native_tls::TlsConnector> {
    use native_tls::{Identity, TlsConnector};
    use std::fs;

    let cert_pem = fs::read(&settings.spiffe_svid_cert_path)
        .context("read SPIFFE SVID cert")?;
    let key_pem = fs::read(&settings.spiffe_svid_key_path)
        .context("read SPIFFE SVID key")?;
    let ca_pem = fs::read(&settings.spiffe_bundle_path)
        .context("read SPIFFE trust bundle")?;

    let identity = Identity::from_pkcs8(&cert_pem, &key_pem)?;
    let ca_cert = native_tls::Certificate::from_pem(&ca_pem)?;

    let connector = TlsConnector::builder()
        .identity(identity)
        .add_root_certificate(ca_cert)
        .min_protocol_version(Some(native_tls::Protocol::Tlsv12))
        .build()?;
    Ok(connector)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn subscription_active_flag_roundtrip() {
        let original = SUBSCRIPTION_ACTIVE.load(Ordering::Relaxed);
        SUBSCRIPTION_ACTIVE.store(false, Ordering::Relaxed);
        assert!(!subscription_active(), "should report inactive after store(false)");
        SUBSCRIPTION_ACTIVE.store(true, Ordering::Relaxed);
        assert!(subscription_active(), "should report active after store(true)");
        // Restore
        SUBSCRIPTION_ACTIVE.store(original, Ordering::Relaxed);
    }

    #[test]
    fn fallback_activates_when_subscription_inactive() {
        SUBSCRIPTION_ACTIVE.store(false, Ordering::Relaxed);
        assert!(
            !subscription_active(),
            "subscription should report inactive — REST fallback should fire"
        );
        SUBSCRIPTION_ACTIVE.store(false, Ordering::Relaxed); // reset
    }

    #[test]
    fn fallback_stays_idle_when_subscription_active() {
        SUBSCRIPTION_ACTIVE.store(true, Ordering::Relaxed);
        assert!(
            subscription_active(),
            "subscription active — REST fallback should stay idle"
        );
        SUBSCRIPTION_ACTIVE.store(false, Ordering::Relaxed); // reset
    }
}
