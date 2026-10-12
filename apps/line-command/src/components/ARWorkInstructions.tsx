import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

// ---------------------------------------------------------------------------
// Contract types — matches GET /ar/instructions/{vin} LLD schema
// ---------------------------------------------------------------------------

export interface ArInstructionStep {
  stepNumber: number;
  description: string;
  torqueSpecNm: number;
  torqueToleranceNm: number;
  fastenerPointId: string;
  isVariantDelta?: boolean;
  variantDeltaNote?: string;
  holographicAssetUrl?: string;
}

export interface ArInstructionResponse {
  vin: string;
  stationId: string;
  optionCodes: string[];
  variantCode: string;
  isRareVariant: boolean;
  steps: ArInstructionStep[];
  instructionVersion: string;
  fetchedAt: string;
  fallbackPdfQrUrl?: string;
}

export interface ArInstructionFallbackResponse {
  vin: string;
  stationId: string;
  fallbackPdfQrUrl: string;
  failureLoggedAt: string;
  correlationId: string;
}

// ---------------------------------------------------------------------------
// Internal sub-components
// ---------------------------------------------------------------------------

function VariantDeltaBadge({ note }: { note?: string }) {
  const { t } = useTranslation();
  return (
    <span
      className="ar-delta-badge"
      role="status"
      aria-label={t("ar_workbench.variant_delta_badge_label")}
      title={note}
    >
      {t("ar_workbench.variant_delta_badge")}
    </span>
  );
}

function StepRow({ step }: { step: ArInstructionStep }) {
  const { t } = useTranslation();
  const isDelta = step.isVariantDelta === true;

  return (
    <li
      className={`ar-step-row${isDelta ? " ar-step-row--delta" : ""}`}
      aria-label={t("ar_workbench.step_label", { number: step.stepNumber })}
    >
      <div className="ar-step-num" aria-hidden="true">
        {isDelta ? "Δ" : step.stepNumber}
      </div>
      <div className="ar-step-body">
        <div className="ar-step-title">
          {step.stepNumber}. {step.description}
          {isDelta && <VariantDeltaBadge note={step.variantDeltaNote} />}
        </div>
        <div className="ar-step-torque">
          {t("ar_workbench.torque_spec", {
            spec: step.torqueSpecNm,
            tol: step.torqueToleranceNm,
          })}
        </div>
        {isDelta && step.variantDeltaNote && (
          <div className="ar-step-delta-note" aria-live="polite">
            {step.variantDeltaNote}
          </div>
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Fallback panel (504 / client-side 10s timeout)
// ---------------------------------------------------------------------------

interface FallbackPanelProps {
  vin: string;
  stationId: string;
  fallbackPdfQrUrl: string;
  correlationId?: string;
  failureLoggedAt?: string;
}

function FallbackPanel({
  vin,
  stationId,
  fallbackPdfQrUrl,
  correlationId,
  failureLoggedAt,
}: FallbackPanelProps) {
  const { t } = useTranslation();

  return (
    <div className="ar-fallback" role="alert" aria-live="assertive">
      <h3 className="ar-fallback-title">{t("ar_workbench.fallback_title")}</h3>
      <p className="ar-fallback-desc">{t("ar_workbench.fallback_desc")}</p>

      <div className="ar-fallback-vin" aria-label={t("ar_workbench.vin_label")}>
        {vin}
      </div>

      <p className="ar-fallback-station">
        {t("ar_workbench.station_label")}: <strong>{stationId}</strong>
      </p>

      {fallbackPdfQrUrl && (
        <div className="ar-fallback-qr">
          <img
            src={fallbackPdfQrUrl}
            alt={t("ar_workbench.fallback_qr_alt", { vin })}
            width={200}
            height={200}
            className="ar-fallback-qr-img"
          />
          <p className="ar-fallback-qr-label">{t("ar_workbench.fallback_qr_label")}</p>
        </div>
      )}

      {(correlationId || failureLoggedAt) && (
        <div className="ar-fallback-audit">
          {correlationId && (
            <div className="ar-fallback-audit-row">
              <span>{t("ar_workbench.correlation_id_label")}:</span>{" "}
              <code>{correlationId}</code>
            </div>
          )}
          {failureLoggedAt && (
            <div className="ar-fallback-audit-row">
              <span>{t("ar_workbench.failure_logged_at_label")}:</span>{" "}
              <time dateTime={failureLoggedAt}>{failureLoggedAt}</time>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export type ArLoadState =
  | { kind: "loading" }
  | { kind: "populated"; data: ArInstructionResponse }
  | { kind: "fallback"; data: ArInstructionFallbackResponse }
  | { kind: "error"; message: string };

interface Props {
  vin: string;
  stationId: string;
  fetchInstructions: (
    vin: string,
    stationId: string,
    signal: AbortSignal,
  ) => Promise<ArInstructionResponse | ArInstructionFallbackResponse>;
  /** Called exactly once on first successful populated render. */
  onOverlayRendered?: (data: ArInstructionResponse) => void;
}

export function ARWorkInstructions({ vin, stationId, fetchInstructions, onOverlayRendered }: Props) {
  const { t } = useTranslation();
  const [loadState, setLoadState] = useState<ArLoadState>({ kind: "loading" });
  const overlayRenderedRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    overlayRenderedRef.current = false;
    setLoadState({ kind: "loading" });

    // Client-side 10 s timeout — spec AC-775df01c
    const timeoutId = setTimeout(() => {
      controller.abort();
    }, 10_000);

    fetchInstructions(vin, stationId, controller.signal)
      .then((data) => {
        clearTimeout(timeoutId);

        // Discriminate between populated and fallback by checking for `steps`
        if ("steps" in data) {
          setLoadState({ kind: "populated", data });
        } else {
          setLoadState({ kind: "fallback", data });
        }
      })
      .catch((err: unknown) => {
        clearTimeout(timeoutId);
        if (err instanceof Error && err.name === "AbortError") {
          // Timeout or unmount abort — show generic fallback without audit data
          setLoadState({
            kind: "fallback",
            data: {
              vin,
              stationId,
              fallbackPdfQrUrl: "",
              failureLoggedAt: new Date().toISOString(),
              correlationId: "",
            },
          });
          return;
        }
        const message = err instanceof Error ? err.message : t("ar_workbench.error");
        setLoadState({ kind: "error", message });
      });

    return () => {
      clearTimeout(timeoutId);
      controller.abort();
    };
  }, [vin, stationId, fetchInstructions, t]);

  // Fire onOverlayRendered exactly once when populated state first renders
  useEffect(() => {
    if (loadState.kind === "populated" && !overlayRenderedRef.current) {
      overlayRenderedRef.current = true;
      onOverlayRendered?.(loadState.data);
    }
  }, [loadState, onOverlayRendered]);

  // ---------- Loading ----------
  if (loadState.kind === "loading") {
    return (
      <div className="ar-instructions ar-instructions--loading" aria-busy="true" role="status">
        <div className="ar-spinner" aria-hidden="true" />
        <p className="ar-loading-vin" aria-label={t("ar_workbench.vin_label")}>
          {vin}
        </p>
        <p className="ar-loading-msg">{t("ar_workbench.loading")}</p>
      </div>
    );
  }

  // ---------- Error ----------
  if (loadState.kind === "error") {
    return (
      <div className="ar-instructions ar-instructions--error" role="alert">
        <h3>{t("ar_workbench.error_title")}</h3>
        <p>{loadState.message}</p>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => setLoadState({ kind: "loading" })}
        >
          {t("ar_workbench.retry")}
        </button>
      </div>
    );
  }

  // ---------- Fallback (504 / 10 s timeout) ----------
  if (loadState.kind === "fallback") {
    const { data } = loadState;
    return (
      <div className="ar-instructions ar-instructions--fallback">
        <FallbackPanel
          vin={data.vin}
          stationId={data.stationId}
          fallbackPdfQrUrl={data.fallbackPdfQrUrl}
          correlationId={data.correlationId}
          failureLoggedAt={data.failureLoggedAt}
        />
      </div>
    );
  }

  // ---------- Populated ----------
  const { data } = loadState;

  return (
    <div className="ar-instructions ar-instructions--populated">
      {/* VIN + metadata header */}
      <div className="ar-header">
        <div className="ar-header-vin">
          <span className="ar-header-label">{t("ar_workbench.vin_label")}:</span>{" "}
          <strong className="ar-header-vin-value">{data.vin}</strong>
        </div>
        <div className="ar-header-station">
          {t("ar_workbench.station_label")}: <strong>{data.stationId}</strong>
        </div>
        <div className="ar-header-version">
          {t("ar_workbench.instruction_version_label")}: <strong>{data.instructionVersion}</strong>
        </div>
      </div>

      {/* Rare-variant banner */}
      {data.isRareVariant && (
        <div
          className="ar-variant-banner"
          role="alert"
          aria-live="polite"
          aria-label={t("ar_workbench.rare_variant_banner_label")}
        >
          <span className="ar-variant-icon" aria-hidden="true">&#9888;</span>
          <div className="ar-variant-text">
            <strong>{t("ar_workbench.rare_variant_title", { variant: data.variantCode })}</strong>
            <span>{t("ar_workbench.rare_variant_desc")}</span>
          </div>
        </div>
      )}

      {/* Option codes */}
      {data.optionCodes.length > 0 && (
        <div className="ar-option-codes" aria-label={t("ar_workbench.option_codes_label")}>
          {data.optionCodes.map((code) => (
            <span key={code} className="ar-option-chip">
              {code}
            </span>
          ))}
        </div>
      )}

      {/* Steps */}
      <ul
        className="ar-steps-list"
        role="list"
        aria-label={t("ar_workbench.steps_list_label")}
      >
        {data.steps.map((step) => (
          <StepRow key={step.fastenerPointId} step={step} />
        ))}
      </ul>

      {data.fallbackPdfQrUrl && (
        <div className="ar-fallback-link">
          <img
            src={data.fallbackPdfQrUrl}
            alt={t("ar_workbench.fallback_qr_alt", { vin: data.vin })}
            width={120}
            height={120}
          />
          <p>{t("ar_workbench.fallback_qr_label")}</p>
        </div>
      )}
    </div>
  );
}
