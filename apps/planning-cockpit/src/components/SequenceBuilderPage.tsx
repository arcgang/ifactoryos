import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { SequencePublishRequest, VinSequenceItem } from "../types/sequence";
import { usePublishSequence } from "../hooks/usePublishSequence";
import { PublishButton } from "./PublishButton";
import { PublishConfirmationBanner } from "./PublishConfirmationBanner";
import { PendingPublishBadge } from "./PendingPublishBadge";

// Stub data representing a locked sequence — in production this would be fetched.
const STUB_PLAN_ID = "SPL-2026-042-A";
const STUB_LOCKED_AT = "2026-10-13T06:17:04Z";
const STUB_PLANNER_USER_ID = "usr_kmueller_pp01";

const STUB_VINS: VinSequenceItem[] = [
  { vin: "WBY11CF050N123401", position: 1, optionCodes: ["P0A96", "ZMP"] },
  { vin: "WBY11CF050N123402", position: 2, optionCodes: ["P0A96", "ZMQ"] },
  { vin: "WBY11CF050N123403", position: 3, optionCodes: ["P0A96", "ZMR"] },
  { vin: "WBY11CF050N123404", position: 4, optionCodes: ["P0B7N", "ZMQ"] },
  { vin: "WBY11CF050N123405", position: 5, optionCodes: ["P0B7N", "ZMP"] },
  { vin: "WBY11CF050N123406", position: 6, optionCodes: ["P0300", "ZMP"] },
];

const VIN_LABELS: Record<string, string> = {
  WBY11CF050N123401: "i4 M50 · Solar Orange",
  WBY11CF050N123402: "i4 eDrive40 · Brooklyn Grey",
  WBY11CF050N123403: "i4 M50 · Tanzanite Blue",
  WBY11CF050N123404: "i4 eDrive40 · Alpine White",
  WBY11CF050N123405: "i4 eDrive40 · Phytonic Blue",
  WBY11CF050N123406: "i4 M50 · Brooklyn Grey",
};

export function SequenceBuilderPage() {
  const { t } = useTranslation();
  const { publishState, publish, dismiss } = usePublishSequence(STUB_PLAN_ID);
  const [showConfirmPanel, setShowConfirmPanel] = useState(false);

  const isPublishing = publishState.phase === "publishing";
  const isQueued = publishState.phase === "queued";
  const isPublished = publishState.phase === "published";

  function handlePublishClick() {
    setShowConfirmPanel(true);
  }

  function handleConfirm() {
    setShowConfirmPanel(false);
    const req: SequencePublishRequest = {
      sequencePlanId: STUB_PLAN_ID,
      vinSequence: STUB_VINS,
      source: "planning-cockpit",
      lockedAt: STUB_LOCKED_AT,
      plannerUserId: STUB_PLANNER_USER_ID,
      idempotencyKey: crypto.randomUUID(),
    };
    void publish(req);
  }

  function handleCancel() {
    setShowConfirmPanel(false);
  }

  const sequenceStatus = isPublished
    ? t("publish.statusPublished")
    : isQueued
      ? t("publish.statusQueued")
      : t("publish.statusLocked");

  return (
    <main className="main-content" id="sequence-builder">
      <div aria-live="assertive" aria-atomic="true" className="sr-only">
        {isPublished && t("publish.srPublished")}
        {isQueued && t("publish.srQueued")}
      </div>

      <div className="page-header">
        <div className="page-header-left">
          <h2>{t("publish.pageTitle")}</h2>
          <p className="subtitle">
            {t("publish.pageSubtitle", {
              planId: STUB_PLAN_ID,
              count: STUB_VINS.length,
            })}
          </p>
        </div>
        <div className="page-header-right">
          <span
            className={`sequence-status-badge sequence-status-badge--${
              isPublished ? "published" : isQueued ? "queued" : "locked"
            }`}
            aria-label={`${t("publish.statusLabel")}: ${sequenceStatus}`}
          >
            <span className="sequence-status-badge__dot" aria-hidden="true" />
            {sequenceStatus}
          </span>
          {!isPublished && !isQueued && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              aria-label={t("publish.exportCsv")}
            >
              {t("publish.exportCsv")}
            </button>
          )}
          {!isPublished && !isQueued && (
            <PublishButton
              disabled={isPublishing}
              publishing={isPublishing}
              onClick={handlePublishClick}
            />
          )}
        </div>
      </div>

      {isPublished && publishState.phase === "published" && (
        <PublishConfirmationBanner
          response={publishState.response}
          onDismiss={dismiss}
        />
      )}

      {isQueued && publishState.phase === "queued" && (
        <PendingPublishBadge retryScheduledAt={publishState.retryScheduledAt} />
      )}

      {publishState.phase === "error" && (
        <div
          className="publish-error-banner"
          role="alert"
          aria-live="assertive"
          aria-atomic="true"
        >
          <h3 className="publish-error-banner__title">{t("publish.errorTitle")}</h3>
          <p className="publish-error-banner__body">{publishState.message}</p>
          {publishState.isRejected && (
            <a
              href="mailto:integration-lead@bmw.com"
              className="publish-error-banner__action"
            >
              {t("publish.contactIntegrationLead")}
            </a>
          )}
        </div>
      )}

      <div className="sequence-layout">
        <div className="sequence-main">
          <section className="card" aria-labelledby="seq-summary-heading">
            <div className="card__header">
              <h3 id="seq-summary-heading" className="card__title">
                {t("publish.sequenceSummaryTitle")}
              </h3>
              <p className="card__subtitle">
                {t("publish.sequenceSummarySubtitle")}
              </p>
            </div>
            <dl className="sequence-meta-grid">
              <div className="sequence-meta-field">
                <dt>{t("publish.fieldSequencePlanId")}</dt>
                <dd className="mono">{STUB_PLAN_ID}</dd>
              </div>
              <div className="sequence-meta-field">
                <dt>{t("publish.fieldSource")}</dt>
                <dd className="mono">planning-cockpit</dd>
              </div>
              <div className="sequence-meta-field">
                <dt>{t("publish.fieldLockedAt")}</dt>
                <dd className="mono">{STUB_LOCKED_AT}</dd>
              </div>
              <div className="sequence-meta-field">
                <dt>{t("publish.fieldPlannerUserId")}</dt>
                <dd className="mono">{STUB_PLANNER_USER_ID}</dd>
              </div>
              <div className="sequence-meta-field">
                <dt>{t("publish.fieldVinsCount")}</dt>
                <dd>{STUB_VINS.length} {t("publish.vehicles")}</dd>
              </div>
            </dl>

            <div className="sequence-vin-list" aria-label={t("publish.vinPreviewLabel")}>
              <div className="sequence-vin-list__header">
                <span>{t("publish.vinPreviewHeading", { count: STUB_VINS.length })}</span>
              </div>
              <table className="vin-table">
                <caption className="sr-only">{t("publish.vinTableCaption")}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t("sequence.columns.position")}</th>
                    <th scope="col">{t("sequence.columns.vin")}</th>
                    <th scope="col">{t("sequence.columns.model")}</th>
                  </tr>
                </thead>
                <tbody>
                  {STUB_VINS.map((item) => (
                    <tr key={item.vin}>
                      <td className="vin-table__pos">#{item.position}</td>
                      <td className="mono">{item.vin}</td>
                      <td>{VIN_LABELS[item.vin] ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {showConfirmPanel && (
            <section
              className="card card--action"
              aria-labelledby="confirm-publish-heading"
            >
              <h3 id="confirm-publish-heading" className="card__title card__title--blue">
                {t("publish.confirmTitle")}
              </h3>
              <p className="publish-confirm__body">
                {t("publish.confirmBody", { count: STUB_VINS.length })}
              </p>
              <div className="publish-confirm__actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={handleCancel}
                >
                  {t("publish.cancel")}
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={handleConfirm}
                >
                  {t("publish.confirmPublish", { count: STUB_VINS.length })}
                </button>
              </div>
            </section>
          )}

          {isPublishing && (
            <section
              className="card card--publishing"
              aria-labelledby="publishing-heading"
              aria-live="polite"
              aria-busy="true"
            >
              <h3 id="publishing-heading" className="publish-progress__title">
                {t("publish.publishingTitle")}
              </h3>
              <p className="publish-progress__subtitle">
                {t("publish.publishingSubtitle", {
                  count: STUB_VINS.length,
                  planId: STUB_PLAN_ID,
                })}
              </p>
              <div
                className="publish-progress__track"
                role="progressbar"
                aria-label={t("publish.publishingProgress")}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div className="publish-progress__bar" aria-hidden="true" />
              </div>
              <p className="publish-progress__hint">{t("publish.publishingHint")}</p>
            </section>
          )}
        </div>

        <aside className="sequence-sidebar" aria-label={t("publish.sidebarLabel")}>
          <div className="sidebar-card">
            <h3 className="sidebar-card__title">{t("publish.sidebarHistoryTitle")}</h3>
            {isPublished && publishState.phase === "published" ? (
              <dl className="sidebar-history-kv">
                <dt>{t("publish.opcenterSequenceId")}</dt>
                <dd className="mono">{publishState.response.opcenterSequenceId}</dd>
                <dt>{t("publish.publishedAt")}</dt>
                <dd className="mono">{publishState.response.publishedAt}</dd>
              </dl>
            ) : (
              <p className="sidebar-card__empty">{t("publish.sidebarHistoryEmpty")}</p>
            )}
          </div>
          <div className="sidebar-card">
            <h3 className="sidebar-card__title">{t("publish.sidebarAuthorityTitle")}</h3>
            <p className="sidebar-card__body">{t("publish.sidebarAuthorityBody")}</p>
          </div>
        </aside>
      </div>
    </main>
  );
}
