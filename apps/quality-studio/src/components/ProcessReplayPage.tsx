import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { getAllVinThreadEvents, getVinThreadCompleteness } from "../services/api";
import type { StageCompletenessDetail, ThreadEvent, ThreadCompletenessResponse } from "../types/digitalThread";

interface Props {
  vin: string;
}

type State =
  | { phase: "loading" }
  | { phase: "error"; message: string; isUnavailable: boolean }
  | { phase: "empty" }
  | { phase: "done"; completeness: ThreadCompletenessResponse; events: ThreadEvent[] };

function groupEventsByStage(events: ThreadEvent[]): Map<number, ThreadEvent[]> {
  const map = new Map<number, ThreadEvent[]>();
  for (const ev of events) {
    if (ev.eventType === "PAINT_PREP_STAGE" && ev.stageNumber != null) {
      const existing = map.get(ev.stageNumber) ?? [];
      existing.push(ev);
      map.set(ev.stageNumber, existing);
    }
  }
  for (const [k, v] of map) {
    map.set(k, [...v].sort((a, b) => a.occurredAt.localeCompare(b.occurredAt)));
  }
  return map;
}

function StageSkeletonLane() {
  return (
    <div
      className="pr-lane pr-lane--skeleton"
      role="region"
      aria-label="Loading stage"
      aria-busy="true"
    >
      <div className="pr-lane-label pr-lane-label--skeleton" />
      <div className="pr-lane-track pr-lane-track--skeleton" />
    </div>
  );
}

interface StageLaneProps {
  stage: StageCompletenessDetail;
  events: ThreadEvent[];
}

function StageLane({ stage, events }: StageLaneProps) {
  const { t } = useTranslation();
  const label = `${t("processReplay.stageLaneLabel", { n: stage.stageNumber, name: stage.stageName })}`;

  if (!stage.available) {
    return (
      <div
        className="pr-lane pr-lane--unavailable"
        role="region"
        aria-label={label}
        data-testid={`stage-lane-${stage.stageNumber}`}
      >
        <div className="pr-lane-label">
          <span className="pr-lane-number">S{stage.stageNumber}</span>
          <span className="pr-lane-name">{stage.stageName}</span>
        </div>
        <div className="pr-lane-track pr-lane-track--gap">
          <span className="pr-gap-text">{t("processReplay.dataUnavailable")}</span>
          <span className="pr-gap-pct">
            {stage.completenessPercent.toFixed(1)}% {t("processReplay.completeness")}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div
      className="pr-lane pr-lane--available"
      role="region"
      aria-label={label}
      data-testid={`stage-lane-${stage.stageNumber}`}
    >
      <div className="pr-lane-label">
        <span className="pr-lane-number">S{stage.stageNumber}</span>
        <span className="pr-lane-name">{stage.stageName}</span>
      </div>
      <div className="pr-lane-track">
        {events.map((ev) => (
          <div
            key={ev.eventId}
            className="pr-event-node"
            title={ev.occurredAt}
            data-testid={`pr-event-${ev.eventId}`}
          >
            <span className="pr-event-ts">{new Date(ev.occurredAt).toISOString().slice(11, 19)}</span>
            <span className="pr-event-params">
              {Object.entries(ev.parameters)
                .slice(0, 3)
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ")}
            </span>
          </div>
        ))}
        {events.length === 0 && (
          <span className="pr-no-events">{t("processReplay.noEvents")}</span>
        )}
      </div>
    </div>
  );
}

export function ProcessReplayPage({ vin }: Props) {
  const { t } = useTranslation();
  const [state, setState] = useState<State>({ phase: "loading" });
  const [retryCount, setRetryCount] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ phase: "loading" });

    async function load() {
      try {
        const [completeness, threadRes] = await Promise.all([
          getVinThreadCompleteness(vin, controller.signal),
          getAllVinThreadEvents(vin, "PAINT", controller.signal),
        ]);

        if (controller.signal.aborted) return;

        if (completeness.stages.length === 0 && threadRes.data.length === 0) {
          setState({ phase: "empty" });
          return;
        }

        setState({ phase: "done", completeness, events: threadRes.data });
      } catch (err: unknown) {
        if (controller.signal.aborted) return;
        const error = err as Error & { status?: number };
        if (error.status === 404) {
          setState({ phase: "empty" });
        } else {
          const isUnavailable = error.status === 503;
          setState({
            phase: "error",
            message: isUnavailable
              ? t("processReplay.errorUnavailable")
              : t("processReplay.error"),
            isUnavailable,
          });
        }
      }
    }

    void load();

    return () => {
      controller.abort();
    };
  }, [vin, t, retryCount]);

  if (state.phase === "loading") {
    return (
      <section className="process-replay" aria-labelledby="pr-heading" aria-busy="true">
        <h2 id="pr-heading">{t("processReplay.title")}</h2>
        <div className="pr-completeness-bar pr-completeness-bar--skeleton" />
        <div className="pr-lanes" aria-label={t("processReplay.timelaneLabel")}>
          {Array.from({ length: 14 }, (_, i) => (
            <StageSkeletonLane key={i} />
          ))}
        </div>
      </section>
    );
  }

  if (state.phase === "error") {
    return (
      <section className="process-replay" aria-labelledby="pr-heading">
        <h2 id="pr-heading">{t("processReplay.title")}</h2>
        <div role="alert" className="pr-error" data-testid="pr-error">
          <p>{state.message}</p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setRetryCount((c) => c + 1)}
            data-testid="pr-retry-btn"
          >
            {t("processReplay.retry")}
          </button>
        </div>
      </section>
    );
  }

  if (state.phase === "empty") {
    return (
      <section className="process-replay" aria-labelledby="pr-heading">
        <h2 id="pr-heading">{t("processReplay.title")}</h2>
        <p className="pr-empty" data-testid="pr-empty">
          {t("processReplay.empty")}
        </p>
      </section>
    );
  }

  const { completeness, events } = state;
  const eventsByStage = groupEventsByStage(events);

  const sortedStages = [...completeness.stages].sort(
    (a, b) => a.stageNumber - b.stageNumber,
  );

  return (
    <section className="process-replay" aria-labelledby="pr-heading">
      <h2 id="pr-heading">{t("processReplay.title")}</h2>

      <div className="pr-completeness-header" data-testid="pr-completeness-header">
        <span className="pr-completeness-pct" data-testid="pr-overall-pct">
          {completeness.overallCompletenessPercent.toFixed(1)}%{" "}
          {t("processReplay.completeness")}
        </span>
        {completeness.meetsThreshold === false && (
          <span className="pr-threshold-badge pr-threshold-badge--fail" data-testid="pr-threshold-fail">
            {t("processReplay.belowThreshold")}
          </span>
        )}
        {completeness.meetsThreshold === true && (
          <span className="pr-threshold-badge pr-threshold-badge--pass" data-testid="pr-threshold-pass">
            {t("processReplay.meetsThreshold")}
          </span>
        )}
      </div>

      <div
        className="pr-lanes"
        role="list"
        aria-label={t("processReplay.timelaneLabel")}
        data-testid="pr-lanes"
      >
        {sortedStages.map((stage) => (
          <StageLane
            key={stage.stageNumber}
            stage={stage}
            events={eventsByStage.get(stage.stageNumber) ?? []}
          />
        ))}
      </div>
    </section>
  );
}
