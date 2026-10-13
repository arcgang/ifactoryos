import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  certifyVehicle,
  getOverrideStatus,
  submitOverrideRequest,
} from "../services/api";
import type {
  CertifyResponse,
  GateResultRecord,
  GateType,
  HealthScore,
  PerGateResult,
} from "../services/api";
import { emitAdoptionEvent } from "../services/adoption";
import { useAuth } from "../hooks/useAuth";
import { ALL_GATE_TYPES, useGateResults } from "../hooks/useGateResults";
import { useHistoricalDistribution } from "../hooks/useHistoricalDistribution";

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

const OVERRIDE_POLL_MS = 5_000;

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
          : t("gateCommand.resultsReceived", { count, total })}
      </span>
      <div
        className="qs-cb-track"
        role="progressbar"
        aria-valuenow={count}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-label={t("gateCommand.resultsReceived", { count, total })}
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
// Manual override panel
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
// Main Gate Command page
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
  const [certificate, setCertificate] = useState<CertifyResponse | null>(null);
  const [overrideApproved, setOverrideApproved] = useState(false);

  // Emit GATE_COMMAND_OPEN adoption event on mount
  useEffect(() => {
    void emitAdoptionEvent({
      userId,
      role: "QUALITY_GATE_VALIDATOR",
      action: "GATE_COMMAND_OPEN",
      vin,
      timestamp: new Date().toISOString(),
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vin]); // userId intentionally omitted — emit once per vin, not on auth refresh

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
    try {
      const resp = await certifyVehicle({
        vin,
        gateResultsSnapshot: deduped,
        healthScore: score,
        validatorId: userId || "unknown",
      });
      setCertificate(resp);
      setCertifyState("success");

      void emitAdoptionEvent({
        userId,
        role: "QUALITY_GATE_VALIDATOR",
        action: "CERTIFY",
        vin,
        fabricTxHash: resp.fabricTxHash,
        timestamp: new Date().toISOString(),
      });
    } catch (err) {
      setCertifyError(
        err instanceof Error ? err.message : t("errors.serverError"),
      );
      setCertifyState("error");
    }
  }, [score, canCertify, vin, deduped, userId, t]);

  const elapsedSec = Math.floor(elapsedMs / 1000);

  // ---------------------------------------------------------------------------
  // Certificate success screen
  // ---------------------------------------------------------------------------

  if (certifyState === "success" && certificate) {
    return <CertificateStudioPanel cert={certificate} />;
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
            <button
              type="button"
              className="qs-btn qs-btn--certify"
              onClick={() => {
                void handleCertify();
              }}
              disabled={!canCertify}
              aria-disabled={!canCertify}
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
              {certifyState === "loading"
                ? t("gateCommand.loading")
                : t("gateCommand.certify")}
            </button>

            {certifyState === "error" && certifyError && (
              <p
                role="alert"
                className="qs-error-inline"
                data-testid="certify-error"
              >
                {certifyError}
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
