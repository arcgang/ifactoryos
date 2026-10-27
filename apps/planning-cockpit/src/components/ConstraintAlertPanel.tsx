import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { acknowledgeAlert, getMy26Config, postResequence, publishAlertSequence } from "../services/api";
import type { ConstraintAlertRecord } from "../types/alerts";
import type { OptimizeResponse, RankedVin } from "../types/optimizer";
import { emitPublishConfirmedEvent } from "../instrumentation/sessionEvents";

const CONSTRAINT_TYPE_LABELS: Record<string, string> = {
  paint_capacity: "Paint Capacity",
  body_wip_queue: "Body WIP Queue",
  kitting_readiness: "Kitting Readiness",
  maintenance_window: "Maintenance Window",
  agv_delay: "AGV Delay",
  press_die_overrun: "Press Die Overrun",
};

function emitAdoptionEvent(
  action: string,
  detail: Record<string, unknown>,
): void {
  const event = { action, ...detail, timestamp: new Date().toISOString() };
  window.dispatchEvent(new CustomEvent("ifos:adoption", { detail: event }));
}

function generateIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

interface TargetedSequenceViewProps {
  alert: ConstraintAlertRecord;
  result: OptimizeResponse;
  onPublish: (sequenceId: string) => Promise<void>;
  publishing: boolean;
  publishError: string | null;
}

function TargetedSequenceView({
  alert,
  result,
  onPublish,
  publishing,
  publishError,
}: TargetedSequenceViewProps) {
  const { t } = useTranslation();
  const sorted = [...result.rankedSequence].sort((a, b) => a.position - b.position);

  return (
    <section
      className="alert-scenario-panel"
      aria-label={t("constraintAlert.scenarioLabLabel")}
      data-testid="scenario-lab-panel"
    >
      <div className="scenario-panel-header">
        <h4 id="scenario-lab-heading" className="scenario-panel-title">
          {t("constraintAlert.targetedResequenceTitle")}
        </h4>
        <div className="scenario-panel-meta">
          <span
            className={`result-type-tag${result.resultType === "monte_carlo" ? " result-type-tag--mc" : " result-type-tag--hf"}`}
          >
            {result.resultType === "monte_carlo"
              ? t("sequenceBuilder.resultTypeMonteCarlo")
              : t("sequenceBuilder.resultTypeHeuristic")}
          </span>
          <span className="result-meta">
            <strong>{sorted.length}</strong> {t("constraintAlert.affectedVinsLabel")}
            {" · "}
            {t("constraintAlert.alertRef")}: {alert.alertId.slice(0, 8)}
          </span>
        </div>
      </div>

      <div className="seq-table-wrap">
        <table
          className="seq-table"
          aria-label={t("constraintAlert.targetedSequenceTableLabel")}
        >
          <thead>
            <tr>
              <th scope="col">{t("sequence.columns.position")}</th>
              <th scope="col">{t("sequence.columns.vin")}</th>
              <th scope="col">{t("sequence.columns.constraintScore")}</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row: RankedVin) => (
              <tr key={row.vin}>
                <td>
                  <span
                    className={`rank-badge${row.position <= 2 ? " rank-badge--top" : ""}`}
                    aria-label={t("sequenceBuilder.positionLabel", { pos: row.position })}
                  >
                    {row.position}
                  </span>
                </td>
                <td>
                  <span className="vin-mono">{row.vin}</span>
                </td>
                <td>
                  <span className="score-bar-num">
                    {row.constraintScore.toFixed(2)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="scenario-panel-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void onPublish(result.runId)}
          disabled={publishing}
          aria-busy={publishing}
          data-testid="publish-resequence-btn"
        >
          {publishing
            ? t("constraintAlert.publishing")
            : t("constraintAlert.publishResequence")}
        </button>
        {publishError && (
          <span role="alert" className="field-error">
            {publishError}
          </span>
        )}
      </div>
    </section>
  );
}

interface AlertCardProps {
  alert: ConstraintAlertRecord;
  onDismissed: (alertId: string) => void;
}

function AlertCard({ alert, onDismissed }: AlertCardProps) {
  const { t } = useTranslation();
  const [reseqState, setReseqState] = useState<
    | { phase: "idle" }
    | { phase: "running" }
    | { phase: "done"; result: OptimizeResponse }
    | { phase: "error"; message: string }
  >({ phase: "idle" });
  const [dismissing, setDismissing] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [published, setPublished] = useState(false);

  // Emit alert_viewed on mount
  const viewedRef = useRef(false);
  useEffect(() => {
    if (viewedRef.current) return;
    viewedRef.current = true;
    const handle = setTimeout(() => {
      emitAdoptionEvent("alert_viewed", { alertId: alert.alertId });
    }, 0);
    return () => clearTimeout(handle);
  }, [alert.alertId]);

  const handleResequence = useCallback(async () => {
    setReseqState({ phase: "running" });
    emitAdoptionEvent("resequence_triggered", {
      alertId: alert.alertId,
      affectedVinCount: alert.affectedVins.length,
    });

    try {
      const result = await postResequence({
        alertId: alert.alertId,
        affectedVins: alert.affectedVins.map((vin) => ({ vin })),
        constraintSnapshot: {
          snapshotTimestamp: new Date().toISOString(),
          paintCapacityUtilisation: 94,
          bodyWipQueueDepth: 78,
          kittingReadinessPct: 97,
        },
        idempotencyKey: generateIdempotencyKey(),
      });
      setReseqState({ phase: "done", result });
    } catch (err) {
      setReseqState({
        phase: "error",
        message: err instanceof Error ? err.message : t("errors.requestFailed"),
      });
    }
  }, [alert.alertId, alert.affectedVins, t]);

  const handlePublish = useCallback(
    async (sequenceId: string) => {
      setPublishing(true);
      setPublishError(null);
      try {
        // Resolve my26ConfigVersion: prefer version from the resequence result,
        // fall back to the currently active config.
        let my26ConfigVersion: string | null = null;
        if (reseqState.phase === "done" && reseqState.result.my26ConfigVersion) {
          my26ConfigVersion = reseqState.result.my26ConfigVersion;
        } else {
          try {
            const activeConfig = await getMy26Config();
            my26ConfigVersion = activeConfig ? String(activeConfig.configId) : null;
          } catch (err: unknown) {
            console.warn("ConstraintAlertPanel: failed to fetch my26 config", err);
          }
        }
        await publishAlertSequence({ sequenceId, isResequence: true, my26ConfigVersion });
        emitPublishConfirmedEvent();
        emitAdoptionEvent("resequence_published", { sequenceId, my26ConfigVersion });
        setPublished(true);
      } catch (err) {
        setPublishError(
          err instanceof Error ? err.message : t("errors.publishFailed"),
        );
      } finally {
        setPublishing(false);
      }
    },
    [t, reseqState],
  );

  const handleDismiss = useCallback(async () => {
    setDismissing(true);
    try {
      await acknowledgeAlert(alert.alertId);
    } catch (err: unknown) {
      console.warn("ConstraintAlertPanel: failed to acknowledge alert", err);
    } finally {
      setDismissing(false);
      onDismissed(alert.alertId);
    }
  }, [alert.alertId, onDismissed]);

  const constraintLabel =
    CONSTRAINT_TYPE_LABELS[alert.constraintType] ?? alert.constraintType;

  return (
    <div
      className="constraint-alert-card"
      role="alert"
      aria-labelledby={`alert-heading-${alert.alertId}`}
      data-testid={`alert-card-${alert.alertId}`}
    >
      <div className="constraint-alert-header">
        <div className="constraint-alert-title-row">
          <span className="alert-icon" aria-hidden="true">⚠</span>
          <h3
            id={`alert-heading-${alert.alertId}`}
            className="constraint-alert-heading"
          >
            {constraintLabel} — {t("constraintAlert.thresholdBreached")}
          </h3>
        </div>
        <button
          type="button"
          className="btn btn-secondary constraint-alert-dismiss"
          onClick={() => void handleDismiss()}
          disabled={dismissing}
          aria-label={t("constraintAlert.dismiss")}
          data-testid={`dismiss-alert-${alert.alertId}`}
        >
          {t("constraintAlert.dismiss")}
        </button>
      </div>

      <div className="constraint-alert-body">
        <dl className="alert-kv-grid">
          <div className="alert-kv-item">
            <dt className="alert-kv-label">{t("constraintAlert.currentValue")}</dt>
            <dd className="alert-kv-value alert-kv-value--warn">
              {alert.currentValue}
            </dd>
          </div>
          <div className="alert-kv-item">
            <dt className="alert-kv-label">{t("constraintAlert.threshold")}</dt>
            <dd className="alert-kv-value">{alert.thresholdValue}</dd>
          </div>
          <div className="alert-kv-item">
            <dt className="alert-kv-label">{t("constraintAlert.detectedAt")}</dt>
            <dd className="alert-kv-value alert-kv-value--mono">
              {alert.detectedAt}
            </dd>
          </div>
          <div className="alert-kv-item">
            <dt className="alert-kv-label">
              {t("constraintAlert.affectedVins")} ({alert.affectedVins.length})
            </dt>
            <dd className="alert-kv-value">
              <ul className="alert-vin-list" aria-label={t("constraintAlert.affectedVinsLabel")}>
                {alert.affectedVins.slice(0, 5).map((vin) => (
                  <li key={vin} className="vin-mono">
                    {vin}
                  </li>
                ))}
                {alert.affectedVins.length > 5 && (
                  <li className="alert-vin-more">
                    +{alert.affectedVins.length - 5} {t("constraintAlert.more")}
                  </li>
                )}
              </ul>
            </dd>
          </div>
        </dl>

        {reseqState.phase === "idle" && (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void handleResequence()}
            data-testid={`resequence-btn-${alert.alertId}`}
          >
            {t("constraintAlert.resequenceButton")}
          </button>
        )}

        {reseqState.phase === "running" && (
          <p role="status" aria-live="polite" className="alert-running">
            {t("constraintAlert.resequencing")}
          </p>
        )}

        {reseqState.phase === "error" && (
          <div role="alert" className="alert-reseq-error">
            <span>{reseqState.message}</span>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => void handleResequence()}
              data-testid={`resequence-retry-${alert.alertId}`}
            >
              {t("errors.retry")}
            </button>
          </div>
        )}

        {reseqState.phase === "done" && !published && (
          <TargetedSequenceView
            alert={alert}
            result={reseqState.result}
            onPublish={handlePublish}
            publishing={publishing}
            publishError={publishError}
          />
        )}

        {published && (
          <div
            className="alert-publish-toast"
            role="status"
            aria-live="polite"
            data-testid="publish-confirmation-toast"
          >
            <span className="toast-icon" aria-hidden="true">✓</span>
            <span>{t("constraintAlert.publishConfirmation")}</span>
          </div>
        )}
      </div>
    </div>
  );
}

interface Props {
  alerts: ConstraintAlertRecord[];
  onAlertDismissed: (alertId: string) => void;
}

export function ConstraintAlertPanel({ alerts, onAlertDismissed }: Props) {
  const { t } = useTranslation();

  if (alerts.length === 0) return null;

  return (
    <section
      className="constraint-alert-panel"
      aria-label={t("constraintAlert.panelLabel")}
      data-testid="constraint-alert-panel"
    >
      <h3 className="constraint-alert-panel-heading">
        {t("constraintAlert.panelTitle")} ({alerts.length})
      </h3>
      {alerts.map((alert) => (
        <AlertCard
          key={alert.alertId}
          alert={alert}
          onDismissed={onAlertDismissed}
        />
      ))}
    </section>
  );
}
