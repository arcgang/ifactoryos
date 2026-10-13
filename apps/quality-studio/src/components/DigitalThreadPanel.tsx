import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { getAllVinThreadEvents } from "../services/api";
import type { ThreadEvent, VinThreadResponse } from "../types/digitalThread";

interface Props {
  vin: string;
}

type State =
  | { phase: "loading" }
  | { phase: "error"; message: string; isUnavailable: boolean }
  | { phase: "empty" }
  | { phase: "done"; thread: VinThreadResponse };

function EventCardSkeleton() {
  return (
    <div className="dt-event-card dt-event-card--skeleton" aria-busy="true">
      <div className="dt-skeleton-line dt-skeleton-line--short" />
      <div className="dt-skeleton-line" />
    </div>
  );
}

interface EventCardProps {
  event: ThreadEvent;
  showChainComplete: boolean;
}

function EventCard({ event, showChainComplete }: EventCardProps) {
  const { t } = useTranslation();

  if (
    event.eventType === "SHOP_BOUNDARY_TRANSITION" &&
    event.dataSource === "SYSTEM_INFERRED"
  ) {
    return (
      <div
        className="dt-gap-indicator"
        role="alert"
        aria-label={t("digitalThread.gapDetectedLabel")}
        data-testid={`dt-gap-${event.eventId}`}
      >
        <span className="dt-gap-icon" aria-hidden="true">⚠</span>
        <span className="dt-gap-text">{t("digitalThread.gapDetected")}</span>
        <span className="dt-gap-shop">{event.shop}</span>
      </div>
    );
  }

  if (event.eventType === "SHOP_BOUNDARY_TRANSITION") {
    return (
      <div
        className="dt-shop-divider"
        role="separator"
        aria-label={t("digitalThread.shopTransitionLabel", { shop: event.shop })}
        data-testid={`dt-shop-divider-${event.shop}`}
      >
        <span className="dt-shop-arrow" aria-hidden="true">→</span>
        <span className="dt-shop-name">{event.shop}</span>
      </div>
    );
  }

  const topParams = Object.entries(event.parameters).slice(0, 5);

  return (
    <article
      className="dt-event-card"
      data-event-type={event.eventType}
      data-testid={`dt-event-${event.eventId}`}
    >
      {showChainComplete && (
        <div className="dt-chain-complete-badge" data-testid="dt-chain-complete-badge">
          {t("digitalThread.chainComplete")}
        </div>
      )}
      <header className="dt-event-header">
        <time className="dt-event-time" dateTime={event.occurredAt}>
          {new Date(event.occurredAt).toISOString().replace("T", " ").slice(0, 19)} UTC
        </time>
        <span className="dt-event-type">{event.eventType}</span>
      </header>
      <div className="dt-event-meta">
        <span className="dt-event-shop">{event.shop}</span>
        <span className="dt-event-sep" aria-hidden="true"> · </span>
        <span className="dt-event-station">{event.station}</span>
        <span className="dt-event-sep" aria-hidden="true"> · </span>
        <span className="dt-event-source">{event.dataSource}</span>
      </div>
      {topParams.length > 0 && (
        <dl className="dt-event-params">
          {topParams.map(([k, v]) => (
            <div key={k} className="dt-param-row">
              <dt className="dt-param-key">{k}</dt>
              <dd className="dt-param-val">{String(v ?? "—")}</dd>
            </div>
          ))}
        </dl>
      )}
    </article>
  );
}

function isFinalGateEvent(ev: ThreadEvent): boolean {
  return ev.eventType === "QUALITY_CERTIFICATION";
}

export function DigitalThreadPanel({ vin }: Props) {
  const { t } = useTranslation();
  const [state, setState] = useState<State>({ phase: "loading" });
  const [retryCount, setRetryCount] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ phase: "loading" });

    getAllVinThreadEvents(vin, undefined, controller.signal)
      .then((thread) => {
        if (controller.signal.aborted) return;
        if (thread.data.length === 0) {
          setState({ phase: "empty" });
        } else {
          setState({ phase: "done", thread });
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const error = err as Error & { status?: number };
        if (error.status === 404) {
          setState({ phase: "empty" });
        } else {
          const isUnavailable = error.status === 503;
          setState({
            phase: "error",
            message: isUnavailable
              ? t("digitalThread.errorUnavailable")
              : t("digitalThread.error"),
            isUnavailable,
          });
        }
      });

    return () => {
      controller.abort();
    };
  }, [vin, t, retryCount]);

  if (state.phase === "loading") {
    return (
      <section className="digital-thread-panel" aria-labelledby="dt-heading" aria-busy="true">
        <h2 id="dt-heading">{t("digitalThread.title")}</h2>
        <div className="dt-timeline" data-testid="dt-skeleton">
          {Array.from({ length: 5 }, (_, i) => (
            <EventCardSkeleton key={i} />
          ))}
        </div>
      </section>
    );
  }

  if (state.phase === "error") {
    return (
      <section className="digital-thread-panel" aria-labelledby="dt-heading">
        <h2 id="dt-heading">{t("digitalThread.title")}</h2>
        <div role="alert" className="dt-error" data-testid="dt-error">
          <p>{state.message}</p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setRetryCount((c) => c + 1)}
            data-testid="dt-retry-btn"
          >
            {t("digitalThread.retry")}
          </button>
        </div>
      </section>
    );
  }

  if (state.phase === "empty") {
    return (
      <section className="digital-thread-panel" aria-labelledby="dt-heading">
        <h2 id="dt-heading">{t("digitalThread.title")}</h2>
        <p className="dt-empty" data-testid="dt-empty">
          {t("digitalThread.empty")}
        </p>
      </section>
    );
  }

  const { thread } = state;
  const isFinalGateComplete = thread.vinStatus === "FINAL_GATE_COMPLETE";

  let finalGateIdx = -1;
  if (isFinalGateComplete) {
    for (let i = thread.data.length - 1; i >= 0; i--) {
      if (isFinalGateEvent(thread.data[i])) {
        finalGateIdx = i;
        break;
      }
    }
  }

  return (
    <section className="digital-thread-panel" aria-labelledby="dt-heading">
      <h2 id="dt-heading">
        {t("digitalThread.title")}
        <span className="dt-vin-label">{thread.vin}</span>
      </h2>

      <ol className="dt-timeline" data-testid="dt-timeline">
        {thread.data.map((event, idx) => (
          <li key={event.eventId} className="dt-timeline-item">
            <EventCard
              event={event}
              showChainComplete={idx === finalGateIdx}
            />
          </li>
        ))}
      </ol>
    </section>
  );
}
