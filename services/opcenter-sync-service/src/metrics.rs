use once_cell::sync::Lazy;
use prometheus::{
    HistogramOpts, HistogramVec, IntCounterVec, IntGaugeVec, Opts, Registry,
};

pub static REGISTRY: Lazy<Registry> = Lazy::new(Registry::new);

/// Histogram: seconds from OpCenter event origination to Kafka commit.
/// Labels: event_type (oee | andon), line_id.
pub static DELIVERY_SECONDS: Lazy<HistogramVec> = Lazy::new(|| {
    let opts = HistogramOpts::new(
        "opcenter_sync_event_delivery_seconds",
        "Seconds from OpCenter event origination to Kafka commit",
    )
    .buckets(vec![0.1, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 30.0]);
    let h = HistogramVec::new(opts, &["event_type", "line_id"]).unwrap();
    REGISTRY.register(Box::new(h.clone())).unwrap();
    h
});

/// Counter: total events processed, by type and outcome.
pub static EVENTS_TOTAL: Lazy<IntCounterVec> = Lazy::new(|| {
    let opts = Opts::new("opcenter_sync_events_total", "Total OpCenter events processed");
    let c = IntCounterVec::new(opts, &["event_type", "outcome"]).unwrap();
    REGISTRY.register(Box::new(c.clone())).unwrap();
    c
});

/// Gauge: 1 = GraphQL subscription active, 0 = REST polling fallback.
pub static SUBSCRIPTION_MODE: Lazy<IntGaugeVec> = Lazy::new(|| {
    let opts = Opts::new(
        "opcenter_sync_subscription_mode",
        "1=GraphQL subscription active, 0=REST polling fallback",
    );
    let g = IntGaugeVec::new(opts, &["mode"]).unwrap();
    REGISTRY.register(Box::new(g.clone())).unwrap();
    g
});

/// Counter: messages sent to dead-letter queue.
pub static DLQ_SENDS_TOTAL: Lazy<IntCounterVec> = Lazy::new(|| {
    let opts = Opts::new(
        "opcenter_sync_dlq_sends_total",
        "Messages sent to dead-letter queue",
    );
    let c = IntCounterVec::new(opts, &["event_type", "reason"]).unwrap();
    REGISTRY.register(Box::new(c.clone())).unwrap();
    c
});

/// Gather all metrics from our private registry as a Prometheus text exposition.
pub fn gather_metrics() -> String {
    use prometheus::Encoder;
    let encoder = prometheus::TextEncoder::new();
    let mut buf = Vec::new();
    encoder
        .encode(&REGISTRY.gather(), &mut buf)
        .unwrap_or_default();
    String::from_utf8(buf).unwrap_or_default()
}
