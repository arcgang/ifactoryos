import { useTranslation } from "react-i18next";
import { useBilingualContent, type BilingualStrings } from "../hooks/useBilingualContent";

export interface AndonAlertContent {
  title: BilingualStrings;
  body: BilingualStrings;
  source?: BilingualStrings;
}

interface Props {
  content: AndonAlertContent;
  variant?: "error" | "warning";
  countdown?: string | null;
  onAcknowledge?: () => void;
  onDispatchMaintenance?: () => void;
}

export function AndonAlert({
  content,
  variant = "warning",
  countdown,
  onAcknowledge,
  onDispatchMaintenance,
}: Props) {
  const { t } = useTranslation();

  const title = useBilingualContent(content.title);
  const body = useBilingualContent(content.body);
  const source = useBilingualContent(content.source ?? null);

  if (title === null || body === null) {
    return (
      <div
        className="alert alert-info"
        role="status"
        aria-label={t("andon.translation_pending")}
      >
        <span className="alert-icon" aria-hidden="true">🕐</span>
        <div className="alert-body">
          <div className="alert-title">{t("andon.translation_pending")}</div>
        </div>
      </div>
    );
  }

  const alertClass = variant === "error" ? "alert alert-err" : "alert alert-warn";
  const icon = variant === "error" ? "⚡" : "⚠";

  return (
    <div className={alertClass} role="alert" aria-live="assertive">
      <span className="alert-icon" aria-hidden="true">{icon}</span>
      <div className="alert-body">
        <div className="alert-title">{title}</div>
        <div className="alert-text">{body}</div>
        {source && <div className="alert-time">{source}</div>}
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "6px", flexShrink: 0 }}>
        {countdown && (
          <div className="countdown-chip" aria-label={t("andon.minutes_until_failure", { count: Number(countdown) })}>
            ⏱ {countdown}
          </div>
        )}
        {onDispatchMaintenance && (
          <button className="btn btn-danger btn-sm" type="button" onClick={onDispatchMaintenance}>
            {t("command_center.maintenance_dispatch")}
          </button>
        )}
        {onAcknowledge && (
          <button className="btn btn-ghost btn-sm" type="button" onClick={onAcknowledge}>
            {t("command_center.acknowledge")}
          </button>
        )}
      </div>
    </div>
  );
}
