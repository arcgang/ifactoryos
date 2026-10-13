import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { TorqueIndicatorState } from "../hooks/useTorqueStream";

interface Props {
  state: TorqueIndicatorState;
  fastenerPointId?: string;
  /** Called when the green (in_tolerance) indicator renders — adoption instrumentation. */
  onTorqueConfirmationViewed?: (torqueLogId: string) => void;
}

/**
 * AR overlay step indicator for torque tightening confirmation.
 *
 * States:
 *   neutral         — awaiting PLC event (spinner / loading)
 *   in_tolerance    — green indicator (torque within spec)
 *   out_of_tolerance — red indicator + HOLD message
 *   pending_sync    — amber indicator (edge-buffered, UNS offline)
 *   sync_confirmed  — back to neutral after sync clears
 */
export function TorqueStepIndicator({ state, fastenerPointId, onTorqueConfirmationViewed }: Props) {
  const { t } = useTranslation();
  const adoptionFiredRef = useRef(false);

  // Fire TORQUE_CONFIRMATION_VIEWED adoption event exactly once when green renders
  useEffect(() => {
    if (state.kind === "in_tolerance" && !adoptionFiredRef.current) {
      adoptionFiredRef.current = true;
      onTorqueConfirmationViewed?.(state.torqueLogId);
    }
    // Reset when state leaves in_tolerance so a next tightening fires again
    if (state.kind !== "in_tolerance") {
      adoptionFiredRef.current = false;
    }
  }, [state, onTorqueConfirmationViewed]);

  if (state.kind === "neutral" || state.kind === "sync_confirmed") {
    return (
      <div
        className="torque-indicator torque-indicator--neutral"
        role="status"
        aria-label={t("torque.indicator.neutral_label")}
        aria-live="polite"
      >
        <span className="torque-indicator__icon torque-indicator__icon--spinner" aria-hidden="true" />
        <span className="torque-indicator__text">{t("torque.indicator.neutral_text")}</span>
      </div>
    );
  }

  if (state.kind === "in_tolerance") {
    return (
      <div
        className="torque-indicator torque-indicator--green"
        role="status"
        aria-label={t("torque.indicator.in_tolerance_label", {
          torqueNm: state.torqueNm,
          torqueLogId: state.torqueLogId,
        })}
        aria-live="polite"
      >
        <span className="torque-indicator__icon torque-indicator__icon--green" aria-hidden="true">✓</span>
        <div className="torque-indicator__body">
          <div className="torque-indicator__title">{t("torque.indicator.in_tolerance_title")}</div>
          <div className="torque-indicator__sub">
            {t("torque.indicator.in_tolerance_sub", { torqueNm: state.torqueNm })}
          </div>
          <div className="torque-indicator__meta">
            {t("torque.indicator.log_id_label")}: {state.torqueLogId}
          </div>
        </div>
      </div>
    );
  }

  if (state.kind === "out_of_tolerance") {
    return (
      <div
        className="torque-indicator torque-indicator--red"
        role="alert"
        aria-label={t("torque.indicator.oot_label", {
          vin: state.vin,
          fastenerPointId: state.fastenerPointId,
          torqueNm: state.torqueNm,
          specNm: state.specNm,
        })}
        aria-live="assertive"
      >
        <span className="torque-indicator__icon torque-indicator__icon--red" aria-hidden="true">✕</span>
        <div className="torque-indicator__body">
          <div className="torque-indicator__title">{t("torque.indicator.oot_title")}</div>
          <div className="torque-indicator__sub">
            {t("torque.indicator.oot_sub", {
              torqueNm: state.torqueNm,
              specNm: state.specNm,
            })}
          </div>
          <div className="torque-indicator__meta">
            {fastenerPointId ?? state.fastenerPointId} · {t("torque.indicator.alert_id_label")}: {state.alertId}
          </div>
          <div className="torque-indicator__hold">{t("torque.indicator.hold_message")}</div>
        </div>
      </div>
    );
  }

  // pending_sync
  return (
    <div
      className="torque-indicator torque-indicator--amber"
      role="status"
      aria-label={t("torque.indicator.pending_sync_label")}
      aria-live="polite"
    >
      <span className="torque-indicator__icon torque-indicator__icon--amber" aria-hidden="true">⏳</span>
      <div className="torque-indicator__body">
        <div className="torque-indicator__title">{t("torque.indicator.pending_sync_title")}</div>
        <div className="torque-indicator__sub">{t("torque.indicator.pending_sync_sub")}</div>
      </div>
    </div>
  );
}
