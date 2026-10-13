import { useTranslation } from "react-i18next";
import type { SequencePublishResponse } from "../types/sequence";

interface PublishConfirmationBannerProps {
  response: SequencePublishResponse;
  onDismiss: () => void;
}

export function PublishConfirmationBanner({
  response,
  onDismiss,
}: PublishConfirmationBannerProps) {
  const { t } = useTranslation();

  const publishedAt = response.publishedAt
    ? new Date(response.publishedAt).toISOString()
    : null;

  return (
    <div
      className="publish-banner publish-banner--success"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <div className="publish-banner__icon" aria-hidden="true">✓</div>
      <div className="publish-banner__body">
        <h3 className="publish-banner__title">
          {t("publish.successTitle")}
        </h3>
        <dl className="publish-banner__kv">
          <dt>{t("publish.opcenterSequenceId")}</dt>
          <dd className="mono">{response.opcenterSequenceId ?? "—"}</dd>
          <dt>{t("publish.publishedAt")}</dt>
          <dd className="mono">{publishedAt ?? "—"}</dd>
          <dt>{t("publish.source")}</dt>
          <dd className="mono">{response.source}</dd>
          <dt>{t("publish.status")}</dt>
          <dd className="mono">{response.status}</dd>
          <dt>{t("publish.syncJobId")}</dt>
          <dd className="mono">{response.syncJobId}</dd>
          {response.auditEventId && (
            <>
              <dt>{t("publish.auditEventId")}</dt>
              <dd className="mono">{response.auditEventId}</dd>
            </>
          )}
        </dl>
      </div>
      <button
        type="button"
        className="publish-banner__dismiss btn btn-secondary"
        onClick={onDismiss}
        aria-label={t("publish.dismissBanner")}
      >
        {t("publish.dismissBanner")}
      </button>
    </div>
  );
}
