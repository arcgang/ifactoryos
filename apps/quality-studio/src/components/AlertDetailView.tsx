"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  ApiError,
  acknowledgeClusterAlert,
  createCorrectiveAction,
  getClusterAlert,
} from "../services/api";
import type { ClusterAlert, CorrectiveActionResult } from "../types/defectGenome";
import {
  emitClusterAlertAcknowledged,
  emitClusterAlertViewed,
} from "../instrumentation/adoptionEvents";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAINT_ENGINEER_GROUP = "GRP-IFOS-PAINTENGINEER-MUC";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatConfidence(score: number): string {
  return `${Math.round(score * 100)}%`;
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-GB");
}

function hasEngineerGroup(groups: string[]): boolean {
  return groups.includes(PAINT_ENGINEER_GROUP);
}

// ---------------------------------------------------------------------------
// Field sub-component
// ---------------------------------------------------------------------------

function DetailField({
  label,
  children,
  fullWidth = false,
}: {
  label: string;
  children: ReactNode;
  fullWidth?: boolean;
}) {
  return (
    <div
      style={{
        background: "#0e1c2e",
        border: "1px solid #1e2a3a",
        borderRadius: "4px",
        padding: "10px 12px",
        gridColumn: fullWidth ? "1 / -1" : undefined,
      }}
    >
      <div
        style={{
          fontSize: "10px",
          fontWeight: 700,
          textTransform: "uppercase" as const,
          letterSpacing: "0.08em",
          color: "#2e4460",
          marginBottom: "4px",
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: "13px",
          fontWeight: 600,
          color: "#c8d6e5",
        }}
      >
        {children}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Corrective action form
// ---------------------------------------------------------------------------

interface CorrectiveActionFormProps {
  clusterId: string;
  userId: string;
  onSuccess: (result: CorrectiveActionResult) => void;
  onCancel: () => void;
}

function CorrectiveActionForm({
  clusterId,
  userId,
  onSuccess,
  onCancel,
}: CorrectiveActionFormProps) {
  const { t } = useTranslation();
  const [rank, setRank] = useState<string>("1");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent): Promise<void> {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result = await createCorrectiveAction(clusterId, {
        assigned_root_cause_factor_rank: Number(rank),
        confirmed_by_user_id: userId,
        additional_notes: notes || undefined,
      });
      onSuccess(result);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Already exists — extract jira id from error body if available
        try {
          const body = JSON.parse(err.message) as {
            details?: Array<{ jira_ticket_id?: string }>;
          };
          const jiraId = body.details?.[0]?.jira_ticket_id ?? "";
          if (jiraId) {
            setError(
              t("clusterAlerts.correctiveActionAlreadyExists", { jiraId }),
            );
          } else {
            setError(t("clusterAlerts.correctiveActionAlreadyExists", { jiraId: "" }));
          }
        } catch {
          setError(t("clusterAlerts.correctiveActionError"));
        }
      } else {
        setError(t("clusterAlerts.correctiveActionError"));
      }
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={(e) => void handleSubmit(e)}
      data-testid="corrective-action-form"
      style={{
        background: "#07101a",
        border: "1px solid #1e2a3a",
        borderTop: "3px solid #0062cc",
        borderRadius: "6px",
        padding: "20px",
        marginTop: "4px",
      }}
    >
      <h3
        style={{
          fontSize: "13px",
          fontWeight: 700,
          color: "#c8d6e5",
          marginBottom: "14px",
          marginTop: 0,
        }}
      >
        {t("clusterAlerts.assignCorrectiveAction")}
      </h3>

      {error && (
        <div
          role="alert"
          data-testid="corrective-action-error"
          style={{
            background: "#1a0a0a",
            border: "1px solid #7f1d1d",
            borderLeft: "4px solid #e84545",
            borderRadius: "6px",
            padding: "10px 12px",
            marginBottom: "12px",
            fontSize: "12px",
            color: "#fca5a5",
          }}
        >
          {error}
        </div>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "12px",
          marginBottom: "12px",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "5px" }}>
          <label
            htmlFor="ca-rank"
            style={{
              fontSize: "11px",
              fontWeight: 600,
              color: "#6b7a90",
              textTransform: "uppercase" as const,
              letterSpacing: "0.06em",
            }}
          >
            {t("clusterAlerts.correctiveActionRank")}
          </label>
          <select
            id="ca-rank"
            value={rank}
            onChange={(e) => setRank(e.target.value)}
            required
            style={{
              background: "#0e1c2e",
              border: "1px solid #2a3a50",
              borderRadius: "4px",
              padding: "8px 10px",
              fontSize: "12px",
              color: "#c8d6e5",
            }}
          >
            {[1, 2, 3, 4, 5].map((r) => (
              <option key={r} value={String(r)}>
                {r}
              </option>
            ))}
          </select>
          <span style={{ fontSize: "10px", color: "#2e4460" }}>
            {t("clusterAlerts.correctiveActionRankHint")}
          </span>
        </div>

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "5px",
            gridColumn: "1 / -1",
          }}
        >
          <label
            htmlFor="ca-notes"
            style={{
              fontSize: "11px",
              fontWeight: 600,
              color: "#6b7a90",
              textTransform: "uppercase" as const,
              letterSpacing: "0.06em",
            }}
          >
            {t("clusterAlerts.correctiveActionNotes")}
          </label>
          <textarea
            id="ca-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            style={{
              background: "#0e1c2e",
              border: "1px solid #2a3a50",
              borderRadius: "4px",
              padding: "8px 10px",
              fontSize: "12px",
              color: "#c8d6e5",
              resize: "vertical",
            }}
          />
        </div>
      </div>

      <div
        style={{
          display: "flex",
          gap: "10px",
          marginTop: "16px",
          paddingTop: "14px",
          borderTop: "1px solid #1e2a3a",
        }}
      >
        <button
          type="submit"
          disabled={submitting}
          data-testid="corrective-action-submit"
          style={{
            padding: "6px 14px",
            borderRadius: "4px",
            fontSize: "12px",
            fontWeight: 600,
            cursor: submitting ? "not-allowed" : "pointer",
            border: "none",
            background: submitting ? "#1e2a3a" : "#0062cc",
            color: submitting ? "#4a6080" : "#fff",
          }}
        >
          {t("clusterAlerts.correctiveActionSubmit")}
        </button>
        <button
          type="button"
          onClick={onCancel}
          data-testid="corrective-action-cancel"
          style={{
            padding: "6px 14px",
            borderRadius: "4px",
            fontSize: "12px",
            fontWeight: 600,
            cursor: "pointer",
            background: "#1e2a3a",
            color: "#c8d6e5",
            border: "1px solid #2a3a50",
          }}
        >
          {t("clusterAlerts.correctiveActionCancel")}
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// AlertDetailView
// ---------------------------------------------------------------------------

export interface AlertDetailViewProps {
  alertId: string;
  userId: string;
  userGroups: string[];
  onBack: () => void;
  onAlertUpdated?: (alert: ClusterAlert) => void;
}

export function AlertDetailView({
  alertId,
  userId,
  userGroups,
  onBack,
  onAlertUpdated,
}: AlertDetailViewProps) {
  const { t } = useTranslation();

  const [alert, setAlert] = useState<ClusterAlert | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ackStatus, setAckStatus] = useState<
    "idle" | "submitting" | "success" | "already_acknowledged"
  >("idle");
  const [showCorrectiveForm, setShowCorrectiveForm] = useState(false);
  const [correctiveResult, setCorrectiveResult] =
    useState<CorrectiveActionResult | null>(null);

  const isAuthorised = hasEngineerGroup(userGroups);
  const viewedEmitted = useRef(false);

  const fetchAlert = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getClusterAlert(alertId);
      setAlert(data);
      if (!viewedEmitted.current) {
        viewedEmitted.current = true;
        emitClusterAlertViewed(alertId, data.cluster_id);
      }
    } catch {
      setError(t("clusterAlerts.detailError"));
    } finally {
      setLoading(false);
    }
  }, [alertId, t]);

  useEffect(() => {
    void fetchAlert();
  }, [fetchAlert]);

  async function handleAcknowledge(): Promise<void> {
    if (!alert) return;
    setAckStatus("submitting");
    try {
      const updated = await acknowledgeClusterAlert(alertId, {
        acknowledged_by_user_id: userId,
      });
      setAlert(updated);
      setAckStatus("success");
      emitClusterAlertAcknowledged(alertId, alert.cluster_id);
      onAlertUpdated?.(updated);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setAckStatus("already_acknowledged");
      } else {
        setAckStatus("idle");
      }
    }
  }

  function handleCorrectiveSuccess(result: CorrectiveActionResult): void {
    setCorrectiveResult(result);
    setShowCorrectiveForm(false);
    if (alert) {
      const updated: ClusterAlert = {
        ...alert,
        corrective_action_jira_id: result.jira_ticket_id,
        status: "in_progress",
      };
      setAlert(updated);
      onAlertUpdated?.(updated);
    }
  }

  const canAct =
    isAuthorised && !loading && alert !== null && ackStatus !== "submitting";
  const alreadyAcknowledged =
    alert?.status === "acknowledged" ||
    alert?.status === "in_progress" ||
    alert?.status === "resolved" ||
    ackStatus === "already_acknowledged" ||
    ackStatus === "success";

  return (
    <section
      aria-labelledby="alert-detail-heading"
      data-testid="alert-detail-view"
    >
      <button
        type="button"
        onClick={onBack}
        style={{
          background: "none",
          border: "none",
          color: "#4a6080",
          fontSize: "12px",
          cursor: "pointer",
          padding: "0 0 12px",
          display: "block",
        }}
        data-testid="alert-detail-back"
      >
        {t("clusterAlerts.backToAlerts")}
      </button>

      <h2
        id="alert-detail-heading"
        style={{
          fontSize: "16px",
          fontWeight: 700,
          color: "#e2e8f0",
          marginBottom: "16px",
        }}
      >
        {t("clusterAlerts.detailTitle")}
      </h2>

      {loading && (
        <div
          role="status"
          aria-live="polite"
          aria-label={t("clusterAlerts.detailLoading")}
          data-testid="alert-detail-loading"
          style={{
            background: "#0b1520",
            border: "1px solid #1e2a3a",
            borderRadius: "6px",
            padding: "20px",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              fontSize: "12px",
              color: "#4a6080",
              marginBottom: "16px",
            }}
          >
            <div
              aria-hidden="true"
              style={{
                width: "14px",
                height: "14px",
                border: "2px solid #1e2a3a",
                borderTopColor: "#0062cc",
                borderRadius: "50%",
              }}
            />
            {t("clusterAlerts.detailLoading")}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {[40, 70, 60, 55, 65].map((w, i) => (
              <div
                key={i}
                style={{
                  height: i === 0 ? "18px" : "64px",
                  background:
                    "linear-gradient(90deg, #1a2a3a 25%, #1e3248 50%, #1a2a3a 75%)",
                  backgroundSize: "200% 100%",
                  borderRadius: "4px",
                  width: `${w}%`,
                }}
                aria-hidden="true"
              />
            ))}
          </div>
        </div>
      )}

      {!loading && error && (
        <div
          role="alert"
          data-testid="alert-detail-error"
          style={{
            background: "#1a0a0a",
            border: "1px solid #7f1d1d",
            borderLeft: "4px solid #e84545",
            borderRadius: "6px",
            padding: "16px",
          }}
        >
          <p
            style={{
              color: "#fca5a5",
              fontSize: "13px",
              fontWeight: 700,
              margin: "0 0 8px",
            }}
          >
            {error}
          </p>
          <button
            type="button"
            onClick={() => void fetchAlert()}
            data-testid="alert-detail-retry"
            style={{
              padding: "4px 10px",
              borderRadius: "4px",
              fontSize: "11px",
              fontWeight: 600,
              cursor: "pointer",
              background: "#1e2a3a",
              color: "#c8d6e5",
              border: "1px solid #2a3a50",
            }}
          >
            {t("clusterAlerts.detailRetry")}
          </button>
        </div>
      )}

      {!loading && !error && alert && (
        <div>
          {/* Detail card */}
          <div
            style={{
              background: "#0b1520",
              border: "1px solid #1e2a3a",
              borderRadius: "6px",
              padding: "20px",
              marginBottom: "16px",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
                justifyContent: "space-between",
                marginBottom: "16px",
                paddingBottom: "16px",
                borderBottom: "1px solid #1e2a3a",
              }}
            >
              <div>
                <div
                  style={{
                    fontSize: "10px",
                    fontWeight: 700,
                    letterSpacing: "0.1em",
                    textTransform: "uppercase" as const,
                    color: "#4a6080",
                    marginBottom: "4px",
                  }}
                >
                  {t("clusterAlerts.fieldClusterId")}
                </div>
                <div
                  style={{
                    fontSize: "18px",
                    fontWeight: 700,
                    color: "#fca5a5",
                    marginBottom: "6px",
                    fontFamily: "monospace",
                  }}
                >
                  {alert.cluster_id}
                </div>
                <div style={{ fontSize: "11px", color: "#4a6080" }}>
                  {formatDateTime(alert.triggered_at)}
                </div>
              </div>

              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "flex-end",
                  gap: "6px",
                }}
              >
                <span
                  style={{
                    display: "inline-block",
                    padding: "2px 8px",
                    borderRadius: "10px",
                    fontSize: "10px",
                    fontWeight: 700,
                    textTransform: "uppercase" as const,
                    letterSpacing: "0.05em",
                    background:
                      alert.status === "new"
                        ? "#3d0f0f"
                        : alert.status === "in_progress"
                          ? "#2d1c00"
                          : alert.status === "acknowledged"
                            ? "#0e2040"
                            : "#052e16",
                    color:
                      alert.status === "new"
                        ? "#fca5a5"
                        : alert.status === "in_progress"
                          ? "#fbbf24"
                          : alert.status === "acknowledged"
                            ? "#6db3f2"
                            : "#86efac",
                  }}
                >
                  {t(
                    `clusterAlerts.status${
                      alert.status === "new"
                        ? "New"
                        : alert.status === "acknowledged"
                          ? "Acknowledged"
                          : alert.status === "in_progress"
                            ? "InProgress"
                            : "Resolved"
                    }`,
                  )}
                </span>
              </div>
            </div>

            {/* Detail grid */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: "12px",
                marginBottom: "16px",
              }}
            >
              <DetailField label={t("clusterAlerts.fieldAffectedVins")}>
                <span
                  style={{
                    fontSize: "22px",
                    fontWeight: 800,
                    color: "#fca5a5",
                  }}
                >
                  {alert.affected_vin_count}
                </span>
              </DetailField>

              <DetailField label={t("clusterAlerts.fieldConfidence")}>
                <span
                  style={{
                    fontSize: "22px",
                    fontWeight: 800,
                    color: "#fbbf24",
                  }}
                  aria-label={`${t("clusterAlerts.fieldConfidence")}: ${formatConfidence(alert.confidence_score)}`}
                >
                  {formatConfidence(alert.confidence_score)}
                </span>
              </DetailField>

              <DetailField label={t("clusterAlerts.fieldDefectClass")}>
                {alert.defect_class}
              </DetailField>

              <DetailField label={t("clusterAlerts.fieldTriggeredAt")}>
                <time dateTime={alert.triggered_at}>
                  {formatDateTime(alert.triggered_at)}
                </time>
              </DetailField>

              <DetailField
                label={t("clusterAlerts.fieldRootCause")}
                fullWidth
              >
                <span
                  style={{
                    fontFamily: "monospace",
                    fontSize: "11px",
                    color: "#86efac",
                    wordBreak: "break-all",
                    lineHeight: 1.6,
                  }}
                >
                  {alert.root_cause_signature}
                </span>
              </DetailField>

              <DetailField
                label={t("processReplay.launch")}
                fullWidth
              >
                <a
                  href={alert.process_replay_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  data-testid="process-replay-link"
                  style={{ color: "#6db3f2", fontSize: "12px" }}
                >
                  {t("clusterAlerts.openProcessReplay")}
                </a>
              </DetailField>

              {alert.corrective_action_jira_id && (
                <DetailField label={t("clusterAlerts.colJira")} fullWidth>
                  <a
                    href={`https://jira.internal/browse/${alert.corrective_action_jira_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    data-testid="jira-link"
                    aria-label={t("clusterAlerts.jiraTicketLink", {
                      jiraId: alert.corrective_action_jira_id,
                    })}
                    style={{ color: "#6db3f2", fontSize: "12px" }}
                  >
                    {alert.corrective_action_jira_id}
                  </a>
                </DetailField>
              )}
            </div>

            {/* Actions */}
            <div
              style={{
                display: "flex",
                gap: "10px",
                paddingTop: "14px",
                borderTop: "1px solid #1e2a3a",
                flexWrap: "wrap",
              }}
            >
              {!alreadyAcknowledged && (
                <button
                  type="button"
                  disabled={!canAct}
                  onClick={() => void handleAcknowledge()}
                  data-testid="acknowledge-button"
                  aria-label={t("clusterAlerts.acknowledgeButton")}
                  style={{
                    padding: "6px 14px",
                    borderRadius: "4px",
                    fontSize: "12px",
                    fontWeight: 600,
                    cursor: canAct ? "pointer" : "not-allowed",
                    border: "none",
                    background: canAct ? "#0062cc" : "#1e2a3a",
                    color: canAct ? "#fff" : "#4a6080",
                  }}
                >
                  {t("clusterAlerts.acknowledgeButton")}
                </button>
              )}

              {!showCorrectiveForm && !correctiveResult && (
                <button
                  type="button"
                  disabled={!canAct}
                  onClick={() => setShowCorrectiveForm(true)}
                  data-testid="assign-corrective-action-button"
                  aria-label={t("clusterAlerts.assignCorrectiveAction")}
                  style={{
                    padding: "6px 14px",
                    borderRadius: "4px",
                    fontSize: "12px",
                    fontWeight: 600,
                    cursor: canAct ? "pointer" : "not-allowed",
                    background: canAct ? "#1e2a3a" : "#111d2e",
                    color: canAct ? "#c8d6e5" : "#4a6080",
                    border: "1px solid #2a3a50",
                  }}
                >
                  {t("clusterAlerts.assignCorrectiveAction")}
                </button>
              )}
            </div>
          </div>

          {/* Acknowledge feedback */}
          {ackStatus === "success" && (
            <div
              role="status"
              data-testid="acknowledge-success"
              style={{
                background: "linear-gradient(135deg, #052e16, #0a1f12)",
                border: "1px solid #14532d",
                borderLeft: "4px solid #4ade80",
                borderRadius: "6px",
                padding: "14px 16px",
                marginBottom: "12px",
                fontSize: "13px",
                color: "#86efac",
                fontWeight: 700,
              }}
            >
              {t("clusterAlerts.acknowledgeSuccess")}
            </div>
          )}

          {ackStatus === "already_acknowledged" && (
            <div
              role="status"
              data-testid="acknowledge-already"
              style={{
                background: "#0e2040",
                border: "1px solid #1e3a5f",
                borderLeft: "4px solid #6db3f2",
                borderRadius: "6px",
                padding: "14px 16px",
                marginBottom: "12px",
                fontSize: "13px",
                color: "#6db3f2",
              }}
            >
              {t("clusterAlerts.acknowledgedAlready")}
            </div>
          )}

          {/* Corrective action result */}
          {correctiveResult && (
            <div
              role="status"
              data-testid="corrective-action-result"
              style={{
                background: "linear-gradient(135deg, #052e16, #0a1f12)",
                border: "1px solid #14532d",
                borderLeft: "4px solid #4ade80",
                borderRadius: "6px",
                padding: "14px 16px",
                marginBottom: "12px",
              }}
            >
              <p
                style={{
                  fontSize: "13px",
                  fontWeight: 700,
                  color: "#86efac",
                  margin: "0 0 4px",
                }}
              >
                {t("clusterAlerts.correctiveActionSuccess", {
                  jiraId: correctiveResult.jira_ticket_id,
                })}
              </p>
              <a
                href={correctiveResult.jira_ticket_url}
                target="_blank"
                rel="noopener noreferrer"
                data-testid="corrective-action-jira-link"
                aria-label={t("clusterAlerts.jiraTicketLink", {
                  jiraId: correctiveResult.jira_ticket_id,
                })}
                style={{ color: "#6db3f2", fontSize: "12px" }}
              >
                {correctiveResult.jira_ticket_id}
              </a>
            </div>
          )}

          {/* Corrective action form */}
          {showCorrectiveForm && (
            <CorrectiveActionForm
              clusterId={alert.cluster_id}
              userId={userId}
              onSuccess={handleCorrectiveSuccess}
              onCancel={() => setShowCorrectiveForm(false)}
            />
          )}
        </div>
      )}
    </section>
  );
}
