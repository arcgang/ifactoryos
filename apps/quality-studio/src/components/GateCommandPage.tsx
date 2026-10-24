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
import {
  issueCertificate,
  getOverrideStatus,
  submitOverrideRequest,
} from "../services/api";
import type {
  CertifyResponse,
  CertificateIssueResponse,
  GateResult,
  GateResultRecord,
  GateType,
  HealthScore,
  PerGateResult,
  StationCode,
  StationResult,
  GateResultSnapshot,
} from "../services/api";
import { useAuth } from "../hooks/useAuth";
import { ALL_GATE_TYPES, useGateResults } from "../hooks/useGateResults";
import { useHistoricalDistribution } from "../hooks/useHistoricalDistribution";
import { emitCertify, emitCertificateStudioOpen } from "../instrumentation/adoptionEvents";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const GATE_TYPE_LABELS: Record<GateType, string> = {
  dimensional: "DIM",
  leak: "LEAK",
  electrical: "ELEC",
  nvh: "NVH",
  adas: "ADAS",
};

const GATE_ICON: Record<GateType, string> = {
  dimensional: "📐",
  leak: "💧",
  electrical: "⚡",
  nvh: "🔊",
  adas: "🛡️",
};

const OVERRIDE_POLL_MS = 5_000;

const GATE_TYPE_TO_STATION_CODE: Record<GateType, StationCode> = {
  dimensional: "DIM",
  leak: "LEAK",
  electrical: "ELEC",
  nvh: "NVH",
  adas: "ADAS",
};

const GATE_RESULT_TO_STATION_RESULT: Record<GateResult, StationResult> = {
  pass: "PASS",
  conditional_pass: "CONDITIONAL",
  fail: "FAIL",
};

async function computeSha256Hex(payload: string): Promise<string> {
  const encoded = new TextEncoder().encode(payload);
  const hashBuffer = await crypto.subtle.digest("SHA-256", encoded);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, "0")).join("");
}

function generateUuid(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function buildGateResultSnapshot(
  results: GateResultRecord[],
  gateId: string,
): GateResultSnapshot {
  return {
    gateId,
    stationResults: results.map((r) => ({
      stationCode: GATE_TYPE_TO_STATION_CODE[r.gateType],
      result: GATE_RESULT_TO_STATION_RESULT[r.result],
      details:
        r.measurements.length > 0
          ? `${r.measurements[0].name}: ${r.measurements[0].value} ${r.measurements[0].unit}`
          : undefined,
    })),
  };
}

// ---------------------------------------------------------------------------
// Health score computation
// ---------------------------------------------------------------------------

export function computeHealthScore(results: GateResultRecord[]): HealthScore {
  const perGate: Partial<PerGateResult> = {};
  const seen = new Set<GateType>();

  for (const r of results) {
    if (!seen.has(r.gateType)) {
      seen.add(r.gateType);
      perGate[r.gateType] = r.result;
    }
  }

  const values = Object.values(perGate);
  let overall: "pass" | "conditional_pass" | "fail" = "pass";
  if (values.includes("fail")) overall = "fail";
  else if (values.includes("conditional_pass")) overall = "conditional_pass";

  return { overall, perGate: perGate as PerGateResult };
}

// ---------------------------------------------------------------------------
// Anomaly highlight (per-measurement, shown for conditional_pass results)
// ---------------------------------------------------------------------------

interface AnomalyHighlightProps {
  measurementId: string;
  name: string;
  unit: string;
  value: number;
  toleranceLow: number;
  toleranceHigh: number;
  reworkThresholdLow: number | null;
  reworkThresholdHigh: number | null;
  gateType: GateType;
}

function AnomalyHighlight({
  measurementId,
  name,
  unit,
  value,
  toleranceLow,
  toleranceHigh,
  reworkThresholdLow,
  reworkThresholdHigh,
  gateType,
}: AnomalyHighlightProps) {
  const { t } = useTranslation();
  const { data: dist, loading: distLoading } = useHistoricalDistribution(
    measurementId,
    gateType,
  );

  const rangeMin =
    reworkThresholdLow ?? toleranceLow - (toleranceHigh - toleranceLow);
  const rangeMax =
    reworkThresholdHigh ?? toleranceHigh + (toleranceHigh - toleranceLow);
  const rangeSpan = rangeMax - rangeMin || 1;

  const tolLoPct = ((toleranceLow - rangeMin) / rangeSpan) * 100;
  const tolHiPct = ((toleranceHigh - rangeMin) / rangeSpan) * 100;
  const valuePct = Math.min(
    100,
    Math.max(0, ((value - rangeMin) / rangeSpan) * 100),
  );

  const maxCount = dist
    ? Math.max(...dist.points.map((p) => p.count), 1)
    : 1;

  return (
    <section
      className="qs-anomaly-highlight"
      aria-labelledby={`anom-${measurementId}-title`}
      data-testid={`anomaly-highlight-${measurementId}`}
    >
      <h4 id={`anom-${measurementId}-title`} className="qs-anomaly-name">
        {name}
        <span
          className="qs-anomaly-badge"
          aria-label={t("gateCommand.conditionalPass")}
        >
          {t("gateCommand.conditionalPass")}
        </span>
      </h4>

      {/* Tolerance range bar */}
      <div
        className="qs-range-bar-wrap"
        aria-label={t("gateCommand.anomalyRangeLabel", {
          value,
          unit,
          low: toleranceLow,
          high: toleranceHigh,
        })}
      >
        <div className="qs-range-bar-track" role="img" aria-hidden="true">
          <div
            className="qs-range-tol-band"
            style={{
              left: `${tolLoPct}%`,
              width: `${tolHiPct - tolLoPct}%`,
            }}
          />
          {reworkThresholdLow !== null && (
            <div
              className="qs-range-rework-marker qs-range-rework-lo"
              style={{
                left: `${((reworkThresholdLow - rangeMin) / rangeSpan) * 100}%`,
              }}
            />
          )}
          {reworkThresholdHigh !== null && (
            <div
              className="qs-range-rework-marker qs-range-rework-hi"
              style={{
                left: `${((reworkThresholdHigh - rangeMin) / rangeSpan) * 100}%`,
              }}
            />
          )}
          <div
            className="qs-range-value-marker"
            style={{ left: `${valuePct}%` }}
            aria-hidden="true"
          />
        </div>
        <div className="qs-range-labels">
          <span className="qs-range-label-lo">
            {toleranceLow} {unit}
          </span>
          <span className="qs-range-label-actual qs-range-label-cond">
            {value} {unit}
          </span>
          <span className="qs-range-label-hi">
            {toleranceHigh} {unit}
          </span>
        </div>
      </div>

      {/* Historical distribution (stub) */}
      <div
        className="qs-dist-chart"
        aria-label={t("gateCommand.historicalDistributionLabel", { name })}
      >
        {distLoading ? (
          <div className="qs-dist-loading" aria-busy="true">
            {t("gateCommand.loading")}
          </div>
        ) : dist ? (
          <>
            <div className="qs-dist-bars" aria-hidden="true">
              {dist.points.map((pt) => (
                <div
                  key={pt.bucket}
                  className="qs-dist-bar-col"
                  style={{ height: `${(pt.count / maxCount) * 100}%` }}
                  title={`${pt.bucket} ${unit}: ${pt.count}`}
                />
              ))}
            </div>
            <p className="qs-dist-meta">
              {t("gateCommand.distMeta", {
                sampleCount: dist.sampleCount,
                p50: dist.p50,
                p95: dist.p95,
                unit,
              })}
            </p>
          </>
        ) : null}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Single gate card
// ---------------------------------------------------------------------------

interface GateCardProps {
  record: GateResultRecord;
}

function GateCard({ record }: GateCardProps) {
  const { t } = useTranslation();
  const label = GATE_TYPE_LABELS[record.gateType];
  const isConditional = record.result === "conditional_pass";
  const isFail = record.result === "fail";

  const resultClass = isConditional
    ? "qs-badge-cond"
    : isFail
      ? "qs-badge-fail"
      : "qs-badge-pass";

  const resultLabel = isConditional
    ? t("gateCommand.conditionalPass")
    : isFail
      ? t("gateCommand.fail")
      : t("gateCommand.pass");

  const anomalous = record.measurements.filter((m) => !m.withinTolerance);

  return (
    <article
      className={`qs-gate-card ${isConditional ? "qs-gate-card--cond" : isFail ? "qs-gate-card--fail" : "qs-gate-card--pass"}`}
      aria-label={`${t("gateCommand.gate")} ${label} — ${resultLabel}`}
      data-testid={`gate-card-${record.gateType}`}
    >
      <header className="qs-gate-card-header">
        <span className="qs-gate-card-type">{label}</span>
        <span className={`qs-badge ${resultClass}`} aria-label={resultLabel}>
          {resultLabel}
        </span>
      </header>
      <p className="qs-gate-card-id">{record.gateId}</p>
      <ul className="qs-gate-card-meas" aria-label={t("gateCommand.measurements")}>
        {record.measurements.map((m) => (
          <li
            key={m.measurementId}
            className={`qs-meas-row ${m.withinTolerance ? "" : "qs-meas-row--cond"}`}
          >
            <span className="qs-meas-name">{m.name}</span>
            <span className="qs-meas-value">
              {m.value} {m.unit}
            </span>
          </li>
        ))}
      </ul>
      <p className="qs-gate-card-ts">
        {t("gateCommand.measured")}:{" "}
        {new Date(record.measuredAt).toLocaleTimeString()}
        <br />
        {t("gateCommand.received")}:{" "}
        {new Date(record.receivedFromOpcenterAt).toLocaleTimeString()}
      </p>
      {isConditional && anomalous.length > 0 && (
        <div className="qs-gate-card-anomalies">
          {anomalous.map((m) => (
            <AnomalyHighlight
              key={m.measurementId}
              measurementId={m.measurementId}
              name={m.name}
              unit={m.unit}
              value={m.value}
              toleranceLow={m.toleranceLow}
              toleranceHigh={m.toleranceHigh}
              reworkThresholdLow={m.reworkThresholdLow}
              reworkThresholdHigh={m.reworkThresholdHigh}
              gateType={record.gateType}
            />
          ))}
        </div>
      )}
    </article>
  );
}

// ---------------------------------------------------------------------------
// Completeness indicator
// ---------------------------------------------------------------------------

interface CompletenessBarProps {
  received: Set<GateType>;
}

function CompletenessBar({ received }: CompletenessBarProps) {
  const { t } = useTranslation();
  const count = received.size;
  const total = ALL_GATE_TYPES.length;
  const pct = (count / total) * 100;
  const full = count === total;

  return (
    <div
      className={`qs-completeness-bar ${full ? "qs-completeness-bar--full" : ""}`}
      data-testid="completeness-bar"
    >
      <span className="qs-cb-label">
        {full
          ? t("gateCommand.allResultsReceived")
          : t("gateCommand.resultsReceived", { received: count, total })}
      </span>
      <div
        className="qs-cb-track"
        role="progressbar"
        aria-valuenow={count}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-label={t("gateCommand.resultsReceived", { received: count, total })}
      >
        <div
          className={`qs-cb-fill ${full ? "qs-cb-fill--full" : "qs-cb-fill--partial"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="qs-cb-gates" aria-label={t("gateCommand.gateStatusList")}>
        {ALL_GATE_TYPES.map((gt) => (
          <span
            key={gt}
            className={`qs-cb-gate ${received.has(gt) ? "qs-cb-gate--ok" : "qs-cb-gate--pending"}`}
            aria-label={`${GATE_TYPE_LABELS[gt]}: ${received.has(gt) ? t("gateCommand.pass") : t("gateCommand.pending")}`}
          >
            {GATE_TYPE_LABELS[gt]}
          </span>
        ))}
      </div>
    </div>
  );
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
// ManualOverrideModal — used by GateCompletenessCard
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
    fetch("/api/v1/supervisor/approval-request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ justification: justification.trim() }),
    })
      .then((res) => {
        if (res.ok) setApprovalStatus("approved");
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
// Health score banner
// ---------------------------------------------------------------------------

interface HealthBannerProps {
  score: HealthScore;
}

function HealthBanner({ score }: HealthBannerProps) {
  const { t } = useTranslation();
  const isPass = score.overall === "pass";
  const isCond = score.overall === "conditional_pass";

  const overallLabel = isPass
    ? t("gateCommand.pass")
    : isCond
      ? t("gateCommand.conditionalPass")
      : t("gateCommand.fail");

  const numericScore = isPass ? 95 : isCond ? 82 : 40;

  return (
    <section
      className={`qs-health-banner ${isPass ? "qs-health-banner--pass" : isCond ? "qs-health-banner--cond" : "qs-health-banner--fail"}`}
      role="region"
      aria-label={t("gateCommand.healthScore")}
      data-testid="health-banner"
    >
      <div className="qs-score-ring" aria-hidden="true">
        <span className="qs-score-num">{numericScore}</span>
        <span className="qs-score-denom">/100</span>
      </div>
      <div className="qs-health-info">
        <p className="qs-health-status">{overallLabel}</p>
        <div
          className="qs-gate-pills"
          role="list"
          aria-label={t("gateCommand.perGateResults")}
        >
          {ALL_GATE_TYPES.map((gt) => {
            const r = score.perGate[gt];
            if (!r) return null;
            return (
              <span
                key={gt}
                role="listitem"
                className={`qs-gate-pill ${r === "pass" ? "qs-gate-pill--pass" : r === "conditional_pass" ? "qs-gate-pill--cond" : "qs-gate-pill--fail"}`}
                data-testid={`per-gate-badge-${gt}`}
              >
                {GATE_TYPE_LABELS[gt]}:{" "}
                {r === "pass"
                  ? t("gateCommand.pass")
                  : r === "conditional_pass"
                    ? t("gateCommand.conditionalPass")
                    : t("gateCommand.fail")}
              </span>
            );
          })}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Manual override panel (used by full GateCommandPage)
// ---------------------------------------------------------------------------

interface OverridePanelProps {
  vin: string;
  validatorId: string;
  onApproved: () => void;
}

function OverridePanel({ vin, validatorId, onApproved }: OverridePanelProps) {
  const { t } = useTranslation();
  const [justification, setJustification] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [awaiting, setAwaiting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onApprovedRef = useRef(onApproved);
  onApprovedRef.current = onApproved;

  const stopPoll = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPoll, [stopPoll]);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!justification.trim()) return;
      setSubmitError(null);
      try {
        await submitOverrideRequest({ vin, justification, validatorId });
        setSubmitted(true);
        setAwaiting(true);

        pollRef.current = setInterval(() => {
          void (async () => {
            try {
              const status = await getOverrideStatus(vin);
              if (status.status === "approved") {
                stopPoll();
                setAwaiting(false);
                onApprovedRef.current();
              } else if (status.status === "rejected") {
                stopPoll();
                setAwaiting(false);
                setSubmitted(false);
                setSubmitError(t("gateCommand.overrideRejected"));
              }
            } catch {
              // keep polling on transient errors
            }
          })();
        }, OVERRIDE_POLL_MS);
      } catch (err) {
        setSubmitError(
          err instanceof Error ? err.message : t("errors.serverError"),
        );
      }
    },
    [justification, vin, validatorId, stopPoll, t],
  );

  return (
    <section
      className="qs-override-panel"
      aria-labelledby="override-panel-heading"
      data-testid="override-panel"
    >
      <h3 id="override-panel-heading">{t("gateCommand.manualOverride")}</h3>
      {!submitted ? (
        <form
          onSubmit={(e) => {
            void handleSubmit(e);
          }}
        >
          <div className="qs-form-field">
            <label htmlFor="override-justification">
              {t("gateCommand.overrideJustification")}
            </label>
            <textarea
              id="override-justification"
              value={justification}
              onChange={(e) => setJustification(e.target.value)}
              required
              rows={3}
              aria-required="true"
              data-testid="override-justification-input"
            />
          </div>
          {submitError && (
            <p role="alert" className="qs-error-inline">
              {submitError}
            </p>
          )}
          <button
            type="submit"
            className="qs-btn qs-btn--override"
            disabled={!justification.trim()}
          >
            {t("gateCommand.submitOverride")}
          </button>
        </form>
      ) : awaiting ? (
        <p
          className="qs-awaiting-supervisor"
          aria-live="polite"
          data-testid="awaiting-supervisor"
        >
          {t("gateCommand.awaitingSupervisor")}
        </p>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Certificate Studio panel
// ---------------------------------------------------------------------------

interface CertificateStudioPanelProps {
  cert: CertifyResponse;
}

export function CertificateStudioPanel({ cert }: CertificateStudioPanelProps) {
  const { t } = useTranslation();
  const { userId } = useAuth();

  // Emit CERTIFICATE_STUDIO_OPEN adoption event on mount
  useEffect(() => {
    void emitCertificateStudioOpen({
      userId: userId || "unknown",
      role: "QUALITY_GATE_VALIDATOR",
      vin: cert.vin,
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cert.vin]); // Emit once per vin, not on auth refresh

  return (
    <section
      className="qs-cert-panel"
      aria-labelledby="cert-panel-heading"
      data-testid="certificate-panel"
    >
      <span className="qs-cert-icon" aria-hidden="true">
        ✓
      </span>
      <h2 id="cert-panel-heading" className="qs-cert-title">
        {t("certificateStudio.digitalBirthCertificate")}
      </h2>

      <dl className="qs-cert-fields">
        <div className="qs-cert-field">
          <dt>{t("gateCommand.vinLabel")}</dt>
          <dd data-testid="cert-vin">{cert.vin}</dd>
        </div>
        <div className="qs-cert-field">
          <dt>{t("certificateStudio.issuedAt")}</dt>
          <dd data-testid="cert-issued-at">
            {new Date(cert.issuedAt).toISOString()}
          </dd>
        </div>
        <div className="qs-cert-field">
          <dt>{t("gateCommand.validatorId")}</dt>
          <dd data-testid="cert-validator-id">{cert.validatorId}</dd>
        </div>
        <div className="qs-cert-field">
          <dt>{t("gateCommand.healthScore")}</dt>
          <dd data-testid="cert-health-score">{cert.healthScore.overall}</dd>
        </div>
      </dl>

      <div className="qs-cert-hash-box" data-testid="cert-fabric-hash">
        <span className="qs-cert-hash-label">
          {t("certificateStudio.fabricHash")}
        </span>
        <code>{cert.fabricTxHash}</code>
      </div>

      <div className="qs-cert-gate-snapshot">
        <h3>{t("gateCommand.gateSnapshot")}</h3>
        <ul aria-label={t("gateCommand.gateSnapshot")}>
          {cert.gateResultsSnapshot.map((r) => (
            <li
              key={r.gateResultId}
              data-testid={`cert-gate-${r.gateType}`}
            >
              {GATE_TYPE_LABELS[r.gateType]}: {r.result}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// CertificateDetailCard — shown after successful issueCertificate call
// ---------------------------------------------------------------------------

interface CertificateDetailCardProps {
  response: CertificateIssueResponse;
  vin: string;
  validatorId: string;
  issuedAt: string;
  gateResultSnapshot: GateResultSnapshot;
  healthScore: number;
  onVerify?: () => void;
}

export function CertificateDetailCard({
  response,
  vin,
  validatorId,
  issuedAt,
  gateResultSnapshot,
  healthScore,
  onVerify,
}: CertificateDetailCardProps) {
  const { t } = useTranslation();

  return (
    <section
      className="qs-cert-detail-card"
      aria-labelledby="cert-detail-heading"
      data-testid="certificate-detail-card"
    >
      <header className="qs-cert-detail-header">
        <span className="qs-cert-detail-icon" aria-hidden="true">✓</span>
        <div className="qs-cert-detail-header-main">
          <h2 id="cert-detail-heading" className="qs-cert-detail-title">
            {t("certificateStudio.digitalBirthCertificate")}
          </h2>
          <p className="qs-cert-detail-subtitle">
            {response.idempotent
              ? t("certificateStudio.idempotentReplay")
              : t("certificateStudio.anchoredSuccess")}
          </p>
        </div>
        <span
          className="qs-cert-status-pill"
          aria-label={t("certificateStudio.anchoredStatus")}
        >
          {t("certificateStudio.anchored")}
        </span>
      </header>

      <dl className="qs-cert-detail-fields">
        <div className="qs-cert-detail-field">
          <dt>{t("gateCommand.vinLabel")}</dt>
          <dd className="qs-cert-detail-mono" data-testid="cert-detail-vin">
            {vin}
          </dd>
        </div>
        <div className="qs-cert-detail-field">
          <dt>{t("certificateStudio.certificateId")}</dt>
          <dd className="qs-cert-detail-mono" data-testid="cert-detail-id">
            {response.certificateId}
          </dd>
        </div>
        <div className="qs-cert-detail-field">
          <dt>{t("certificateStudio.issuedAt")}</dt>
          <dd data-testid="cert-detail-issued-at">
            {new Date(issuedAt).toISOString()}
          </dd>
        </div>
        <div className="qs-cert-detail-field">
          <dt>{t("certificateStudio.anchoredAt")}</dt>
          <dd data-testid="cert-detail-anchored-at">
            {new Date(response.anchoredAt).toISOString()}
          </dd>
        </div>
        <div className="qs-cert-detail-field">
          <dt>{t("gateCommand.validatorId")}</dt>
          <dd data-testid="cert-detail-validator">{validatorId}</dd>
        </div>
        <div className="qs-cert-detail-field">
          <dt>{t("gateCommand.healthScore")}</dt>
          <dd data-testid="cert-detail-health-score">{healthScore.toFixed(1)}</dd>
        </div>
      </dl>

      <div
        className="qs-cert-detail-fabric-block"
        aria-label={t("certificateStudio.fabricAnchorDetails")}
      >
        <h3 className="qs-cert-detail-fabric-title">
          {t("certificateStudio.fabricAnchor")}
        </h3>
        <div className="qs-cert-detail-fabric-row">
          <span className="qs-cert-detail-fabric-key">
            {t("certificateStudio.fabricTransactionId")}
          </span>
          <code
            className="qs-cert-detail-fabric-val"
            data-testid="cert-detail-fabric-tx-id"
          >
            {response.fabricTransactionId}
          </code>
        </div>
        <div className="qs-cert-detail-fabric-row">
          <span className="qs-cert-detail-fabric-key">
            {t("certificateStudio.fabricBlockNumber")}
          </span>
          <code
            className="qs-cert-detail-fabric-val"
            data-testid="cert-detail-fabric-block"
          >
            {response.fabricBlockNumber}
          </code>
        </div>
        <div className="qs-cert-detail-fabric-row">
          <span className="qs-cert-detail-fabric-key">
            {t("certificateStudio.certificateHash")}
          </span>
          <code
            className="qs-cert-detail-fabric-val"
            data-testid="cert-detail-hash"
          >
            {response.certificateHash}
          </code>
        </div>
      </div>

      <div className="qs-cert-detail-snapshot">
        <h3>{t("gateCommand.gateSnapshot")}</h3>
        <ul
          aria-label={t("gateCommand.gateSnapshot")}
          className="qs-cert-detail-station-list"
        >
          {gateResultSnapshot.stationResults.map((sr) => (
            <li
              key={sr.stationCode}
              className={`qs-cert-station-row qs-cert-station-row--${sr.result.toLowerCase()}`}
              data-testid={`cert-detail-station-${sr.stationCode}`}
            >
              <span className="qs-cert-station-code">{sr.stationCode}</span>
              <span className="qs-cert-station-result">{sr.result}</span>
              {sr.details && (
                <span className="qs-cert-station-detail">{sr.details}</span>
              )}
            </li>
          ))}
        </ul>
      </div>

      {onVerify && (
        <div className="qs-cert-detail-actions">
          <button
            type="button"
            className="qs-btn qs-btn--verify"
            onClick={onVerify}
            aria-label={t("certificateStudio.verifyAriaLabel")}
            data-testid="verify-tamper-button"
          >
            {t("certificateStudio.verifyTamperEvidence")}
          </button>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// GateCompletenessCard — standalone completeness + certify card (from main)
// ---------------------------------------------------------------------------

interface GateCompletenessCardProps {
  vin: string;
}

export function GateCompletenessCard({ vin }: GateCompletenessCardProps): ReactNode {
  const { t } = useTranslation();
  const {
    phase,
    results,
    receivedGateTypes,
    missingGateTypes,
    elapsedMs,
  } = useGateResults(vin);
  const [showOverrideModal, setShowOverrideModal] = useState(false);
  const [certified, setCertified] = useState(false);
  const certifyTooltipId = useId();
  const timeoutAlertId = useId();

  // Emit adoption event on mount
  useEffect(() => {
    const event = { action: "GATE_COMMAND_OPEN", vin, timestamp: new Date().toISOString() };
    window.dispatchEvent(new CustomEvent("ifos:adoption", { detail: event }));
  }, [vin]);

  function handleOpenOverride() {
    const event = { action: "MANUAL_OVERRIDE_INITIATED", vin, timestamp: new Date().toISOString() };
    window.dispatchEvent(new CustomEvent("ifos:adoption", { detail: event }));
    setShowOverrideModal(true);
  }

  function handleCertify() {
    setCertified(true);
  }

  function handleCertifyWithOverride(_justification: string) {
    setShowOverrideModal(false);
    setCertified(true);
  }

  if (phase === "loading") {
    return (
      <div className="gate-card" role="status" aria-label={t("gateCommand.loading")}>
        <span className="gate-spinner" aria-hidden="true" />
        <p>{t("gateCommand.loading")}</p>
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div className="gate-card gate-card--error" role="alert">
        <p>{t("gateCommand.error")}</p>
      </div>
    );
  }

  // Build byType map from results
  const byType: Partial<Record<GateType, GateResultRecord>> = {};
  for (const r of results) {
    if (!byType[r.gateType]) byType[r.gateType] = r;
  }

  const receivedCount = receivedGateTypes.size;
  const isComplete = phase === "populated";
  const timedOut = phase === "timeout";
  const timedOutTypes = timedOut ? missingGateTypes : [];
  const elapsedSeconds = Math.floor(elapsedMs / 1000);
  const timeoutSeconds = 600; // 10 minutes

  const completenessPercent = Math.round((receivedCount / ALL_GATE_TYPES.length) * 100);

  const pendingTypeLabels = missingGateTypes
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
// Full Gate Command page (original implementation)
// ---------------------------------------------------------------------------

interface GateCommandPageProps {
  vin: string;
}

export function GateCommandPage({ vin }: GateCommandPageProps) {
  const { t } = useTranslation();
  const { userId } = useAuth();
  const {
    phase,
    results,
    receivedGateTypes,
    missingGateTypes,
    error,
    elapsedMs,
    reload,
  } = useGateResults(vin);

  const [certifyState, setCertifyState] = useState<
    "idle" | "loading" | "success" | "error"
  >("idle");
  const [certifyError, setCertifyError] = useState<string | null>(null);
  const [certifyErrorCode, setCertifyErrorCode] = useState<number | null>(null);
  const [issuedCertificate, setIssuedCertificate] = useState<CertificateIssueResponse | null>(null);
  const [issuedAt, setIssuedAt] = useState<string>("");
  const [issuedSnapshot, setIssuedSnapshot] = useState<GateResultSnapshot | null>(null);
  const [certifyCountdown, setCertifyCountdown] = useState<number>(60);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [overrideApproved, setOverrideApproved] = useState(false);

  const stopCountdown = useCallback(() => {
    if (countdownRef.current !== null) {
      clearInterval(countdownRef.current);
      countdownRef.current = null;
    }
  }, []);

  useEffect(() => stopCountdown, [stopCountdown]);

  const canCertify =
    (phase === "populated" || overrideApproved) &&
    certifyState === "idle";

  const score = results.length > 0 ? computeHealthScore(results) : null;

  // Deduplicated gate records: one per gateType (first received per type)
  const deduped = ALL_GATE_TYPES.flatMap((gt) => {
    const found = results.find((r) => r.gateType === gt);
    return found ? [found] : [];
  });

  const handleCertify = useCallback(async () => {
    if (!score || !canCertify) return;
    setCertifyState("loading");
    setCertifyError(null);
    setCertifyErrorCode(null);
    setCertifyCountdown(60);

    countdownRef.current = setInterval(() => {
      setCertifyCountdown((prev) => {
        if (prev <= 1) {
          stopCountdown();
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    const certificateId = generateUuid();
    const now = new Date().toISOString();
    const numericHealth =
      score.overall === "pass"
        ? 95.0
        : score.overall === "conditional_pass"
          ? 82.0
          : 40.0;

    const gateId = deduped.length > 0 ? deduped[0].gateId : "GATE-FINAL";
    const snapshot = buildGateResultSnapshot(deduped, gateId);

    const canonicalPayload = JSON.stringify({
      certificateId,
      vin,
      gateResultSnapshot: snapshot,
      healthScore: numericHealth,
      validatorId: userId || "unknown",
      issuedAt: now,
    });

    try {
      const certHash = await computeSha256Hex(canonicalPayload);

      const { response } = await issueCertificate({
        certificateId,
        vin,
        gateResultSnapshot: snapshot,
        healthScore: numericHealth,
        validatorId: userId || "unknown",
        issuedAt: now,
        certificateHash: certHash,
        sourceSystem: "quality-studio",
      });

      stopCountdown();
      setIssuedCertificate(response);
      setIssuedAt(now);
      setIssuedSnapshot(snapshot);
      setCertifyState("success");

      void emitCertify({
        userId: userId || "unknown",
        role: "QUALITY_GATE_VALIDATOR",
        vin,
        fabricTxHash: response.fabricTransactionId,
      });
    } catch (err) {
      stopCountdown();
      if (err instanceof Error) {
        // Try parse status from ApiError
        const apiErr = err as { status?: number };
        const status = apiErr.status;
        setCertifyErrorCode(status ?? null);
        if (status === 504) {
          setCertifyError(t("certificateStudio.error504"));
        } else if (status === 409) {
          setCertifyError(t("certificateStudio.error409"));
        } else if (status === 403) {
          setCertifyError(t("certificateStudio.error403"));
        } else {
          setCertifyError(err.message || t("errors.serverError"));
        }
      } else {
        setCertifyError(t("errors.serverError"));
      }
      setCertifyState("error");
    }
  }, [score, canCertify, vin, deduped, userId, t, stopCountdown]);

  const elapsedSec = Math.floor(elapsedMs / 1000);

  // ---------------------------------------------------------------------------
  // Certificate success screen
  // ---------------------------------------------------------------------------

  if (certifyState === "success" && issuedCertificate && issuedSnapshot) {
    return (
      <CertificateDetailCard
        response={issuedCertificate}
        vin={vin}
        validatorId={userId || "unknown"}
        issuedAt={issuedAt}
        gateResultSnapshot={issuedSnapshot}
        healthScore={
          score?.overall === "pass"
            ? 95.0
            : score?.overall === "conditional_pass"
              ? 82.0
              : 40.0
        }
      />
    );
  }

  return (
    <div className="qs-gate-command" data-testid="gate-command-page">
      {/* Page header */}
      <header className="qs-gc-header">
        <div className="qs-gc-vin">
          <small className="qs-label-upper">{t("gateCommand.vinLabel")}</small>
          <strong className="qs-vin-display">{vin}</strong>
        </div>
        {phase !== "error" && (
          <div
            className={`qs-gc-timer ${elapsedSec <= 10 ? "qs-gc-timer--ok" : "qs-gc-timer--warn"}`}
            aria-live="polite"
            aria-label={t("gateCommand.timeSinceFinalGate", {
              seconds: elapsedSec,
            })}
          >
            <small className="qs-label-upper">
              {t("gateCommand.timeSinceFinalGate", { seconds: elapsedSec })}
            </small>
            <strong className="qs-timer-value">
              {String(Math.floor(elapsedSec / 60)).padStart(2, "0")}:
              {String(elapsedSec % 60).padStart(2, "0")}
            </strong>
          </div>
        )}
      </header>

      {/* Loading state */}
      {phase === "loading" && (
        <div
          className="qs-loading-banner"
          role="status"
          aria-live="polite"
          data-testid="loading-state"
        >
          <p>{t("gateCommand.loading")}</p>
          <div className="qs-skeleton-gates" aria-hidden="true">
            {ALL_GATE_TYPES.map((gt) => (
              <div key={gt} className="qs-skeleton qs-skeleton--gate" />
            ))}
          </div>
        </div>
      )}

      {/* Error state */}
      {phase === "error" && (
        <div
          className="qs-error-box"
          role="alert"
          aria-live="assertive"
          data-testid="error-state"
        >
          <span className="qs-error-icon" aria-hidden="true">
            ⚠
          </span>
          <h2>{t("gateCommand.error")}</h2>
          <p>{error}</p>
          <button
            type="button"
            className="qs-btn qs-btn--retry"
            onClick={reload}
          >
            {t("defectDashboard.retry")}
          </button>
        </div>
      )}

      {/* Partial state — waiting for all gate results */}
      {phase === "partial" && (
        <>
          <CompletenessBar received={receivedGateTypes} />
          <div
            className="qs-empty-box"
            role="status"
            aria-live="polite"
            data-testid="partial-state"
          >
            <span className="qs-empty-icon" aria-hidden="true">
              ◯
            </span>
            <h2>{t("gateCommand.empty")}</h2>
            <p>
              {t("gateCommand.certifyDisabledTooltip", {
                gates: missingGateTypes
                  .map((g) => GATE_TYPE_LABELS[g])
                  .join(", "),
              })}
            </p>
          </div>
        </>
      )}

      {/* Timeout state */}
      {phase === "timeout" && (
        <>
          <CompletenessBar received={receivedGateTypes} />
          <div
            className="qs-error-box"
            role="alert"
            aria-live="assertive"
            data-testid="timeout-state"
          >
            <span className="qs-error-icon" aria-hidden="true">
              ⚠
            </span>
            <h2>{t("gateCommand.timeoutTitle")}</h2>
            <p>
              {t("gateCommand.timeoutMessage", {
                gates: missingGateTypes
                  .map((g) => GATE_TYPE_LABELS[g])
                  .join(", "),
              })}
            </p>
          </div>
          {!overrideApproved && (
            <OverridePanel
              vin={vin}
              validatorId={userId || "unknown"}
              onApproved={() => setOverrideApproved(true)}
            />
          )}
        </>
      )}

      {/* Populated state — all 5 results received (or override approved) */}
      {(phase === "populated" ||
        (phase === "timeout" && overrideApproved)) && (
        <>
          <CompletenessBar received={receivedGateTypes} />

          {score && <HealthBanner score={score} />}

          <h2 className="qs-section-heading">
            {t("gateCommand.gateResults")}
          </h2>
          <div
            className="qs-gate-cards"
            role="list"
            aria-label={t("gateCommand.gateResults")}
            data-testid="gate-cards"
          >
            {deduped.map((r) => (
              <GateCard key={r.gateResultId} record={r} />
            ))}
          </div>

          {/* Certify action bar */}
          <div className="qs-certify-bar" data-testid="certify-bar">
            {certifyState === "loading" ? (
              <div
                className="qs-certify-in-progress"
                role="status"
                aria-live="polite"
                data-testid="certify-in-progress"
              >
                <span className="gate-spinner" aria-hidden="true" />
                <span className="qs-certify-countdown-label">
                  {t("certificateStudio.anchoringInProgress", {
                    seconds: certifyCountdown,
                  })}
                </span>
              </div>
            ) : (
              <button
                type="button"
                className="qs-btn qs-btn--certify"
                onClick={() => {
                  void handleCertify();
                }}
                disabled={!canCertify}
                aria-disabled={!canCertify}
                aria-label={t("certificateStudio.certifyAriaLabel", { vin })}
                title={
                  !canCertify
                    ? t("gateCommand.certifyDisabledTooltip", {
                        gates: missingGateTypes
                          .map((g) => GATE_TYPE_LABELS[g])
                          .join(", "),
                      })
                    : undefined
                }
                data-testid="certify-button"
              >
                {t("gateCommand.certify")}
              </button>
            )}

            {certifyState === "error" && certifyError && (
              <div
                role="alert"
                className="qs-certify-error"
                data-testid="certify-error"
              >
                <p className="qs-error-inline">{certifyError}</p>
                {certifyErrorCode === 504 && (
                  <button
                    type="button"
                    className="qs-btn qs-btn--retry"
                    onClick={() => {
                      setCertifyState("idle");
                      setCertifyError(null);
                      setCertifyErrorCode(null);
                    }}
                    data-testid="certify-retry-button"
                  >
                    {t("defectDashboard.retry")}
                  </button>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
