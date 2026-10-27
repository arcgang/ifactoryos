import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  acknowledgeAndonAlert,
  getAndonAlert,
  type AcknowledgeResponse,
  type AndonAlertResponse,
} from "../services/api";

interface Props {
  alertId: string;
  userId?: string;
  onForbidden?: (resource: string) => void;
}

type DispatchState = "idle" | "confirming" | "submitting" | "success" | "error";

export function StationDetailPage({ alertId, userId, onForbidden }: Props) {
  const { t } = useTranslation();

  const [alert, setAlert] = useState<AndonAlertResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [dispatchState, setDispatchState] = useState<DispatchState>("idle");
  const [ackResponse, setAckResponse] = useState<AcknowledgeResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);

    getAndonAlert(alertId, userId)
      .then((data) => {
        if (!cancelled) {
          setAlert(data);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          if (
            onForbidden &&
            err instanceof Error &&
            err.name === "ForbiddenError"
          ) {
            onForbidden(`/andon/alerts/${alertId}`);
          } else {
            setLoadError(t("station_detail.error"));
          }
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [alertId, userId, onForbidden, t]);

  const handleDispatchClick = useCallback(() => {
    setDispatchState("confirming");
  }, []);

  const handleDispatchCancel = useCallback(() => {
    setDispatchState("idle");
  }, []);

  const handleDispatchConfirm = useCallback(() => {
    setDispatchState("submitting");
    acknowledgeAndonAlert(alertId, { disposition: "resolved" }, userId)
      .then((resp) => {
        setAckResponse(resp);
        setDispatchState("success");
      })
      .catch((err: unknown) => {
        if (
          onForbidden &&
          err instanceof Error &&
          err.name === "ForbiddenError"
        ) {
          onForbidden(`/andon/alerts/${alertId}/acknowledge`);
        } else {
          setDispatchState("error");
        }
      });
  }, [alertId, userId, onForbidden]);

  if (loading) {
    return (
      <div className="station-detail" aria-busy="true">
        <p role="status">{t("station_detail.loading")}</p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="station-detail">
        <p role="alert">{loadError}</p>
      </div>
    );
  }

  if (!alert) {
    return (
      <div className="station-detail">
        <p>{t("station_detail.no_alert_id")}</p>
      </div>
    );
  }

  const isAlreadyAcknowledged = alert.status !== "open";
  const canDispatch =
    !isAlreadyAcknowledged &&
    dispatchState !== "success" &&
    dispatchState !== "submitting";

  return (
    <div className="station-detail">
      <h2>{t("station_detail.title")}</h2>

      <section aria-label={t("station_detail.weld_trace_heading")}>
        <h3>{t("station_detail.weld_trace_heading")}</h3>

        <WeldTraceView
          trace={alert.weldTrace}
          alertType={alert.alertType}
          minutesToPredictedFailure={alert.minutesToPredictedFailure}
          thresholdCrossingAt={alert.thresholdCrossingAt}
          thresholdValue={alert.thresholdValue}
          currentSeriesLabel={t("station_detail.current_series_label")}
          voltageSeriesLabel={t("station_detail.voltage_series_label")}
          thresholdCrossingLabel={t("station_detail.threshold_crossing_label")}
          failurePredictedLabel={t("andon.minutes_until_failure", { count: alert.minutesToPredictedFailure ?? 0 })}
        />
      </section>

      <section className="station-detail-meta" aria-label="Alert metadata">
        {alert.minutesToPredictedFailure !== null && (
          <p data-testid="minutes-to-failure">
            <strong>{t("station_detail.minutes_to_failure_label")}:</strong>{" "}
            {alert.minutesToPredictedFailure}
          </p>
        )}
        <p>
          <strong>{t("station_detail.recommended_action_label")}:</strong>{" "}
          {alert.recommendedAction}
        </p>
      </section>

      {dispatchState === "success" && ackResponse && (
        <div role="status" className="dispatch-success">
          {t("station_detail.dispatch_success")}
        </div>
      )}

      {dispatchState === "error" && (
        <div role="alert" className="dispatch-error">
          {t("station_detail.dispatch_error")}
        </div>
      )}

      {dispatchState === "confirming" && (
        <div className="dispatch-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="dispatch-confirm-heading">
          <h4 id="dispatch-confirm-heading">{t("station_detail.dispatch_confirm_heading")}</h4>
          <p>{t("station_detail.dispatch_confirm_body")}</p>
          <div className="dispatch-confirm-actions">
            <button
              type="button"
              className="btn btn-danger"
              onClick={handleDispatchConfirm}
            >
              {t("station_detail.dispatch_confirm_yes")}
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={handleDispatchCancel}
            >
              {t("station_detail.dispatch_confirm_cancel")}
            </button>
          </div>
        </div>
      )}

      {canDispatch && dispatchState !== "confirming" && (
        <button
          type="button"
          className="btn btn-danger"
          onClick={handleDispatchClick}
          aria-label={t("command_center.maintenance_dispatch")}
        >
          {t("command_center.maintenance_dispatch")}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Weld trace view — renders current and voltage point counts inline.
// A real trace would use a charting library; this renders accessible summary
// tables so the page is testable and the data shape is verifiable.
// ---------------------------------------------------------------------------

interface WeldTraceViewProps {
  trace: AndonAlertResponse["weldTrace"];
  alertType: AndonAlertResponse["alertType"];
  minutesToPredictedFailure: number | null;
  thresholdCrossingAt: string;
  thresholdValue: number | null;
  currentSeriesLabel: string;
  voltageSeriesLabel: string;
  thresholdCrossingLabel: string;
  failurePredictedLabel: string;
}

function WeldTraceView({
  trace,
  alertType,
  minutesToPredictedFailure,
  thresholdCrossingAt,
  thresholdValue,
  currentSeriesLabel,
  voltageSeriesLabel,
  thresholdCrossingLabel,
  failurePredictedLabel,
}: WeldTraceViewProps) {
  const isPredictive = alertType === "predictive";
  return (
    <div
      className="weld-trace"
      role="img"
      aria-label={`${currentSeriesLabel} and ${voltageSeriesLabel} — last 60 minutes${isPredictive && thresholdValue !== null ? `. Threshold ${thresholdValue} crossed at ${thresholdCrossingAt}` : ""}`}
    >
      <div
        className="weld-trace-series"
        aria-label={currentSeriesLabel}
        data-testid="weld-current-series"
        data-point-count={trace.weld_current_a.length}
      >
        <span className="series-label">{currentSeriesLabel}</span>
        <span className="series-count">{trace.weld_current_a.length} pts</span>
      </div>
      <div
        className="weld-trace-series"
        aria-label={voltageSeriesLabel}
        data-testid="weld-voltage-series"
        data-point-count={trace.weld_voltage_v.length}
      >
        <span className="series-label">{voltageSeriesLabel}</span>
        <span className="series-count">{trace.weld_voltage_v.length} pts</span>
      </div>
      {isPredictive && (
        <div
          className="threshold-crossing threshold-crossing--predictive"
          data-testid="threshold-crossing"
          data-crossing-at={thresholdCrossingAt}
        >
          <span>{thresholdCrossingLabel}: {new Date(thresholdCrossingAt).toISOString()}</span>
          {thresholdValue !== null && (
            <span className="threshold-value"> ({thresholdValue})</span>
          )}
          {minutesToPredictedFailure !== null && (
            <span className="threshold-crossing-countdown" data-testid="threshold-crossing-countdown">
              {" "}— {failurePredictedLabel}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
