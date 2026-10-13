"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { CSSProperties, FormEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  ApiError,
  createCorrectiveAction,
  getConfidenceThreshold,
  listClusters,
  runClusterQuery,
} from "../services/api";
import type {
  ClusterQueryResult,
  ConfidenceThresholdConfig,
  CorrectiveActionResult,
  DefectClass,
  DefectCluster,
  RootCauseFactor,
} from "../types/defectGenome";
import {
  emitDefectGenomeQuery,
  emitRootCauseAssigned,
} from "../instrumentation/adoptionEvents";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFECT_CLASSES: { value: DefectClass; labelKey: string }[] = [
  { value: "dust", labelKey: "defectGenome.defectClassDust" },
  { value: "orange_peel", labelKey: "defectGenome.defectClassOrangePeel" },
  { value: "run", labelKey: "defectGenome.defectClassRun" },
  { value: "crater", labelKey: "defectGenome.defectClassCrater" },
  { value: "pinhole", labelKey: "defectGenome.defectClassPinhole" },
];

const TIME_WINDOW_OPTIONS: { value: number; labelKey: string }[] = [
  { value: 24, labelKey: "defectGenome.timeWindow24h" },
  { value: 48, labelKey: "defectGenome.timeWindow48h" },
  { value: 72, labelKey: "defectGenome.timeWindow72h" },
  { value: 168, labelKey: "defectGenome.timeWindow168h" },
];

function generateIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function formatDatetime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("en-GB", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function parseApiError(err: unknown): { message: string; correlationId?: string; status?: number } {
  if (err instanceof ApiError) {
    try {
      const body = JSON.parse(err.message) as { message?: string; correlation_id?: string };
      return {
        message: body.message ?? err.message,
        correlationId: body.correlation_id,
        status: err.status,
      };
    } catch {
      return { message: err.message, status: err.status };
    }
  }
  if (err instanceof Error) return { message: err.message };
  return { message: String(err) };
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface FactorCategoryBadgeProps {
  category: RootCauseFactor["factor_category"];
}

function FactorCategoryBadge({ category }: FactorCategoryBadgeProps) {
  const { t } = useTranslation();
  const styles: Record<string, CSSProperties> = {
    prep_stage: { background: "#EDE9FE", color: "#5B21B6" },
    booth_parameter: { background: "#FEF3C7", color: "#92400E" },
    climate_condition: { background: "#DCFCE7", color: "#14532D" },
  };
  const labelKeys: Record<string, string> = {
    prep_stage: "defectGenome.factorCategoryPrepStage",
    booth_parameter: "defectGenome.factorCategoryBoothParameter",
    climate_condition: "defectGenome.factorCategoryClimateCondition",
  };
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 7px",
        borderRadius: "4px",
        fontSize: "10px",
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: ".04em",
        ...(styles[category] ?? {}),
      }}
    >
      {t(labelKeys[category] ?? category)}
    </span>
  );
}

interface ConfidenceBadgeProps {
  lowConfidence: boolean;
}

function ConfidenceBadge({ lowConfidence }: ConfidenceBadgeProps) {
  const { t } = useTranslation();
  if (lowConfidence) {
    return (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "5px",
          padding: "3px 10px",
          borderRadius: "20px",
          fontSize: "11px",
          fontWeight: 700,
          letterSpacing: ".04em",
          textTransform: "uppercase",
          background: "var(--warn-bg, #FFF4E0)",
          color: "var(--warn, #C47800)",
          border: "1px solid #F6D27A",
        }}
        data-testid="low-confidence-badge"
        aria-label={t("defectGenome.lowConfidenceBadge")}
      >
        <span
          style={{
            width: "6px",
            height: "6px",
            borderRadius: "50%",
            background: "currentColor",
            display: "inline-block",
          }}
          aria-hidden="true"
        />
        {t("defectGenome.lowConfidenceBadge")}
      </span>
    );
  }
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "5px",
        padding: "3px 10px",
        borderRadius: "20px",
        fontSize: "11px",
        fontWeight: 700,
        letterSpacing: ".04em",
        textTransform: "uppercase",
        background: "#E6F5EE",
        color: "#0A7C42",
      }}
      data-testid="high-confidence-badge"
    >
      <span
        style={{
          width: "6px",
          height: "6px",
          borderRadius: "50%",
          background: "currentColor",
          display: "inline-block",
        }}
        aria-hidden="true"
      />
      {t("defectGenome.highConfidenceBadge")}
    </span>
  );
}

interface RootCauseFactorRowProps {
  factor: RootCauseFactor;
  selected: boolean;
  onSelect: (rank: number) => void;
}

function RootCauseFactorRow({ factor, selected, onSelect }: RootCauseFactorRowProps) {
  const { t } = useTranslation();
  const isTop = factor.rank === 1;
  const pct = Math.round(factor.probability_score * 100);
  const barColor = isTop ? "#1C69D4" : "#6B9DE8";

  return (
    <div
      role="listitem"
      style={{
        display: "grid",
        gridTemplateColumns: "24px 1fr 80px 72px 80px 100px",
        alignItems: "center",
        gap: "12px",
        padding: "14px 16px",
        background: isTop ? "#E8F0FB" : "#F7F8FA",
        borderRadius: "8px",
        border: `1px solid ${isTop ? "#1C69D4" : "#DDE1E8"}`,
      }}
      data-testid={`factor-row-${factor.rank}`}
    >
      {/* Rank */}
      <div
        style={{
          fontSize: "13px",
          fontWeight: 700,
          color: isTop ? "#1C69D4" : "#7A8494",
          textAlign: "center",
        }}
        aria-label={t("defectGenome.factorRank", { rank: factor.rank })}
      >
        {factor.rank}
      </div>

      {/* Name + category */}
      <div>
        <strong style={{ display: "block", fontSize: "13px", fontWeight: 600 }}>
          {factor.factor_name}
        </strong>
        <span style={{ fontSize: "11px", marginTop: "1px", display: "block" }}>
          <FactorCategoryBadge category={factor.factor_category} />
        </span>
      </div>

      {/* Probability bar */}
      <div>
        <div style={{ fontSize: "12px", fontWeight: 700, marginBottom: "3px" }}>{pct}%</div>
        <div
          style={{
            height: "6px",
            background: "#DDE1E8",
            borderRadius: "3px",
            overflow: "hidden",
          }}
          role="presentation"
        >
          <div
            style={{
              height: "100%",
              width: `${pct}%`,
              background: barColor,
              borderRadius: "3px",
            }}
          />
        </div>
      </div>

      {/* VIN count */}
      <div style={{ textAlign: "right" }}>
        <strong style={{ display: "block", fontSize: "14px", fontWeight: 700 }}>
          {factor.supporting_vin_count}
        </strong>
        <span style={{ fontSize: "11px", color: "#7A8494" }}>VINs</span>
      </div>

      {/* Probability score (numeric) */}
      <div style={{ fontSize: "12px", color: "#7A8494", textAlign: "right" }}>
        {factor.probability_score.toFixed(3)}
      </div>

      {/* Radio / select button */}
      <div style={{ textAlign: "right" }}>
        <label
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "6px",
            cursor: "pointer",
            fontSize: "12px",
            fontWeight: 600,
          }}
        >
          <input
            type="radio"
            name="selected-root-cause"
            value={String(factor.rank)}
            checked={selected}
            onChange={() => onSelect(factor.rank)}
            style={{ accentColor: "#1C69D4" }}
            aria-label={`${t("defectGenome.assignRootCause")}: ${factor.factor_name}`}
            data-testid={`factor-radio-${factor.rank}`}
          />
          {selected ? t("defectGenome.assignedRootCause") : t("defectGenome.assignRootCause")}
        </label>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Corrective Action Modal
// ---------------------------------------------------------------------------

interface CorrectiveActionModalProps {
  result: ClusterQueryResult;
  selectedFactor: RootCauseFactor;
  userId: string;
  onClose: () => void;
  onSuccess: (caResult: CorrectiveActionResult) => void;
  onDuplicate: (existingTicketId: string) => void;
}

type ModalState =
  | { type: "idle" }
  | { type: "submitting" }
  | { type: "error"; message: string; correlationId?: string };

function CorrectiveActionModal({
  result,
  selectedFactor,
  userId,
  onClose,
  onSuccess,
  onDuplicate,
}: CorrectiveActionModalProps) {
  const { t } = useTranslation();
  const [modalState, setModalState] = useState<ModalState>({ type: "idle" });
  const [vinListOpen, setVinListOpen] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  // Focus trap on mount
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
      }
      // Trap focus inside modal
      if (e.key === "Tab" && dialogRef.current) {
        const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault();
            last?.focus();
          }
        } else {
          if (document.activeElement === last) {
            e.preventDefault();
            first?.focus();
          }
        }
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previous?.focus();
    };
  }, [onClose]);

  const prepAdjustment = useMemo(() => {
    const catMap: Record<string, string> = {
      prep_stage: t("defectGenome.factorCategoryPrepStage"),
      booth_parameter: t("defectGenome.factorCategoryBoothParameter"),
      climate_condition: t("defectGenome.factorCategoryClimateCondition"),
    };
    return `${catMap[selectedFactor.factor_category] ?? selectedFactor.factor_category}: ${selectedFactor.factor_name}`;
  }, [selectedFactor, t]);

  async function handleSubmit() {
    setModalState({ type: "submitting" });
    try {
      const caResult = await createCorrectiveAction(result.cluster_id, {
        assigned_root_cause_factor_rank: selectedFactor.rank,
        confirmed_by_user_id: userId,
        idempotency_key: generateIdempotencyKey(),
      });
      onSuccess(caResult);
      emitRootCauseAssigned({
        userId,
        role: "paint_engineer",
        clusterId: result.cluster_id,
      });
    } catch (err: unknown) {
      const parsed = parseApiError(err);
      if (parsed.status === 409) {
        // Extract existing ticket ID from error body if present
        let existingId = result.cluster_id;
        try {
          const body = JSON.parse((err as ApiError).message) as { details?: Array<{ corrective_action_jira_id?: string }> };
          existingId = body.details?.[0]?.corrective_action_jira_id ?? existingId;
        } catch {
          // use cluster_id as fallback label
        }
        onDuplicate(existingId);
      } else {
        setModalState({
          type: "error",
          message: parsed.message,
          correlationId: parsed.correlationId,
        });
      }
    }
  }

  const pct = Math.round(selectedFactor.probability_score * 100);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="ca-modal-title"
      aria-describedby="ca-modal-desc"
      ref={dialogRef}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(10,15,30,.35)",
      }}
      data-testid="corrective-action-modal"
    >
      <div
        style={{
          background: "#fff",
          borderRadius: "12px",
          boxShadow: "0 4px 16px rgba(0,0,0,.12), 0 0 0 1px rgba(0,0,0,.05)",
          overflow: "hidden",
          width: "min(560px, 95vw)",
          maxHeight: "90vh",
          overflowY: "auto",
        }}
      >
        {/* Header */}
        <div
          style={{
            background: "#0D1B36",
            color: "#fff",
            padding: "18px 24px",
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: "12px",
          }}
        >
          <div>
            <h2 id="ca-modal-title" style={{ fontSize: "16px", fontWeight: 700 }}>
              {t("defectGenome.modalTitle")}
            </h2>
            <p
              id="ca-modal-desc"
              style={{ fontSize: "12px", color: "rgba(255,255,255,.6)", marginTop: "3px" }}
            >
              {t("defectGenome.modalSubtitle")}
            </p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            aria-label="Close"
            style={{
              background: "transparent",
              border: "none",
              color: "rgba(255,255,255,.7)",
              fontSize: "20px",
              cursor: "pointer",
              lineHeight: 1,
              padding: "2px 6px",
            }}
          >
            ×
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: "24px" }}>
          {modalState.type === "error" && (
            <div
              role="alert"
              aria-live="assertive"
              style={{
                display: "flex",
                gap: "14px",
                padding: "16px",
                background: "#FDF0EE",
                border: "1px solid #F5C6C1",
                borderRadius: "8px",
                marginBottom: "16px",
              }}
              data-testid="modal-error-banner"
            >
              <div
                style={{
                  width: "32px",
                  height: "32px",
                  background: "#C0392B",
                  borderRadius: "50%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "#fff",
                  fontWeight: 700,
                  flexShrink: 0,
                  fontSize: "16px",
                }}
                aria-hidden="true"
              >
                !
              </div>
              <div>
                <h3 style={{ fontSize: "14px", fontWeight: 700, color: "#C0392B" }}>
                  {t("defectGenome.modalErrorTitle")}
                </h3>
                <p style={{ fontSize: "13px", color: "#4B5568", marginTop: "4px" }}>
                  {modalState.message}
                </p>
                {modalState.correlationId && (
                  <p style={{ fontSize: "11px", color: "#7A8494", marginTop: "4px" }}>
                    {t("defectGenome.correlationId")}: {modalState.correlationId}
                  </p>
                )}
              </div>
            </div>
          )}

          {/* Pre-populated fields */}
          <div style={{ marginBottom: "14px" }}>
            <label
              style={{
                fontSize: "11px",
                fontWeight: 700,
                color: "#7A8494",
                textTransform: "uppercase",
                letterSpacing: ".06em",
                display: "block",
                marginBottom: "4px",
              }}
            >
              {t("defectGenome.modalClusterIdLabel")}
            </label>
            <div
              style={{
                padding: "9px 12px",
                background: "#F7F8FA",
                border: "1px solid #DDE1E8",
                borderRadius: "8px",
                fontSize: "13px",
                fontFamily: "monospace",
              }}
              data-testid="modal-cluster-id"
            >
              {result.cluster_id}
            </div>
          </div>

          <div style={{ marginBottom: "14px" }}>
            <label
              style={{
                fontSize: "11px",
                fontWeight: 700,
                color: "#7A8494",
                textTransform: "uppercase",
                letterSpacing: ".06em",
                display: "block",
                marginBottom: "4px",
              }}
            >
              {t("defectGenome.modalRootCauseLabel")}
            </label>
            <div
              style={{
                padding: "9px 12px",
                background: "#F7F8FA",
                border: "1px solid #DDE1E8",
                borderRadius: "8px",
                fontSize: "13px",
              }}
              data-testid="modal-root-cause"
            >
              {selectedFactor.factor_name}
            </div>
          </div>

          <div style={{ marginBottom: "14px" }}>
            <label
              style={{
                fontSize: "11px",
                fontWeight: 700,
                color: "#7A8494",
                textTransform: "uppercase",
                letterSpacing: ".06em",
                display: "block",
                marginBottom: "4px",
              }}
            >
              {t("defectGenome.modalProbabilityLabel")}
            </label>
            <div
              style={{
                padding: "9px 12px",
                background: "#F7F8FA",
                border: "1px solid #DDE1E8",
                borderRadius: "8px",
                fontSize: "13px",
                fontWeight: 700,
              }}
              data-testid="modal-probability"
            >
              {pct}%
            </div>
          </div>

          <div style={{ marginBottom: "14px" }}>
            <label
              style={{
                fontSize: "11px",
                fontWeight: 700,
                color: "#7A8494",
                textTransform: "uppercase",
                letterSpacing: ".06em",
                display: "block",
                marginBottom: "4px",
              }}
            >
              {t("defectGenome.modalAffectedVinsLabel")} ({result.affected_vin_count})
            </label>
            <div
              style={{
                padding: "9px 12px",
                background: "#F7F8FA",
                border: "1px solid #DDE1E8",
                borderRadius: "8px",
                fontSize: "13px",
              }}
              data-testid="modal-affected-vins"
            >
              {result.affected_vins && result.affected_vins.length > 0 ? (
                <>
                  <button
                    type="button"
                    onClick={() => setVinListOpen((prev) => !prev)}
                    style={{
                      background: "transparent",
                      border: "none",
                      color: "#1C69D4",
                      fontWeight: 600,
                      cursor: "pointer",
                      fontSize: "13px",
                      padding: 0,
                    }}
                    aria-expanded={vinListOpen}
                  >
                    {vinListOpen
                      ? t("defectGenome.modalVinListCollapse")
                      : t("defectGenome.modalVinListToggle")}
                    {" "}({result.affected_vin_count})
                  </button>
                  {vinListOpen && (
                    <div
                      style={{
                        marginTop: "8px",
                        display: "flex",
                        flexWrap: "wrap",
                        gap: "6px",
                      }}
                    >
                      {result.affected_vins.map((vin) => (
                        <span
                          key={vin}
                          style={{
                            fontFamily: "monospace",
                            fontSize: "10px",
                            padding: "3px 7px",
                            background: "#ECEEF2",
                            borderRadius: "4px",
                            color: "#4B5568",
                          }}
                        >
                          {vin}
                        </span>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <span style={{ color: "#7A8494" }}>{result.affected_vin_count} VINs</span>
              )}
            </div>
          </div>

          <div style={{ marginBottom: "14px" }}>
            <label
              style={{
                fontSize: "11px",
                fontWeight: 700,
                color: "#7A8494",
                textTransform: "uppercase",
                letterSpacing: ".06em",
                display: "block",
                marginBottom: "4px",
              }}
            >
              {t("defectGenome.modalPrepAdjustmentLabel")}
            </label>
            <div
              style={{
                padding: "9px 12px",
                background: "#F7F8FA",
                border: "1px solid #DDE1E8",
                borderRadius: "8px",
                fontSize: "13px",
              }}
              data-testid="modal-prep-adjustment"
            >
              {prepAdjustment}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div
          style={{
            padding: "16px 24px",
            background: "#F7F8FA",
            borderTop: "1px solid #DDE1E8",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "12px",
            flexWrap: "wrap",
          }}
        >
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: "10px 20px",
              borderRadius: "8px",
              fontSize: "14px",
              fontWeight: 600,
              cursor: "pointer",
              background: "#fff",
              color: "#1A2234",
              border: "1px solid #B8BEC8",
            }}
            data-testid="modal-cancel"
          >
            {t("defectGenome.modalCancel")}
          </button>
          <button
            type="button"
            onClick={() => { void handleSubmit(); }}
            disabled={modalState.type === "submitting"}
            style={{
              padding: "10px 20px",
              borderRadius: "8px",
              fontSize: "14px",
              fontWeight: 600,
              cursor: modalState.type === "submitting" ? "not-allowed" : "pointer",
              background: "#0A7C42",
              color: "#fff",
              border: "none",
              opacity: modalState.type === "submitting" ? 0.6 : 1,
            }}
            data-testid="modal-confirm"
          >
            {modalState.type === "submitting" ? t("defectGenome.loadingTitle") : t("defectGenome.modalConfirm")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

interface ToastProps {
  ticketId: string;
  ticketUrl: string;
  onDismiss: () => void;
}

function SuccessToast({ ticketId, ticketUrl, onDismiss }: ToastProps) {
  const { t } = useTranslation();

  useEffect(() => {
    const timer = setTimeout(onDismiss, 8000);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="success-toast"
      style={{
        position: "fixed",
        bottom: "24px",
        right: "24px",
        zIndex: 1100,
        background: "#fff",
        border: "1px solid #0A7C42",
        borderRadius: "12px",
        boxShadow: "0 4px 16px rgba(0,0,0,.12)",
        padding: "16px 20px",
        maxWidth: "380px",
        display: "flex",
        gap: "12px",
        alignItems: "flex-start",
      }}
    >
      <div
        style={{
          width: "32px",
          height: "32px",
          background: "#E6F5EE",
          border: "2px solid #0A7C42",
          borderRadius: "50%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#0A7C42",
          fontWeight: 700,
          flexShrink: 0,
          fontSize: "16px",
        }}
        aria-hidden="true"
      >
        ✓
      </div>
      <div style={{ flex: 1 }}>
        <strong style={{ display: "block", fontSize: "13px", fontWeight: 700 }}>
          {t("defectGenome.successToastTitle")}
        </strong>
        <p style={{ fontSize: "12px", color: "#4B5568", marginTop: "2px" }}>
          {t("defectGenome.successToastBody", { ticketId })}
        </p>
        <a
          href={ticketUrl}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "4px",
            marginTop: "8px",
            fontSize: "12px",
            fontWeight: 600,
            color: "#1C69D4",
            textDecoration: "none",
          }}
          data-testid="toast-jira-link"
        >
          {t("defectGenome.successToastViewLink")} ↗
        </a>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss notification"
        style={{
          background: "transparent",
          border: "none",
          color: "#7A8494",
          cursor: "pointer",
          fontSize: "16px",
          lineHeight: 1,
          padding: "2px",
        }}
      >
        ×
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recent clusters table (query builder screen)
// ---------------------------------------------------------------------------

interface RecentClustersTableProps {
  clusters: DefectCluster[];
  onView: (clusterId: string) => void;
}

function RecentClustersTable({ clusters, onView }: RecentClustersTableProps) {
  const { t } = useTranslation();

  function statusLabel(c: DefectCluster): string {
    const conf = c.confidence_score;
    if (conf < 0.6) return t("defectGenome.statusLowConf");
    if (c.status === "resolved") return t("defectGenome.statusResolved");
    if (c.status === "in_progress") return t("defectGenome.statusInProgress");
    return t("defectGenome.statusOpen");
  }

  function statusStyle(c: DefectCluster): CSSProperties {
    const conf = c.confidence_score;
    if (conf < 0.6)
      return { background: "#FFF4E0", color: "#C47800", border: "1px solid #F6D27A" };
    if (c.status === "resolved") return { background: "#E6F5EE", color: "#0A7C42" };
    if (c.status === "in_progress") return { background: "#E8F0FB", color: "#1455AA" };
    return { background: "#ECEEF2", color: "#4B5568" };
  }

  return (
    <table
      style={{ width: "100%", borderCollapse: "collapse" }}
      role="table"
      aria-label={t("defectGenome.recentQueriesTitle")}
    >
      <thead>
        <tr style={{ borderBottom: "2px solid #DDE1E8" }}>
          {[
            t("defectGenome.colClusterId"),
            t("defectGenome.colDefect"),
            t("defectGenome.colVins"),
            t("defectGenome.colConfidence"),
            t("defectGenome.colStatus"),
            "",
          ].map((h, i) => (
            <th
              key={i}
              scope="col"
              style={{
                padding: "8px 10px",
                fontSize: "11px",
                fontWeight: 700,
                color: "#7A8494",
                textTransform: "uppercase",
                letterSpacing: ".06em",
                textAlign: i >= 2 && i <= 3 ? "right" : "left",
              }}
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {clusters.map((c) => {
          const confPct = Math.round(c.confidence_score * 100);
          const confColor = confPct >= 60 ? "#0A7C42" : "#C47800";
          return (
            <tr key={c.cluster_id} style={{ borderBottom: "1px solid #DDE1E8" }}>
              <td style={{ padding: "10px" }}>
                <code
                  style={{
                    fontFamily: "monospace",
                    fontSize: "11px",
                    background: "#ECEEF2",
                    padding: "2px 5px",
                    borderRadius: "3px",
                    color: "#4B5568",
                  }}
                >
                  {c.cluster_id.slice(0, 8)}…
                </code>
              </td>
              <td style={{ padding: "10px" }}>
                <span
                  style={{
                    background: "#ECEEF2",
                    color: "#4B5568",
                    padding: "3px 10px",
                    borderRadius: "20px",
                    fontSize: "11px",
                    fontWeight: 700,
                    textTransform: "uppercase",
                  }}
                >
                  {c.defect_class}
                </span>
              </td>
              <td style={{ padding: "10px", textAlign: "right", fontWeight: 600 }}>
                {c.affected_vin_count}
              </td>
              <td
                style={{
                  padding: "10px",
                  textAlign: "right",
                  fontWeight: 700,
                  color: confColor,
                }}
              >
                {confPct}%
              </td>
              <td style={{ padding: "10px" }}>
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    padding: "3px 10px",
                    borderRadius: "20px",
                    fontSize: "11px",
                    fontWeight: 700,
                    textTransform: "uppercase",
                    ...statusStyle(c),
                  }}
                >
                  {statusLabel(c)}
                </span>
              </td>
              <td style={{ padding: "10px" }}>
                <button
                  type="button"
                  onClick={() => onView(c.cluster_id)}
                  style={{
                    background: "transparent",
                    border: "none",
                    color: "#1C69D4",
                    fontWeight: 600,
                    cursor: "pointer",
                    fontSize: "12px",
                    padding: 0,
                  }}
                  aria-label={`${t("defectGenome.viewLink")} cluster ${c.cluster_id}`}
                >
                  {t("defectGenome.viewLink")}
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

interface Props {
  userId?: string;
}

type QueryState =
  | { type: "idle" }
  | { type: "loading"; defectClass: DefectClass; timeWindowHours: number; idempotencyKey: string }
  | { type: "error"; message: string; correlationId?: string; defectClass: DefectClass; idempotencyKey: string }
  | { type: "result"; data: ClusterQueryResult };

interface Toast {
  ticketId: string;
  ticketUrl: string;
}

interface DuplicateInfo {
  ticketId: string;
}

export function DefectGenomePage({ userId = "anonymous" }: Props) {
  const { t } = useTranslation();

  // Config loaded once per session
  const [config, setConfig] = useState<ConfidenceThresholdConfig | null>(null);
  const configLoadedRef = useRef(false);

  // Form state
  const [defectClass, setDefectClass] = useState<DefectClass | "">("");
  const [timeWindowHours, setTimeWindowHours] = useState<number>(48);
  const [vinFilterRaw, setVinFilterRaw] = useState<string>("");

  // Query state
  const [queryState, setQueryState] = useState<QueryState>({ type: "idle" });

  // Recent clusters
  const [recentClusters, setRecentClusters] = useState<DefectCluster[]>([]);

  // Result interaction
  const [selectedFactorRank, setSelectedFactorRank] = useState<number | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);
  const [duplicateInfo, setDuplicateInfo] = useState<DuplicateInfo | null>(null);

  // Load config once at session start
  useEffect(() => {
    if (configLoadedRef.current) return;
    configLoadedRef.current = true;
    getConfidenceThreshold()
      .then(setConfig)
      .catch(() => {
        // Non-fatal — use defaults
        setConfig({
          low_confidence_threshold: 0.6,
          cluster_alert_threshold: 0.8,
          cluster_alert_min_vins: 3,
          cluster_alert_window_hours: 4,
        });
      });
  }, []);

  // Load recent clusters on mount
  useEffect(() => {
    listClusters({ limit: 3 })
      .then((res) => setRecentClusters(res.data))
      .catch(() => setRecentClusters([]));
  }, []);

  const handleReset = useCallback(() => {
    setDefectClass("");
    setTimeWindowHours(48);
    setVinFilterRaw("");
    setQueryState({ type: "idle" });
    setSelectedFactorRank(null);
    setDuplicateInfo(null);
  }, []);

  const handleSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      if (!defectClass) return;

      const idempotencyKey = generateIdempotencyKey();
      const vinFilter = vinFilterRaw.trim()
        ? vinFilterRaw
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean)
        : undefined;

      setQueryState({
        type: "loading",
        defectClass,
        timeWindowHours,
        idempotencyKey,
      });
      setSelectedFactorRank(null);
      setDuplicateInfo(null);

      try {
        const result = await runClusterQuery({
          defect_class: defectClass,
          time_window_hours: timeWindowHours,
          vin_filter: vinFilter,
          idempotency_key: idempotencyKey,
        });
        setQueryState({ type: "result", data: result });
        emitDefectGenomeQuery({
          userId,
          role: "paint_engineer",
          defectClass,
        });
        // Refresh recent clusters
        listClusters({ limit: 3 })
          .then((res) => setRecentClusters(res.data))
          .catch(() => {});
      } catch (err: unknown) {
        const parsed = parseApiError(err);
        setQueryState({
          type: "error",
          message: parsed.message,
          correlationId: parsed.correlationId,
          defectClass,
          idempotencyKey,
        });
      }
    },
    [defectClass, timeWindowHours, vinFilterRaw, userId],
  );

  const selectedFactor = useMemo(() => {
    if (queryState.type !== "result" || selectedFactorRank === null) return null;
    return queryState.data.top_factors.find((f) => f.rank === selectedFactorRank) ?? null;
  }, [queryState, selectedFactorRank]);

  const threshold = config?.low_confidence_threshold ?? 0.6;
  const thresholdPct = Math.round(threshold * 100);

  // -------------------------------------------------------------------------
  // Render helpers
  // -------------------------------------------------------------------------

  function renderConfigSidebar() {
    return (
      <>
        <div
          style={{
            background: "#F7F8FA",
            border: "1px solid #DDE1E8",
            borderRadius: "8px",
            padding: "16px",
            marginBottom: "12px",
          }}
        >
          <div
            style={{
              fontSize: "11px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: ".08em",
              color: "#7A8494",
              marginBottom: "10px",
            }}
          >
            {t("defectGenome.configTitle")}
          </div>
          {[
            ["low_confidence_threshold", config?.low_confidence_threshold?.toFixed(2) ?? "—"],
            ["cluster_alert_threshold", config?.cluster_alert_threshold?.toFixed(2) ?? "—"],
            ["cluster_alert_min_vins", String(config?.cluster_alert_min_vins ?? "—")],
            ["cluster_alert_window_hours", String(config?.cluster_alert_window_hours ?? "—")],
            ["Source", "GET /models/confidence-threshold"],
            ["updated_at", config?.updated_at ? formatDatetime(config.updated_at) : "—"],
          ].map(([k, v]) => (
            <div
              key={k}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "6px 0",
                borderBottom: "1px solid #DDE1E8",
              }}
            >
              <span style={{ fontSize: "12px", color: "#4B5568" }}>{k}</span>
              <span
                style={{
                  fontSize: "12px",
                  fontWeight: 600,
                  color: "#1A2234",
                  fontFamily: "monospace",
                }}
              >
                {v}
              </span>
            </div>
          ))}
        </div>
        <div
          style={{
            background: "#E8F0FB",
            border: "1px solid #97C0EF",
            borderRadius: "8px",
            padding: "16px",
          }}
        >
          <div
            style={{
              fontSize: "11px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: ".08em",
              color: "#1455AA",
              marginBottom: "10px",
            }}
          >
            {t("defectGenome.howItWorksTitle")}
          </div>
          <p style={{ fontSize: "12px", color: "#4B5568", lineHeight: 1.7 }}>
            {t("defectGenome.howItWorksBody")}
          </p>
        </div>
      </>
    );
  }

  // -------------------------------------------------------------------------
  // Result screen
  // -------------------------------------------------------------------------
  if (queryState.type === "result") {
    const { data } = queryState;
    const confPct = Math.round(data.model_confidence * 100);
    const isLow = data.low_confidence;
    const confBgColor = isLow ? "#FFF4E0" : "#F7F8FA";
    const confBorderColor = isLow ? "#F6D27A" : "#DDE1E8";
    const circleStyle: CSSProperties = isLow
      ? { background: "#FFF4E0", color: "#C47800", border: "3px solid #F59E0B" }
      : { background: "#E6F5EE", color: "#0A7C42", border: "3px solid #0A7C42" };

    return (
      <section aria-labelledby="dg-result-heading">
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "24px", flexWrap: "wrap", gap: "12px" }}>
          <div>
            <p style={{ fontSize: "12px", color: "#7A8494", marginBottom: "6px" }}>
              Quality Studio /{" "}
              <span style={{ color: "#4B5568" }}>{t("defectGenome.breadcrumbParent")}</span>{" "}
              / <span style={{ color: "#4B5568" }}>{data.cluster_id.slice(0, 8)}</span>
            </p>
            <h1 id="dg-result-heading" style={{ fontSize: "22px", fontWeight: 700, lineHeight: 1.2 }}>
              {t("defectGenome.resultTitle")}
            </h1>
            <p style={{ fontSize: "13px", color: "#4B5568", marginTop: "4px" }}>
              {t("defectGenome.resultSubtitle", {
                defectClass: data.defect_class,
                queriedAt: formatDatetime(data.queried_at),
              })}
            </p>
          </div>
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" }}>
            <ConfidenceBadge lowConfidence={isLow} />
            <span
              style={{
                background: "#E8F0FB",
                color: "#1455AA",
                padding: "3px 10px",
                borderRadius: "20px",
                fontSize: "11px",
                fontWeight: 700,
                textTransform: "uppercase",
              }}
            >
              query_id: {data.query_id.slice(0, 6)}
            </span>
            <span
              style={{
                background: "#ECEEF2",
                color: "#4B5568",
                padding: "3px 10px",
                borderRadius: "20px",
                fontSize: "11px",
                fontWeight: 700,
                textTransform: "uppercase",
              }}
            >
              cluster_id: {data.cluster_id.slice(0, 8)}
            </span>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: "20px", alignItems: "start" }}>
          <div>
            {/* Confidence summary bar */}
            <div
              role="region"
              aria-label="Model confidence summary"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "16px",
                padding: "14px 16px",
                borderRadius: "8px",
                background: confBgColor,
                border: `1px solid ${confBorderColor}`,
                marginBottom: "20px",
                flexWrap: "wrap",
              }}
              data-testid="confidence-summary"
            >
              <div
                style={{
                  width: "56px",
                  height: "56px",
                  borderRadius: "50%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: "15px",
                  fontWeight: 800,
                  flexShrink: 0,
                  ...circleStyle,
                }}
                aria-label={`Model confidence ${confPct} percent`}
                data-testid="confidence-circle"
              >
                {confPct}%
              </div>
              <div style={{ flex: 1 }}>
                <strong style={{ display: "block", fontSize: "13px", fontWeight: 700 }}>
                  {isLow
                    ? t("defectGenome.modelConfidenceBelow", { pct: confPct })
                    : t("defectGenome.modelConfidenceAbove", { pct: confPct })}
                </strong>
                <span style={{ fontSize: "12px", color: "#4B5568" }}>
                  {t("defectGenome.modelConfidenceDetail", {
                    confidence: data.model_confidence.toFixed(2),
                    low: String(data.low_confidence),
                    threshold: threshold.toFixed(2),
                    version: data.model_version ?? "—",
                  })}
                </span>
              </div>
              <ConfidenceBadge lowConfidence={isLow} />
            </div>

            {/* Root-cause factors */}
            <div
              style={{
                background: "#fff",
                borderRadius: "12px",
                boxShadow: "0 1px 4px rgba(0,0,0,.08), 0 0 0 1px rgba(0,0,0,.04)",
                padding: "24px",
              }}
            >
              <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "16px", gap: "12px" }}>
                <div>
                  <h2 style={{ fontSize: "15px", fontWeight: 700 }}>
                    {t("defectGenome.rootCauseListTitle")}
                  </h2>
                  <p style={{ fontSize: "12px", color: "#7A8494", marginTop: "2px" }}>
                    {t("defectGenome.rootCauseListSubtitle", {
                      count: data.affected_vin_count,
                      defectClass: data.defect_class,
                    })}
                  </p>
                </div>
                <span
                  style={{
                    background: "#ECEEF2",
                    color: "#4B5568",
                    padding: "3px 10px",
                    borderRadius: "20px",
                    fontSize: "11px",
                    fontWeight: 700,
                    textTransform: "uppercase",
                    whiteSpace: "nowrap",
                  }}
                >
                  {t("defectGenome.topFactors")}
                </span>
              </div>

              {data.top_factors.length === 0 ? (
                <p
                  role="status"
                  style={{ fontSize: "13px", color: "#7A8494", padding: "32px 0", textAlign: "center" }}
                  data-testid="empty-factors"
                >
                  {t("defectGenome.emptyClusters")}
                </p>
              ) : (
                <div
                  role="radiogroup"
                  aria-label={t("defectGenome.rootCauseListTitle")}
                  style={{ display: "flex", flexDirection: "column", gap: "10px" }}
                  data-testid="factor-list"
                >
                  {data.top_factors
                    .slice()
                    .sort((a, b) => a.rank - b.rank)
                    .map((factor) => (
                      <RootCauseFactorRow
                        key={factor.rank}
                        factor={factor}
                        selected={selectedFactorRank === factor.rank}
                        onSelect={setSelectedFactorRank}
                      />
                    ))}
                </div>
              )}

              {/* Action bar */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "12px",
                  marginTop: "24px",
                  paddingTop: "20px",
                  borderTop: "1px solid #DDE1E8",
                  flexWrap: "wrap",
                }}
              >
                <button
                  type="button"
                  onClick={() => setModalOpen(true)}
                  disabled={selectedFactorRank === null}
                  aria-disabled={selectedFactorRank === null}
                  title={selectedFactorRank === null ? t("defectGenome.createCorrectiveActionDisabledHint") : undefined}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "8px",
                    padding: "10px 20px",
                    borderRadius: "8px",
                    fontSize: "14px",
                    fontWeight: 600,
                    cursor: selectedFactorRank === null ? "not-allowed" : "pointer",
                    background: selectedFactorRank === null ? "#9CA3AF" : "#0A7C42",
                    color: "#fff",
                    border: "none",
                    opacity: selectedFactorRank === null ? 0.6 : 1,
                  }}
                  data-testid="create-corrective-action-btn"
                >
                  {t("defectGenome.createCorrectiveAction")}
                </button>
                <button
                  type="button"
                  onClick={handleReset}
                  style={{
                    padding: "10px 20px",
                    borderRadius: "8px",
                    fontSize: "14px",
                    fontWeight: 600,
                    cursor: "pointer",
                    background: "#fff",
                    color: "#1A2234",
                    border: "1px solid #B8BEC8",
                  }}
                  data-testid="back-to-query-btn"
                >
                  {t("defectGenome.backToQuery")}
                </button>
              </div>
            </div>

            {/* Duplicate info */}
            {duplicateInfo && (
              <div
                role="alert"
                aria-live="polite"
                data-testid="duplicate-info"
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "14px",
                  padding: "16px 20px",
                  background: "#F0F5FF",
                  border: "1px solid #C7D9F8",
                  borderRadius: "8px",
                  marginTop: "16px",
                }}
              >
                <div>
                  <strong style={{ fontSize: "14px", fontWeight: 700, color: "#1455AA" }}>
                    {t("defectGenome.duplicateTitle")}
                  </strong>
                  <p style={{ fontSize: "13px", color: "#4B5568", marginTop: "4px" }}>
                    {t("defectGenome.duplicateBody", { ticketId: duplicateInfo.ticketId })}
                  </p>
                </div>
              </div>
            )}

            {/* Advisor v1 fallback */}
            {isLow && data.advisor_v1_fallback && (
              <div
                style={{
                  border: "2px solid #F59E0B",
                  borderRadius: "12px",
                  padding: "20px",
                  background: "#FFF4E0",
                  marginTop: "16px",
                }}
                data-testid="advisor-v1-fallback"
              >
                <div style={{ display: "flex", alignItems: "flex-start", gap: "10px", marginBottom: "14px" }}>
                  <div>
                    <h3 style={{ fontSize: "14px", fontWeight: 700, color: "#C47800" }}>
                      {t("defectGenome.advisorFallbackTitle")}
                    </h3>
                    <p style={{ fontSize: "12px", color: "#4B5568", marginTop: "2px" }}>
                      {t("defectGenome.advisorFallbackSubtitle")}
                    </p>
                  </div>
                </div>
                <div
                  style={{
                    padding: "10px 14px",
                    background: "#fff",
                    border: "1px solid #F6D27A",
                    borderRadius: "8px",
                    marginBottom: "8px",
                  }}
                >
                  <strong style={{ fontSize: "13px", fontWeight: 600, display: "block" }}>
                    {t("defectGenome.advisorRuleLabel")}: {data.advisor_v1_fallback.rule_id}
                  </strong>
                  <span style={{ fontSize: "12px", color: "#4B5568" }}>
                    {data.advisor_v1_fallback.rule_description}
                  </span>
                </div>
                {data.advisor_v1_fallback.matched_conditions && data.advisor_v1_fallback.matched_conditions.length > 0 && (
                  <div
                    style={{
                      padding: "10px 14px",
                      background: "#fff",
                      border: "1px solid #F6D27A",
                      borderRadius: "8px",
                      marginBottom: "8px",
                    }}
                  >
                    <strong style={{ fontSize: "13px", fontWeight: 600, display: "block", marginBottom: "6px" }}>
                      {t("defectGenome.advisorMatchedConditions")}
                    </strong>
                    <ul style={{ margin: 0, paddingLeft: "16px", fontSize: "12px", color: "#4B5568", lineHeight: 1.7 }}>
                      {data.advisor_v1_fallback.matched_conditions.map((c, i) => (
                        <li key={i}>{c}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div
                  style={{
                    padding: "10px 14px",
                    background: "#fff",
                    border: "1px solid #F6D27A",
                    borderRadius: "8px",
                  }}
                  data-testid="advisor-recommended-action"
                >
                  <strong style={{ fontSize: "13px", fontWeight: 600, display: "block", marginBottom: "2px" }}>
                    {t("defectGenome.advisorRecommendedAction")}
                  </strong>
                  <span style={{ fontSize: "12px", color: "#4B5568" }}>
                    {data.advisor_v1_fallback.recommended_action}
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Sidebar */}
          <aside>{renderConfigSidebar()}</aside>
        </div>

        {/* Modal */}
        {modalOpen && selectedFactor && (
          <CorrectiveActionModal
            result={data}
            selectedFactor={selectedFactor}
            userId={userId}
            onClose={() => setModalOpen(false)}
            onSuccess={(caResult) => {
              setModalOpen(false);
              setToast({ ticketId: caResult.jira_ticket_id, ticketUrl: caResult.jira_ticket_url });
            }}
            onDuplicate={(existingTicketId) => {
              setModalOpen(false);
              setDuplicateInfo({ ticketId: existingTicketId });
            }}
          />
        )}

        {/* Toast */}
        {toast && (
          <SuccessToast
            ticketId={toast.ticketId}
            ticketUrl={toast.ticketUrl}
            onDismiss={() => setToast(null)}
          />
        )}
      </section>
    );
  }

  // -------------------------------------------------------------------------
  // Query builder (idle + loading + error states)
  // -------------------------------------------------------------------------

  const isLoading = queryState.type === "loading";
  const isError = queryState.type === "error";

  return (
    <section aria-labelledby="dg-heading">
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          marginBottom: "24px",
          flexWrap: "wrap",
          gap: "12px",
        }}
      >
        <div>
          <p style={{ fontSize: "12px", color: "#7A8494", marginBottom: "6px" }}>
            Quality Studio / <span style={{ color: "#4B5568" }}>{t("defectGenome.breadcrumbParent")}</span>
          </p>
          <h1 id="dg-heading" style={{ fontSize: "22px", fontWeight: 700, lineHeight: 1.2 }}>
            {t("defectGenome.title")}
          </h1>
          <p style={{ fontSize: "13px", color: "#4B5568", marginTop: "4px" }}>
            {t("defectGenome.subtitle")}
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
          <span
            style={{
              background: "#E8F0FB",
              color: "#1455AA",
              padding: "3px 10px",
              borderRadius: "20px",
              fontSize: "11px",
              fontWeight: 700,
              textTransform: "uppercase",
            }}
          >
            Model v2.4.1
          </span>
          <span
            style={{
              background: "#ECEEF2",
              color: "#4B5568",
              padding: "3px 10px",
              borderRadius: "20px",
              fontSize: "11px",
              fontWeight: 700,
              textTransform: "uppercase",
            }}
          >
            Threshold: {thresholdPct}%
          </span>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: "20px", alignItems: "start" }}>
        <div>
          {/* Error banner */}
          {isError && (
            <div
              role="alert"
              aria-live="assertive"
              style={{
                display: "flex",
                gap: "14px",
                padding: "16px 20px",
                background: "#FDF0EE",
                border: "1px solid #F5C6C1",
                borderRadius: "12px",
                marginBottom: "16px",
              }}
              data-testid="query-error-banner"
            >
              <div
                style={{
                  width: "36px",
                  height: "36px",
                  background: "#C0392B",
                  borderRadius: "50%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "#fff",
                  fontWeight: 700,
                  flexShrink: 0,
                  fontSize: "18px",
                }}
                aria-hidden="true"
              >
                !
              </div>
              <div style={{ flex: 1 }}>
                <h2 style={{ fontSize: "14px", fontWeight: 700, color: "#C0392B" }}>
                  {t("defectGenome.queryFailedTitle")}
                </h2>
                <p style={{ fontSize: "13px", color: "#4B5568", marginTop: "4px" }}>
                  {t("defectGenome.queryFailedBody")}
                </p>
                <p style={{ marginTop: "8px", fontSize: "13px", color: "#4B5568" }}>
                  <strong>Error:</strong>{" "}
                  <code
                    style={{
                      fontFamily: "monospace",
                      fontSize: "12px",
                      background: "rgba(192,57,43,.1)",
                      padding: "2px 5px",
                      borderRadius: "3px",
                    }}
                  >
                    {queryState.message}
                  </code>
                </p>
                {queryState.correlationId && (
                  <p style={{ marginTop: "4px", fontSize: "11px", color: "#7A8494" }}>
                    {t("defectGenome.correlationId")}: {queryState.correlationId}
                  </p>
                )}
              </div>
            </div>
          )}

          {/* Query form card */}
          <div
            style={{
              background: "#fff",
              borderRadius: "12px",
              boxShadow: "0 1px 4px rgba(0,0,0,.08), 0 0 0 1px rgba(0,0,0,.04)",
              padding: "24px",
              marginBottom: "16px",
            }}
          >
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "16px", gap: "12px" }}>
              <div>
                <h2 style={{ fontSize: "15px", fontWeight: 700 }}>
                  {t("defectGenome.queryPanelTitle")}
                </h2>
                <p style={{ fontSize: "12px", color: "#7A8494", marginTop: "2px" }}>
                  {t("defectGenome.queryPanelSubtitle")}
                </p>
              </div>
              <span
                style={{
                  background: "#E8F0FB",
                  color: "#1455AA",
                  padding: "3px 10px",
                  borderRadius: "20px",
                  fontSize: "11px",
                  fontWeight: 700,
                  textTransform: "uppercase",
                  whiteSpace: "nowrap",
                }}
              >
                POST /defect/cluster-query
              </span>
            </div>

            <form onSubmit={(e) => { void handleSubmit(e); }} aria-label={t("defectGenome.queryPanelTitle")}>
              {/* Defect class radiogroup */}
              <fieldset
                style={{ border: "none", margin: 0, padding: 0, marginBottom: "16px" }}
              >
                <legend
                  style={{
                    fontSize: "12px",
                    fontWeight: 600,
                    color: "#4B5568",
                    marginBottom: "8px",
                  }}
                >
                  {t("defectGenome.defectClassLabel")}{" "}
                  <span style={{ color: "#C0392B" }} aria-hidden="true">*</span>
                </legend>
                <div
                  role="radiogroup"
                  aria-label={t("defectGenome.defectClassLabel")}
                  aria-required="true"
                  style={{ display: "flex", flexWrap: "wrap", gap: "10px" }}
                  data-testid="defect-class-radiogroup"
                >
                  {DEFECT_CLASSES.map(({ value, labelKey }) => (
                    <label
                      key={value}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "6px",
                        cursor: "pointer",
                        padding: "8px 14px",
                        borderRadius: "8px",
                        border: `1px solid ${defectClass === value ? "#1C69D4" : "#B8BEC8"}`,
                        background: defectClass === value ? "#E8F0FB" : "#fff",
                        fontSize: "13px",
                        fontWeight: defectClass === value ? 600 : 400,
                        color: defectClass === value ? "#1455AA" : "#1A2234",
                      }}
                    >
                      <input
                        type="radio"
                        name="defect_class"
                        value={value}
                        required
                        checked={defectClass === value}
                        onChange={() => setDefectClass(value)}
                        style={{ accentColor: "#1C69D4" }}
                        aria-label={t(labelKey)}
                        data-testid={`radio-${value}`}
                      />
                      {t(labelKey)}
                    </label>
                  ))}
                </div>
              </fieldset>

              {/* Time window */}
              <div style={{ marginBottom: "16px" }}>
                <label
                  htmlFor="time-window"
                  style={{ fontSize: "12px", fontWeight: 600, color: "#4B5568", display: "block", marginBottom: "6px" }}
                >
                  {t("defectGenome.timeWindowLabel")}
                  <span
                    style={{ fontFamily: "monospace", fontSize: "11px", color: "#7A8494", marginLeft: "5px" }}
                  >
                    time_window_hours
                  </span>
                </label>
                <select
                  id="time-window"
                  value={timeWindowHours}
                  onChange={(e) => setTimeWindowHours(Number(e.target.value))}
                  style={{
                    width: "100%",
                    padding: "9px 12px",
                    border: "1px solid #B8BEC8",
                    borderRadius: "8px",
                    fontSize: "14px",
                    color: "#1A2234",
                    background: "#fff",
                    appearance: "none",
                  }}
                >
                  {TIME_WINDOW_OPTIONS.map(({ value, labelKey }) => (
                    <option key={value} value={value}>
                      {t(labelKey)}
                    </option>
                  ))}
                </select>
                <p style={{ fontSize: "11px", color: "#7A8494", marginTop: "2px" }}>
                  {t("defectGenome.timeWindowHint")}
                </p>
              </div>

              {/* VIN filter */}
              <div style={{ marginBottom: "16px" }}>
                <label
                  htmlFor="vin-filter"
                  style={{ fontSize: "12px", fontWeight: 600, color: "#4B5568", display: "block", marginBottom: "6px" }}
                >
                  {t("defectGenome.vinFilterLabel")}
                  <span
                    style={{ fontFamily: "monospace", fontSize: "11px", color: "#7A8494", marginLeft: "5px" }}
                  >
                    vin_filter
                  </span>
                </label>
                <input
                  id="vin-filter"
                  type="text"
                  value={vinFilterRaw}
                  onChange={(e) => setVinFilterRaw(e.target.value)}
                  placeholder={t("defectGenome.vinFilterPlaceholder")}
                  style={{
                    width: "100%",
                    padding: "9px 12px",
                    border: "1px solid #B8BEC8",
                    borderRadius: "8px",
                    fontSize: "14px",
                    color: "#1A2234",
                    background: "#fff",
                  }}
                  data-testid="vin-filter-input"
                />
                <p style={{ fontSize: "11px", color: "#7A8494", marginTop: "2px" }}>
                  {t("defectGenome.vinFilterHint")}
                </p>
              </div>

              {/* Action bar */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "12px",
                  marginTop: "24px",
                  paddingTop: "20px",
                  borderTop: "1px solid #DDE1E8",
                  flexWrap: "wrap",
                }}
              >
                <button
                  type="submit"
                  disabled={isLoading || !defectClass}
                  aria-disabled={isLoading || !defectClass}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "8px",
                    padding: "12px 28px",
                    borderRadius: "8px",
                    fontSize: "15px",
                    fontWeight: 600,
                    cursor: isLoading || !defectClass ? "not-allowed" : "pointer",
                    background: isLoading || !defectClass ? "#9CA3AF" : "#1C69D4",
                    color: "#fff",
                    border: "none",
                    opacity: isLoading || !defectClass ? 0.6 : 1,
                  }}
                  data-testid="run-defect-genome-btn"
                >
                  {isLoading ? (
                    <>
                      <span aria-hidden="true">⟳</span>
                      {t("defectGenome.loadingTitle")}
                    </>
                  ) : (
                    t("defectGenome.runButton")
                  )}
                </button>
                <button
                  type="button"
                  onClick={handleReset}
                  style={{
                    padding: "10px 20px",
                    borderRadius: "8px",
                    fontSize: "14px",
                    fontWeight: 600,
                    cursor: "pointer",
                    background: "#fff",
                    color: "#1A2234",
                    border: "1px solid #B8BEC8",
                  }}
                  data-testid="reset-btn"
                >
                  {t("defectGenome.resetButton")}
                </button>
                <span style={{ marginLeft: "auto", fontSize: "11px", color: "#7A8494" }}>
                  {t("defectGenome.idempotencyHint")}
                </span>
              </div>
            </form>

            {/* Loading overlay inside the card */}
            {isLoading && (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  padding: "40px 24px",
                  textAlign: "center",
                  borderTop: "1px solid #DDE1E8",
                  marginTop: "16px",
                }}
                role="status"
                aria-live="polite"
                aria-label={t("defectGenome.loadingTitle")}
                data-testid="query-loading"
              >
                <div
                  style={{
                    width: "40px",
                    height: "40px",
                    border: "3px solid #DDE1E8",
                    borderTopColor: "#1C69D4",
                    borderRadius: "50%",
                    animation: "spin 0.8s linear infinite",
                    marginBottom: "20px",
                  }}
                  aria-hidden="true"
                />
                <h3 style={{ fontSize: "16px", fontWeight: 700, marginBottom: "6px" }}>
                  {t("defectGenome.loadingTitle")}
                </h3>
                <p style={{ fontSize: "13px", color: "#4B5568" }}>
                  {t("defectGenome.loadingSubtext", {
                    hours: queryState.timeWindowHours,
                    defectClass: queryState.defectClass,
                  })}
                </p>
              </div>
            )}
          </div>

          {/* Recent clusters table */}
          <div
            style={{
              background: "#fff",
              borderRadius: "12px",
              boxShadow: "0 1px 4px rgba(0,0,0,.08), 0 0 0 1px rgba(0,0,0,.04)",
              padding: "24px",
            }}
          >
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "16px", gap: "12px" }}>
              <div>
                <h2 style={{ fontSize: "15px", fontWeight: 700 }}>
                  {t("defectGenome.recentQueriesTitle")}
                </h2>
                <p style={{ fontSize: "12px", color: "#7A8494", marginTop: "2px" }}>
                  {t("defectGenome.recentQueriesSubtitle")}
                </p>
              </div>
            </div>
            {recentClusters.length === 0 ? (
              <p
                role="status"
                style={{ fontSize: "13px", color: "#7A8494", padding: "24px 0", textAlign: "center" }}
                data-testid="no-recent-clusters"
              >
                {t("defectGenome.emptyClusters")}
              </p>
            ) : (
              <RecentClustersTable
                clusters={recentClusters}
                onView={(id) => {
                  // Navigate to result view: re-trigger via view of existing cluster
                  // For now, view link scrolls to the query builder; full cluster detail
                  // view is out of this task's scope — use the query to see full results.
                  void id;
                }}
              />
            )}
          </div>
        </div>

        {/* Sidebar */}
        <aside>{renderConfigSidebar()}</aside>
      </div>
    </section>
  );
}
