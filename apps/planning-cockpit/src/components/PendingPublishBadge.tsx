import { useTranslation } from "react-i18next";

interface PendingPublishBadgeProps {
  retryScheduledAt: string | null;
}

function formatRetryTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function PendingPublishBadge({ retryScheduledAt }: PendingPublishBadgeProps) {
  const { t } = useTranslation();

  return (
    <div
      className="pending-publish-badge"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <span className="pending-publish-badge__dot" aria-hidden="true" />
      <span className="pending-publish-badge__label">
        {t("publish.pendingLabel")}
      </span>
      {retryScheduledAt && (
        <span className="pending-publish-badge__retry">
          {t("publish.nextRetryAt", {
            time: formatRetryTime(retryScheduledAt),
          })}
        </span>
      )}
    </div>
  );
}
