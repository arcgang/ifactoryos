import { useTranslation } from "react-i18next";

export interface TorqueOotPayload {
  torqueLogId: string;
  vin: string;
  fastenerPointId: string;
  torqueNm: number;
  specNm: number;
  toleranceNm: number;
  alertId: string;
  recordedAt: string;
}

interface Props {
  payload: TorqueOotPayload;
  onAcknowledge?: () => void;
}

/**
 * Shift Lead alert toast for out-of-tolerance torque events.
 * Renders within 30 seconds of the plcTimestamp (per AC-69c77dfd).
 * Carries role="alert" so screen readers announce it immediately.
 */
export function TorqueOutOfToleranceToast({ payload, onAcknowledge }: Props) {
  const { t } = useTranslation();

  return (
    <div className="alert alert-err torque-oot-toast" role="alert" aria-live="assertive">
      <span className="alert-icon" aria-hidden="true">⚡</span>
      <div className="alert-body">
        <div className="alert-title">{t("torque.oot_toast.title")}</div>
        <div className="alert-text">
          <span className="torque-oot-toast__field">
            <span className="torque-oot-toast__label">{t("torque.oot_toast.vin_label")}: </span>
            <strong>{payload.vin}</strong>
          </span>
          <span className="torque-oot-toast__field">
            <span className="torque-oot-toast__label">{t("torque.oot_toast.fastener_label")}: </span>
            <strong>{payload.fastenerPointId}</strong>
          </span>
          <span className="torque-oot-toast__field">
            <span className="torque-oot-toast__label">{t("torque.oot_toast.torque_label")}: </span>
            <strong className="torque-oot-toast__value--oor">
              {payload.torqueNm} {t("torque.oot_toast.nm_unit")}
            </strong>
            {" "}
            <span className="torque-oot-toast__spec">
              ({t("torque.oot_toast.spec_label")}: {payload.specNm} {t("torque.oot_toast.nm_unit")} ±{payload.toleranceNm})
            </span>
          </span>
          <span className="torque-oot-toast__field">
            <span className="torque-oot-toast__label">{t("torque.oot_toast.alert_id_label")}: </span>
            <code>{payload.alertId}</code>
          </span>
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "6px", flexShrink: 0 }}>
        {onAcknowledge && (
          <button className="btn btn-ghost btn-sm" type="button" onClick={onAcknowledge}>
            {t("command_center.acknowledge")}
          </button>
        )}
      </div>
    </div>
  );
}
