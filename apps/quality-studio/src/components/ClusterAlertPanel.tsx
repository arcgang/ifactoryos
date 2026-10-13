"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { getDefectGenomeAlerts } from "../services/api";
import type { ClusterAlert, DefectClusterAlertEvent } from "../types/defectGenome";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAINT_ENGINEER_GROUP = "GRP-IFOS-PAINTENGINEER-MUC";
const ROOT_CAUSE_MAX_LEN = 80;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function truncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  return `${text.slice(0, maxLen)}…`;
}

function formatConfidence(score: number): string {
  return `${Math.round(score * 100)}%`;
}

function hasEngineerGroup(groups: string[]): boolean {
  return groups.includes(PAINT_ENGINEER_GROUP);
}

// ---------------------------------------------------------------------------
// SSE / EventSource hook
// ---------------------------------------------------------------------------

const SSE_URL =
  (import.meta as unknown as { env?: Record<string, string> }).env
    ?.VITE_SSE_URL ?? "/api/v1/defect-genome/events";

function useClusterAlertSSE(
  onAlert: (event: DefectClusterAlertEvent) => void,
): void {
  const onAlertRef = useRef(onAlert);
  onAlertRef.current = onAlert;

  useEffect(() => {
    let es: EventSource | null = null;

    function connect(): void {
      es = new EventSource(SSE_URL);

      es.addEventListener("cluster-alert", (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data as string) as DefectClusterAlertEvent;
          onAlertRef.current(data);
        } catch {
          // Malformed SSE payload — ignore.
        }
      });

      es.onerror = () => {
        es?.close();
        es = null;
      };
    }

    connect();

    return () => {
      es?.close();
    };
  }, []);
}

// ---------------------------------------------------------------------------
// Toast notification
// ---------------------------------------------------------------------------

interface ToastProps {
  event: DefectClusterAlertEvent;
  onDismiss: () => void;
  onOpen: (alertId: string) => void;
}

function AlertToast({ event, onDismiss, onOpen }: ToastProps) {
  const { t } = useTranslation();
  const time = new Date(event.triggered_at).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <div
      role="alert"
      aria-live="assertive"
      aria-atomic="true"
      data-testid="cluster-alert-toast"
      style={{
        position: "relative",
        background: "linear-gradient(135deg, #1a0a0a 0%, #1e1208 100%)",
        border: "1px solid #e84545",
        borderLeft: "4px solid #e84545",
        borderRadius: "6px",
        padding: "14px 16px",
        display: "flex",
        alignItems: "flex-start",
        gap: "12px",
        boxShadow: "0 4px 24px rgba(232,69,69,0.25)",
        marginBottom: "12px",
      }}
    >
      <div
        aria-hidden="true"
        style={{
          width: "28px",
          height: "28px",
          background: "#e84545",
          borderRadius: "50%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          fontSize: "14px",
          fontWeight: 900,
          color: "#fff",
          marginTop: "1px",
        }}
      >
        !
      </div>

      <div style={{ flex: 1 }}>
        <div
          style={{
            fontSize: "13px",
            fontWeight: 700,
            color: "#fca5a5",
            marginBottom: "3px",
          }}
        >
          {t("clusterAlerts.toastTitle", {
            defectClass: event.defect_class,
            count: event.affected_vin_count,
          })}
        </div>
        <div
          style={{ fontSize: "11px", color: "#8a9ab0", marginBottom: "8px" }}
        >
          {t("clusterAlerts.toastMeta", {
            alertId: event.alert_id,
            clusterId: event.cluster_id,
            time,
          })}
        </div>

        <div
          style={{
            display: "flex",
            gap: "8px",
            flexWrap: "wrap",
            marginBottom: "10px",
          }}
        >
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "4px",
              padding: "2px 8px",
              borderRadius: "4px",
              fontSize: "11px",
              fontWeight: 600,
              background: "#3d0f0f",
              color: "#fca5a5",
              border: "1px solid #7f1d1d",
            }}
          >
            {event.affected_vin_count} {t("clusterAlerts.colVinCount")}
          </span>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "4px",
              padding: "2px 8px",
              borderRadius: "4px",
              fontSize: "11px",
              fontWeight: 600,
              background: "#2d1c00",
              color: "#fbbf24",
              border: "1px solid #78350f",
            }}
          >
            {formatConfidence(event.confidence_score)}
          </span>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "4px",
              padding: "2px 8px",
              borderRadius: "4px",
              fontSize: "11px",
              fontWeight: 600,
              background: "#0e2040",
              color: "#6db3f2",
              border: "1px solid #1e3a5f",
            }}
          >
            {event.defect_class}
          </span>
        </div>

        <div style={{ display: "flex", gap: "8px" }}>
          <button
            type="button"
            onClick={() => onOpen(event.alert_id)}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "4px 10px",
              borderRadius: "4px",
              fontSize: "11px",
              fontWeight: 600,
              cursor: "pointer",
              border: "none",
              background: "#0062cc",
              color: "#fff",
            }}
          >
            {t("clusterAlerts.openAlert")}
          </button>
          <button
            type="button"
            onClick={onDismiss}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "4px 10px",
              borderRadius: "4px",
              fontSize: "11px",
              fontWeight: 600,
              cursor: "pointer",
              background: "transparent",
              color: "#4a6080",
              border: "1px solid #1e2a3a",
            }}
          >
            {t("clusterAlerts.toastDismiss")}
          </button>
        </div>
      </div>

      <time
        dateTime={event.triggered_at}
        style={{
          position: "absolute",
          top: "10px",
          right: "12px",
          fontSize: "10px",
          color: "#4a6080",
        }}
      >
        {time}
      </time>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Alert row
// ---------------------------------------------------------------------------

function statusBadgeStyle(status: ClusterAlert["status"]): {
  background: string;
  color: string;
} {
  switch (status) {
    case "new":
      return { background: "#3d0f0f", color: "#fca5a5" };
    case "acknowledged":
      return { background: "#0e2040", color: "#6db3f2" };
    case "in_progress":
      return { background: "#2d1c00", color: "#fbbf24" };
    case "resolved":
      return { background: "#052e16", color: "#86efac" };
  }
}

interface AlertRowProps {
  alert: ClusterAlert;
  onClick: (alertId: string) => void;
}

function AlertRow({ alert, onClick }: AlertRowProps) {
  const { t } = useTranslation();
  const badgeStyle = statusBadgeStyle(alert.status);
  const statusKey =
    alert.status === "new"
      ? "statusNew"
      : alert.status === "acknowledged"
        ? "statusAcknowledged"
        : alert.status === "in_progress"
          ? "statusInProgress"
          : "statusResolved";

  const rootCauseFull = alert.root_cause_signature;
  const rootCauseTruncated = truncate(rootCauseFull, ROOT_CAUSE_MAX_LEN);
  const isTruncated = rootCauseTruncated !== rootCauseFull;

  return (
    <tr
      onClick={() => onClick(alert.alert_id)}
      style={{
        cursor: "pointer",
        borderBottom: "1px solid #111d2e",
        background: alert.status === "new" ? "#0f1e30" : undefined,
        borderLeft: alert.status === "new" ? "3px solid #e84545" : undefined,
      }}
      data-testid={`alert-row-${alert.alert_id}`}
    >
      <td
        style={{
          padding: "10px 16px",
          fontFamily: "monospace",
          fontSize: "11px",
          color: "#86efac",
        }}
      >
        {alert.cluster_id.slice(0, 8)}
      </td>
      <td style={{ padding: "10px 16px", fontSize: "12px", color: "#c8d6e5" }}>
        {alert.defect_class}
      </td>
      <td
        style={{
          padding: "10px 16px",
          fontSize: "12px",
          color: "#4a6080",
          textAlign: "center",
        }}
      >
        {alert.affected_vin_count}
      </td>
      <td
        style={{ padding: "10px 16px", fontSize: "12px", color: "#c8d6e5" }}
        title={isTruncated ? rootCauseFull : undefined}
      >
        {rootCauseTruncated}
      </td>
      <td
        style={{
          padding: "10px 16px",
          fontSize: "12px",
          color: "#fbbf24",
          textAlign: "right",
        }}
        aria-label={`${t("clusterAlerts.colConfidence")}: ${formatConfidence(alert.confidence_score)}`}
      >
        {formatConfidence(alert.confidence_score)}
      </td>
      <td style={{ padding: "10px 16px" }}>
        <span
          style={{
            display: "inline-block",
            padding: "2px 8px",
            borderRadius: "10px",
            fontSize: "10px",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            ...badgeStyle,
          }}
          aria-label={`${t("clusterAlerts.colStatus")}: ${t(`clusterAlerts.${statusKey}`)}`}
        >
          {t(`clusterAlerts.${statusKey}`)}
        </span>
      </td>
      <td
        style={{
          padding: "10px 16px",
          fontFamily: "monospace",
          fontSize: "11px",
          color: "#6db3f2",
        }}
      >
        {alert.corrective_action_jira_id ? (
          <a
            href={`https://jira.internal/browse/${alert.corrective_action_jira_id}`}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            aria-label={t("clusterAlerts.jiraTicketLink", {
              jiraId: alert.corrective_action_jira_id,
            })}
            style={{ color: "#6db3f2" }}
          >
            {alert.corrective_action_jira_id}
          </a>
        ) : (
          <span style={{ color: "#4a6080" }}>{t("clusterAlerts.noJira")}</span>
        )}
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// ClusterAlertPanel
// ---------------------------------------------------------------------------

export interface ClusterAlertPanelProps {
  userGroups: string[];
  onOpenAlert: (alertId: string) => void;
}

export function ClusterAlertPanel({
  userGroups,
  onOpenAlert,
}: ClusterAlertPanelProps) {
  const { t } = useTranslation();

  const [alerts, setAlerts] = useState<ClusterAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toasts, setToasts] = useState<DefectClusterAlertEvent[]>([]);
  const [badgeCount, setBadgeCount] = useState(0);

  const isAuthorised = hasEngineerGroup(userGroups);

  const fetchAlerts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await getDefectGenomeAlerts({ status: "active" });
      setAlerts(resp.data);
      const newCount = resp.data.filter((a) => a.status === "new").length;
      setBadgeCount(newCount);
    } catch {
      setError(t("clusterAlerts.error"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void fetchAlerts();
  }, [fetchAlerts]);

  const handleSSEAlert = useCallback((event: DefectClusterAlertEvent) => {
    setToasts((prev) => [...prev, event]);
    setBadgeCount((n) => n + 1);

    const newRow: ClusterAlert = {
      alert_id: event.alert_id,
      cluster_id: event.cluster_id,
      defect_class: event.defect_class,
      status: "new",
      affected_vin_count: event.affected_vin_count,
      root_cause_signature: event.root_cause_signature,
      confidence_score: event.confidence_score,
      most_recent_vin: event.most_recent_vin,
      process_replay_url: event.process_replay_url,
      triggered_at: event.triggered_at,
      created_at: event.triggered_at,
      updated_at: event.triggered_at,
      acknowledged_at: null,
      acknowledged_by_user_id: null,
      corrective_action_jira_id: null,
    };
    setAlerts((prev) => [newRow, ...prev]);
  }, []);

  useClusterAlertSSE(handleSSEAlert);

  function dismissToast(idx: number): void {
    setToasts((prev) => prev.filter((_, i) => i !== idx));
  }

  return (
    <section
      aria-labelledby="cluster-alerts-heading"
      data-testid="cluster-alert-panel"
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 16px",
          borderBottom: "1px solid #1e2a3a",
        }}
      >
        <h2
          id="cluster-alerts-heading"
          style={{
            fontSize: "12px",
            fontWeight: 700,
            color: "#c8d6e5",
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            margin: 0,
          }}
        >
          {t("clusterAlerts.panelTitle")}
          {badgeCount > 0 && (
            <span
              aria-label={`${badgeCount} new`}
              style={{
                marginLeft: "8px",
                background: "#e84545",
                color: "#fff",
                fontSize: "9px",
                fontWeight: 700,
                padding: "1px 5px",
                borderRadius: "8px",
              }}
            >
              {badgeCount}
            </span>
          )}
        </h2>
      </div>

      {/* Toast notifications from SSE */}
      {toasts.length > 0 && (
        <div style={{ padding: "12px 16px 0" }}>
          {toasts.map((toast, idx) => (
            <AlertToast
              key={toast.alert_id}
              event={toast}
              onDismiss={() => dismissToast(idx)}
              onOpen={onOpenAlert}
            />
          ))}
        </div>
      )}

      {loading && (
        <div
          role="status"
          aria-live="polite"
          aria-label={t("clusterAlerts.loading")}
          data-testid="cluster-alerts-loading"
          style={{ padding: "16px", display: "flex", flexDirection: "column", gap: "10px" }}
        >
          <p style={{ color: "#4a6080", fontSize: "12px" }}>
            {t("clusterAlerts.loading")}
          </p>
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              style={{
                height: "14px",
                background: "linear-gradient(90deg, #1a2a3a 25%, #1e3248 50%, #1a2a3a 75%)",
                backgroundSize: "200% 100%",
                borderRadius: "4px",
                width: i === 0 ? "100%" : i === 1 ? "80%" : "60%",
              }}
              aria-hidden="true"
            />
          ))}
        </div>
      )}

      {!loading && error && (
        <div
          style={{
            padding: "16px",
            background: "#1a0a0a",
            border: "1px solid #7f1d1d",
            borderLeft: "4px solid #e84545",
            borderRadius: "6px",
            margin: "12px 16px",
            display: "flex",
            alignItems: "flex-start",
            gap: "12px",
          }}
          role="alert"
          data-testid="cluster-alerts-error"
        >
          <div style={{ flex: 1 }}>
            <p style={{ color: "#fca5a5", fontSize: "13px", fontWeight: 700, margin: "0 0 6px" }}>
              {error}
            </p>
            <button
              type="button"
              onClick={() => void fetchAlerts()}
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
              {t("clusterAlerts.retry")}
            </button>
          </div>
        </div>
      )}

      {!loading && !error && alerts.length === 0 && (
        <p
          role="status"
          data-testid="cluster-alerts-empty"
          style={{ padding: "16px", color: "#4a6080", fontSize: "12px" }}
        >
          {t("clusterAlerts.empty")}
        </p>
      )}

      {!loading && !error && alerts.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table
            aria-label={t("clusterAlerts.panelLabel")}
            style={{ width: "100%", borderCollapse: "collapse" }}
          >
            <thead>
              <tr
                style={{
                  background: "#070f18",
                  borderBottom: "1px solid #1e2a3a",
                }}
              >
                {(
                  [
                    "colClusterId",
                    "colDefectClass",
                    "colVinCount",
                    "colRootCause",
                    "colConfidence",
                    "colStatus",
                    "colJira",
                  ] as const
                ).map((col) => (
                  <th
                    key={col}
                    scope="col"
                    style={{
                      padding: "8px 16px",
                      textAlign: "left",
                      fontSize: "10px",
                      fontWeight: 700,
                      textTransform: "uppercase",
                      letterSpacing: "0.08em",
                      color: "#4a6080",
                    }}
                  >
                    {t(`clusterAlerts.${col}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {alerts.map((alert) => (
                <AlertRow
                  key={alert.alert_id}
                  alert={alert}
                  onClick={onOpenAlert}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!isAuthorised && (
        <p
          style={{
            padding: "8px 16px",
            fontSize: "11px",
            color: "#4a6080",
            borderTop: "1px solid #1e2a3a",
          }}
          aria-live="polite"
        >
          {t("clusterAlerts.notAuthorised")}
        </p>
      )}
    </section>
  );
}
