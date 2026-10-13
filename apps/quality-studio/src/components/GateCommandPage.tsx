import {
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { useGateResults } from "../hooks/useGateResults";
import type { GateType } from "../types/gateResults";
import { ALL_GATE_TYPES } from "../types/gateResults";

// ---------------------------------------------------------------------------
// Adoption events
// ---------------------------------------------------------------------------

function emitAdoptionEvent(action: string, vin: string): void {
  const event = {
    action,
    vin,
    timestamp: new Date().toISOString(),
  };
  window.dispatchEvent(new CustomEvent("ifos:adoption", { detail: event }));
}

// ---------------------------------------------------------------------------
// Focus trap helper for the modal
// ---------------------------------------------------------------------------

function trapFocus(container: HTMLElement, e: KeyboardEvent<HTMLElement>): void {
  const focusable = Array.from(
    container.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((el) => !el.hasAttribute("disabled"));
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey) {
    if (document.activeElement === first) {
      e.preventDefault();
      last.focus();
    }
  } else {
    if (document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
}

// ---------------------------------------------------------------------------
// Gate icons / labels
// ---------------------------------------------------------------------------

const GATE_ICON: Record<GateType, string> = {
  dimensional: "📐",
  leak: "💧",
  electrical: "⚡",
  nvh: "🔊",
  adas: "🛡️",
};

// ---------------------------------------------------------------------------
// ManualOverrideModal
// ---------------------------------------------------------------------------

interface ManualOverrideModalProps {
  onClose: () => void;
  onCertifyWithOverride: (justification: string) => void;
}

function ManualOverrideModal({
  onClose,
  onCertifyWithOverride,
}: ManualOverrideModalProps): ReactNode {
  const { t } = useTranslation();
  const titleId = useId();
  const [justification, setJustification] = useState("");
  const [justificationError, setJustificationError] = useState<string | null>(null);
  const [approvalStatus, setApprovalStatus] = useState<
    "idle" | "pending" | "approved"
  >("idle");
  const dialogRef = useRef<HTMLDivElement>(null);
  const firstFocusRef = useRef<HTMLTextAreaElement>(null);

  // Focus first element on mount
  useEffect(() => {
    firstFocusRef.current?.focus();
  }, []);

  // Close on Escape
  useEffect(() => {
    function handleKeyDown(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  function handleKeyDownTrap(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Tab" && dialogRef.current) {
      trapFocus(dialogRef.current, e);
    }
  }

  function handleRequestApproval() {
    if (justification.trim().length < 20) {
      setJustificationError(t("gateCommand.justificationTooShort"));
      return;
    }
    setJustificationError(null);
    setApprovalStatus("pending");

    // Call supervisor approval endpoint; if not yet deployed fall back to pending state.
    // US-018 endpoint — optimistic: assume the call resolves to approved within 1s for demo.
    // In production this is a real POST; we fire-and-forget the "pending" spinner here.
    fetch("/api/v1/supervisor/approval-request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ justification: justification.trim() }),
    })
      .then((res) => {
        if (res.ok) setApprovalStatus("approved");
        // Non-2xx: stay pending — supervisor has not yet responded
      })
      .catch(() => {
        // US-018 endpoint not yet deployed — stay in pending state per spec
      });
  }

  function handleCertifyWithOverride() {
    if (approvalStatus !== "approved") return;
    onCertifyWithOverride(justification.trim());
  }

  const justificationLabelId = useId();
  const justificationDescId = useId();

  return (
    <div
      className="gate-modal-overlay"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="gate-modal"
        onKeyDown={handleKeyDownTrap}
      >
        <div className="gate-modal__header">
          <span className="gate-modal__warning-icon" aria-hidden="true">⚠️</span>
          <div>
            <h2 id={titleId} className="gate-modal__title">
              {t("gateCommand.overrideModalTitle")}
            </h2>
            <p className="gate-modal__subtitle">
              {t("gateCommand.overrideModalSubtitle")}
            </p>
          </div>
        </div>

        <div className="gate-modal__field">
          <label id={justificationLabelId} htmlFor="override-justification" className="gate-modal__label">
            {t("gateCommand.overrideJustificationLabel")}
            <span aria-hidden="true"> *</span>
          </label>
          <textarea
            id="override-justification"
            ref={firstFocusRef}
            aria-labelledby={justificationLabelId}
            aria-describedby={justificationDescId}
            aria-required="true"
            className={`gate-modal__textarea${justificationError ? " gate-modal__textarea--error" : ""}`}
            rows={4}
            value={justification}
            onChange={(e) => {
              setJustification(e.target.value);
              if (justificationError && e.target.value.trim().length >= 20) {
                setJustificationError(null);
              }
            }}
            placeholder={t("gateCommand.overrideJustificationPlaceholder")}
            minLength={20}
          />
          <span id={justificationDescId} className="gate-modal__char-hint">
            {justification.trim().length}/20 {t("gateCommand.justificationMinChars")}
          </span>
          {justificationError && (
            <span role="alert" className="gate-modal__field-error">
              {justificationError}
            </span>
          )}
        </div>

        <div className="gate-modal__approval-row">
          <span aria-hidden="true" className="gate-modal__approval-icon">🔒</span>
          <p className="gate-modal__approval-text">
            <strong>{t("gateCommand.supervisorApprovalRequired")}</strong>{" "}
            {t("gateCommand.supervisorApprovalNote")}
          </p>
        </div>

        <div className="gate-modal__footer">
          <button type="button" className="gate-btn gate-btn--outline" onClick={onClose}>
            {t("gateCommand.cancel")}
          </button>

          {approvalStatus === "idle" && (
            <button
              type="button"
              className="gate-btn gate-btn--amber"
              onClick={handleRequestApproval}
            >
              {t("gateCommand.requestSupervisorApproval")}
            </button>
          )}

          {approvalStatus === "pending" && (
            <button type="button" className="gate-btn gate-btn--amber" disabled aria-disabled="true">
              <span className="gate-spinner" aria-hidden="true" />
              {t("gateCommand.pendingApproval")}
            </button>
          )}

          {approvalStatus === "approved" && (
            <button
              type="button"
              className="gate-btn gate-btn--success"
              onClick={handleCertifyWithOverride}
            >
              {t("gateCommand.certifyWithOverride")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// GateCompletenessCard
// ---------------------------------------------------------------------------

interface GateCompletenessCardProps {
  vin: string;
}

export function GateCompletenessCard({ vin }: GateCompletenessCardProps): ReactNode {
  const { t } = useTranslation();
  const gateState = useGateResults(vin);
  const [showOverrideModal, setShowOverrideModal] = useState(false);
  const [certified, setCertified] = useState(false);
  const certifyTooltipId = useId();
  const timeoutAlertId = useId();

  // Emit adoption event on mount
  useEffect(() => {
    emitAdoptionEvent("GATE_COMMAND_OPEN", vin);
  }, [vin]);

  function handleOpenOverride() {
    emitAdoptionEvent("MANUAL_OVERRIDE_INITIATED", vin);
    setShowOverrideModal(true);
  }

  function handleCertify() {
    setCertified(true);
  }

  function handleCertifyWithOverride(_justification: string) {
    setShowOverrideModal(false);
    setCertified(true);
  }

  if (gateState.status === "loading") {
    return (
      <div className="gate-card" role="status" aria-label={t("gateCommand.loading")}>
        <span className="gate-spinner" aria-hidden="true" />
        <p>{t("gateCommand.loading")}</p>
      </div>
    );
  }

  if (gateState.status === "error") {
    return (
      <div className="gate-card gate-card--error" role="alert">
        <p>{t("gateCommand.error")}</p>
        <p className="gate-error-detail">{gateState.message}</p>
      </div>
    );
  }

  const { byType, receivedCount, isComplete, timedOut, timedOutTypes, elapsedSeconds, timeoutSeconds } =
    gateState;

  const completenessPercent = Math.round((receivedCount / ALL_GATE_TYPES.length) * 100);

  const pendingTypes = ALL_GATE_TYPES.filter((gt) => !byType[gt]);
  const pendingTypeLabels = pendingTypes
    .map((gt) => t(`gateCommand.gateType.${gt}`))
    .join(", ");

  const certifyDisabled = !isComplete || certified;

  function formatElapsed(seconds: number): string {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  return (
    <>
      <section
        className="gate-card"
        aria-label={t("gateCommand.completenessCardLabel")}
      >
        <header className="gate-card__header">
          <h2 className="gate-card__title">{t("gateCommand.healthScoreCompleteness")}</h2>
          <p className="gate-card__subtitle">{t("gateCommand.certifyRequiresAll5")}</p>
        </header>

        {/* Completeness progress */}
        <div
          className="gate-completeness"
          aria-live="polite"
          aria-label={t("gateCommand.completenessAriaLabel", {
            received: receivedCount,
            total: ALL_GATE_TYPES.length,
          })}
        >
          {isComplete ? (
            <span className="gate-completeness__check" aria-hidden="true">✓</span>
          ) : (
            <span
              className={`gate-completeness__percent${timedOut ? " gate-completeness__percent--timeout" : ""}`}
              aria-hidden="true"
            >
              {completenessPercent}%
            </span>
          )}

          <div className="gate-completeness__bar-wrap">
            <p className="gate-completeness__label">
              {isComplete
                ? t("gateCommand.healthScoreComplete")
                : t("gateCommand.resultsReceived", {
                    received: receivedCount,
                    total: ALL_GATE_TYPES.length,
                  })}
            </p>
            <div
              className="gate-progress"
              role="progressbar"
              aria-valuenow={completenessPercent}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={t("gateCommand.completenessProgress", {
                percent: completenessPercent,
              })}
            >
              <div
                className={`gate-progress__fill${
                  isComplete
                    ? " gate-progress__fill--complete"
                    : timedOut
                    ? " gate-progress__fill--timeout"
                    : " gate-progress__fill--partial"
                }`}
                style={{ width: `${completenessPercent}%` }}
              />
            </div>
            {!isComplete && (
              <p className="gate-completeness__caption">
                {timedOut
                  ? t("gateCommand.certifyBlockedTimeout")
                  : t("gateCommand.certifyBlockedPending")}
              </p>
            )}
          </div>
        </div>

        {/* Gate result rows */}
        <ul className="gate-results-grid" aria-label={t("gateCommand.gateResultsLabel")}>
          {ALL_GATE_TYPES.map((gateType) => {
            const record = byType[gateType];
            const isPending = !record;

            let rowClass = "gate-result-row";
            let badgeClass = "gate-badge";
            let statusLabel = "";

            if (isPending) {
              rowClass += " gate-result-row--pending";
              badgeClass += " gate-badge--pending";
              statusLabel = t("gateCommand.pending");
            } else if (record.result === "pass") {
              rowClass += " gate-result-row--pass";
              badgeClass += " gate-badge--pass";
              statusLabel = t("gateCommand.pass");
            } else if (record.result === "conditional_pass") {
              rowClass += " gate-result-row--conditional";
              badgeClass += " gate-badge--conditional";
              statusLabel = t("gateCommand.conditionalPass");
            } else {
              rowClass += " gate-result-row--fail";
              badgeClass += " gate-badge--fail";
              statusLabel = t("gateCommand.fail");
            }

            return (
              <li key={gateType} className={rowClass}>
                <span className="gate-result-row__icon" aria-hidden="true">
                  {GATE_ICON[gateType]}
                </span>
                <div className="gate-result-row__info">
                  <span className="gate-result-row__name">
                    {t(`gateCommand.gateType.${gateType}`)}
                  </span>
                  <span className="gate-result-row__meta">
                    {isPending
                      ? t("gateCommand.awaitingOpcenter")
                      : record.measuredAt.slice(0, 19).replace("T", " ")}
                  </span>
                </div>
                <span
                  className={badgeClass}
                  aria-label={`${t(`gateCommand.gateType.${gateType}`)}: ${statusLabel}`}
                >
                  {isPending && (
                    <span
                      className="gate-spinner gate-spinner--sm"
                      aria-label={t("gateCommand.pendingAriaLabel", {
                        gate: t(`gateCommand.gateType.${gateType}`),
                      })}
                    />
                  )}
                  {statusLabel}
                </span>
              </li>
            );
          })}
        </ul>

        {/* Timeout alert */}
        {timedOut && timedOutTypes.length > 0 && (
          <div
            id={timeoutAlertId}
            role="alert"
            className="gate-timeout-alert"
          >
            <span className="gate-timeout-alert__icon" aria-hidden="true">🚨</span>
            <div className="gate-timeout-alert__body">
              <p className="gate-timeout-alert__title">
                {t("gateCommand.timeoutTitle")}
              </p>
              <ul className="gate-timeout-alert__list">
                {timedOutTypes.map((gt) => (
                  <li key={gt}>
                    {t("gateCommand.timeoutStationDetail", {
                      gate: t(`gateCommand.gateType.${gt}`),
                      minutes: Math.round(timeoutSeconds / 60),
                    })}
                  </li>
                ))}
              </ul>
              <code className="gate-timeout-alert__elapsed">
                {t("gateCommand.elapsed")}: {formatElapsed(elapsedSeconds)}
              </code>
            </div>
          </div>
        )}

        {/* Certify section */}
        <div className="gate-certify">
          {certified ? (
            <div className="gate-certified" role="status">
              <span className="gate-certified__check" aria-hidden="true">✓</span>
              <div>
                <p className="gate-certified__title">{t("gateCommand.certifiedTitle")}</p>
                <p className="gate-certified__sub">{t("gateCommand.certifiedSubtitle")}</p>
              </div>
            </div>
          ) : (
            <>
              <div className="gate-certify__tooltip-wrap">
                <button
                  type="button"
                  className={`gate-btn gate-btn--primary${certifyDisabled ? " gate-btn--disabled" : ""}`}
                  aria-disabled={certifyDisabled ? "true" : undefined}
                  aria-describedby={certifyDisabled && !isComplete ? certifyTooltipId : undefined}
                  onClick={certifyDisabled ? undefined : handleCertify}
                  tabIndex={0}
                >
                  {t("gateCommand.certify")}
                </button>
                {!isComplete && (
                  <div
                    id={certifyTooltipId}
                    role="tooltip"
                    className="gate-tooltip"
                  >
                    {t("gateCommand.certifyDisabledTooltip", {
                      gates: pendingTypeLabels,
                    })}
                  </div>
                )}
              </div>

              {!isComplete && (
                <p className="gate-certify__note">
                  {t("gateCommand.awaitingGates", { gates: pendingTypeLabels })}
                </p>
              )}

              {timedOut && (
                <button
                  type="button"
                  className="gate-btn gate-btn--danger"
                  onClick={handleOpenOverride}
                  aria-describedby={timeoutAlertId}
                >
                  {t("gateCommand.manualOverride")}
                </button>
              )}
            </>
          )}
        </div>
      </section>

      {showOverrideModal && (
        <ManualOverrideModal
          onClose={() => setShowOverrideModal(false)}
          onCertifyWithOverride={handleCertifyWithOverride}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// GateCommandPage — top-level page component
// ---------------------------------------------------------------------------

interface GateCommandPageProps {
  vin?: string;
}

const DEMO_VIN = "WBA31AB09NCG47381";

export function GateCommandPage({ vin = DEMO_VIN }: GateCommandPageProps): ReactNode {
  const { t } = useTranslation();

  return (
    <main className="gate-command-page" aria-label={t("gateCommand.title")}>
      <h1 className="gate-command-page__heading">{t("gateCommand.title")}</h1>
      <GateCompletenessCard vin={vin} />
    </main>
  );
}
