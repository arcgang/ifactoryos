import { type MutableRefObject, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { apiRequest } from "../services/api";

// ---------------------------------------------------------------------------
// OTel adoption event stub
// ---------------------------------------------------------------------------

function emitAdoptionEvent(name: string, attributes: Record<string, string | number | boolean>): void {
  try {
    performance.mark(`${name}:start`);
    performance.mark(`${name}:end`);
    performance.measure(name, {
      start: `${name}:start`,
      end: `${name}:end`,
      detail: { ...attributes, topic: "factory.munich.line-command.adoption.v1" },
    });
  } catch {
    // best-effort
  }
}

// ---------------------------------------------------------------------------
// API types — aligned with LLD contract
// ---------------------------------------------------------------------------

export interface OeeSnapshot {
  lineId: string;
  oeePercent: number;
  availability: number;
  performance: number;
  quality: number;
  measuredAt: string;
  dataAgeSeconds: number;
}

interface OeeSnapshotListResponse {
  data: OeeSnapshot[];
  page: { next_cursor: string | null; limit: number };
}

export interface AndonAlert {
  alertId: string;
  stationId: string;
  lineId: string;
  alertType: "predictive" | "reactive";
  predictedFailureType: string;
  minutesToPredictedFailure: number | null;
  recommendedAction: string;
  status: "open" | "acknowledged" | "resolved" | "false_positive";
  detectedAt: string;
  resolvedAt: string | null;
  acknowledgedAt: string | null;
  opcenterEventId: string | null;
}

interface AndonAlertListResponse {
  data: AndonAlert[];
  page: { next_cursor: string | null; limit: number };
}

export interface Handover {
  handoverId: string;
  lineId: string | null;
  submittedAt: string;
  acknowledged: boolean;
  alertId?: string;
}

interface HandoverListResponse {
  data: Handover[];
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function fetchOeeSnapshots(signal: AbortSignal): Promise<OeeSnapshot[]> {
  const res = await apiRequest<OeeSnapshotListResponse>("/api/v1/oee/snapshots?limit=200", { signal });
  const items = Array.isArray(res) ? res : (res?.data ?? []);
  return items;
}

async function fetchAndonAlerts(signal: AbortSignal): Promise<AndonAlert[]> {
  const res = await apiRequest<AndonAlertListResponse>(
    "/api/v1/line-command/andon/alerts?status=open&limit=200",
    { signal },
  );
  const items = Array.isArray(res) ? res : (res?.data ?? []);
  return items;
}

async function fetchHandovers(lineId: string | undefined, signal: AbortSignal): Promise<Handover[]> {
  const qs = lineId ? `?lineId=${encodeURIComponent(lineId)}&limit=1` : "?limit=1";
  try {
    const res = await apiRequest<HandoverListResponse>(`/api/v1/handovers${qs}`, { signal });
    const items = Array.isArray(res) ? res : (res?.data ?? []);
    return items;
  } catch {
    return [];
  }
}

async function acknowledgeAlert(alertId: string, disposition: "resolved" | "escalated" | "monitoring", stationId: string): Promise<void> {
  await apiRequest(`/api/v1/andon/alerts/${encodeURIComponent(alertId)}/acknowledge`, {
    method: "POST",
    body: JSON.stringify({ disposition, stationId }),
  });
}

// ---------------------------------------------------------------------------
// OEE tile helpers
// ---------------------------------------------------------------------------

type OeeStatus = "good" | "warn" | "crit";

const OEE_TARGET = 85;

function oeeStatus(oee: number): OeeStatus {
  if (oee >= OEE_TARGET) return "good";
  if (oee >= 70) return "warn";
  return "crit";
}

// ---------------------------------------------------------------------------
// OEE Tile
// ---------------------------------------------------------------------------

interface OeeTileProps {
  snapshot: OeeSnapshot;
  alertCount: number;
}

function OeeTile({ snapshot, alertCount }: OeeTileProps) {
  const { t } = useTranslation();
  const status = oeeStatus(snapshot.oeePercent);
  const stale = snapshot.dataAgeSeconds > 30;

  const statusColour: Record<OeeStatus, string> = {
    good: "var(--lc-good, #22c55e)",
    warn: "var(--lc-warn, #f59e0b)",
    crit: "var(--lc-crit, #ef4444)",
  };

  const statusLabel: Record<OeeStatus, string> = {
    good: t("command_center.oee_status_above"),
    warn: t("command_center.oee_status_below"),
    crit: t("command_center.oee_status_critical"),
  };

  const colour = statusColour[status];

  return (
    <article
      role="region"
      aria-label={t("command_center.oee_tile_label", { lineId: snapshot.lineId })}
      style={{
        background: "var(--lc-card, #1a1d2a)",
        border: `1px solid ${stale ? "rgba(239,68,68,.4)" : "var(--lc-border, #272b3d)"}`,
        borderRadius: "8px",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          alignItems: "center",
          borderBottom: "1px solid var(--lc-border, #272b3d)",
          display: "flex",
          justifyContent: "space-between",
          padding: "10px 14px",
        }}
      >
        <div>
          <div style={{ fontSize: "11px", fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase" }}>
            {snapshot.lineId}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "4px" }}>
          <span
            style={{
              background: status === "good" ? "rgba(34,197,94,.12)" : status === "warn" ? "rgba(245,158,11,.12)" : "rgba(239,68,68,.12)",
              borderRadius: "3px",
              color: colour,
              fontSize: "9px",
              fontWeight: 700,
              letterSpacing: ".08em",
              padding: "2px 7px",
              textTransform: "uppercase",
            }}
          >
            {statusLabel[status]}
          </span>
          {stale && (
            <span
              role="status"
              aria-label={t("command_center.oee_stale_label", { seconds: snapshot.dataAgeSeconds })}
              style={{
                background: "rgba(239,68,68,.1)",
                border: "1px solid rgba(239,68,68,.3)",
                borderRadius: "4px",
                color: "var(--lc-crit, #ef4444)",
                fontSize: "9px",
                fontWeight: 600,
                padding: "2px 6px",
              }}
            >
              {t("command_center.oee_stale_badge", { seconds: snapshot.dataAgeSeconds })}
            </span>
          )}
        </div>
      </div>
      <div style={{ padding: "14px" }}>
        <span
          style={{
            display: "block",
            fontSize: "36px",
            fontVariantNumeric: "tabular-nums",
            fontWeight: 700,
            letterSpacing: "-.02em",
            lineHeight: 1,
            marginBottom: "6px",
            color: colour,
          }}
          aria-label={t("command_center.oee_percent_label", { value: snapshot.oeePercent.toFixed(1) })}
        >
          {snapshot.oeePercent.toFixed(1)}
          <span style={{ fontSize: "16px", fontWeight: 400, color: "var(--lc-text-muted, #7b83a3)" }}>%</span>
        </span>

        <div
          style={{
            borderTop: "1px solid var(--lc-border, #272b3d)",
            display: "grid",
            fontSize: "10px",
            gridTemplateColumns: "repeat(3,1fr)",
            marginTop: "10px",
            paddingTop: "10px",
          }}
        >
          {(
            [
              { key: "availability", label: t("command_center.oee_avail") },
              { key: "performance", label: t("command_center.oee_perf") },
              { key: "quality", label: t("command_center.oee_quality") },
            ] as const
          ).map(({ key, label }) => (
            <div key={key} style={{ textAlign: "center" }}>
              <span
                style={{
                  display: "block",
                  fontSize: "13px",
                  fontVariantNumeric: "tabular-nums",
                  fontWeight: 600,
                  marginBottom: "2px",
                  color: colour,
                }}
              >
                {(snapshot[key] as number).toFixed(1)}%
              </span>
              <span style={{ color: "var(--lc-text-muted, #7b83a3)" }}>{label}</span>
            </div>
          ))}
        </div>

        {alertCount > 0 && (
          <div
            style={{
              color: "var(--lc-warn, #f59e0b)",
              fontSize: "9px",
              fontWeight: 600,
              marginTop: "5px",
            }}
          >
            {t("command_center.oee_open_alerts", { count: alertCount })}
          </div>
        )}
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// Alert countdown — decrements each 60 s client-side, resyncs when
// initialMinutes changes (i.e. the parent re-polls and gets a fresh value)
// ---------------------------------------------------------------------------

interface CountdownProps {
  initialMinutes: number | null;
  serverResyncRef: MutableRefObject<number>;
}

function AlertCountdown({ initialMinutes, serverResyncRef }: CountdownProps) {
  const { t } = useTranslation();
  const [minutes, setMinutes] = useState<number | null>(initialMinutes);

  // Resync to server value whenever the parent re-polls
  const lastResyncSeenRef = useRef(serverResyncRef.current);
  useEffect(() => {
    if (serverResyncRef.current !== lastResyncSeenRef.current) {
      lastResyncSeenRef.current = serverResyncRef.current;
    }
    setMinutes(initialMinutes);
  }, [initialMinutes, serverResyncRef]);

  // Tick down every 60 s between server polls
  useEffect(() => {
    if (minutes === null) return;

    const id = setInterval(() => {
      setMinutes((prev) => (prev !== null && prev > 0 ? prev - 1 : prev));
    }, 60_000);

    return () => clearInterval(id);
  }, [initialMinutes]);

  if (minutes === null) return null;

  const urgent = minutes <= 5;
  const warning = minutes <= 15;

  return (
    <span
      aria-live="polite"
      aria-label={t("andon.minutes_until_failure", { count: String(minutes) })}
      style={{
        alignItems: "center",
        background: urgent
          ? "rgba(239,68,68,.12)"
          : warning
            ? "rgba(245,158,11,.12)"
            : "rgba(96,165,250,.12)",
        border: `1px solid ${urgent ? "rgba(239,68,68,.3)" : warning ? "rgba(245,158,11,.3)" : "rgba(96,165,250,.3)"}`,
        borderRadius: "4px",
        color: urgent
          ? "var(--lc-crit, #ef4444)"
          : warning
            ? "var(--lc-warn, #f59e0b)"
            : "var(--lc-advisory, #60a5fa)",
        display: "inline-flex",
        fontSize: "11px",
        fontVariantNumeric: "tabular-nums",
        fontWeight: 700,
        gap: "5px",
        padding: "2px 9px",
      }}
    >
      ⏱ {minutes} {t("command_center.min_abbrev")}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Handover queue item
// ---------------------------------------------------------------------------

interface HandoverItemProps {
  handover: Handover;
  onAcknowledge: (handoverId: string) => void;
}

function HandoverQueueItem({ handover, onAcknowledge }: HandoverItemProps) {
  const { t } = useTranslation();

  return (
    <li
      style={{
        background: "rgba(124,58,237,.07)",
        borderBottom: "1px solid var(--lc-border, #272b3d)",
        borderLeft: "3px solid var(--lc-badge-new, #7c3aed)",
        listStyle: "none",
        padding: "14px 16px",
      }}
    >
      <div style={{ alignItems: "center", display: "flex", gap: "8px", marginBottom: "6px" }}>
        <span
          role="status"
          style={{
            background: "rgba(124,58,237,.18)",
            border: "1px solid rgba(124,58,237,.4)",
            borderRadius: "3px",
            color: "#c4b5fd",
            fontSize: "9px",
            fontWeight: 700,
            letterSpacing: ".08em",
            padding: "2px 7px",
            textTransform: "uppercase",
          }}
        >
          {t("command_center.new_handover_badge")}
        </span>
      </div>
      <div style={{ color: "var(--lc-text-muted, #7b83a3)", fontSize: "10px", marginBottom: "8px" }}>
        {t("command_center.handover_submitted_at", {
          time: new Date(handover.submittedAt).toLocaleTimeString(undefined, {
            hour: "2-digit",
            minute: "2-digit",
          }),
        })}
      </div>
      <div style={{ display: "flex", gap: "8px" }}>
        <button
          type="button"
          onClick={() => onAcknowledge(handover.handoverId)}
          style={{
            background: "rgba(124,58,237,.2)",
            border: "1px solid rgba(124,58,237,.4)",
            borderRadius: "5px",
            color: "#c4b5fd",
            cursor: "pointer",
            fontSize: "10px",
            fontWeight: 600,
            padding: "4px 10px",
          }}
        >
          {t("command_center.acknowledge")}
        </button>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Alert queue item
// ---------------------------------------------------------------------------

interface AlertCardProps {
  alert: AndonAlert;
  serverResyncRef: MutableRefObject<number>;
  onAcknowledge: (alertId: string) => void;
}

function AlertQueueItem({ alert, serverResyncRef, onAcknowledge }: AlertCardProps) {
  const { t } = useTranslation();
  const urgent = (alert.minutesToPredictedFailure ?? Infinity) <= 5;
  const warning = !urgent && (alert.minutesToPredictedFailure ?? Infinity) <= 15;

  return (
    <li
      style={{
        background: urgent
          ? "rgba(239,68,68,.07)"
          : warning
            ? "rgba(245,158,11,.07)"
            : "transparent",
        borderBottom: "1px solid var(--lc-border, #272b3d)",
        borderLeft: `3px solid ${urgent ? "var(--lc-crit, #ef4444)" : warning ? "var(--lc-warn, #f59e0b)" : "var(--lc-advisory, #60a5fa)"}`,
        listStyle: "none",
        padding: "14px 16px",
      }}
    >
      <div style={{ alignItems: "center", display: "flex", gap: "8px", marginBottom: "6px" }}>
        {alert.minutesToPredictedFailure !== null && (
          <AlertCountdown
            initialMinutes={alert.minutesToPredictedFailure}
            serverResyncRef={serverResyncRef}
          />
        )}
        <span
          style={{
            color: "var(--lc-text-muted, #7b83a3)",
            fontSize: "9px",
            fontWeight: 600,
            letterSpacing: ".08em",
            marginLeft: "auto",
            textTransform: "uppercase",
          }}
        >
          {alert.alertType === "predictive"
            ? t("andon.predictive_label")
            : t("andon.reactive_label")}
        </span>
      </div>

      <div style={{ alignItems: "center", display: "flex", gap: "8px", marginBottom: "4px" }}>
        <span style={{ fontSize: "12px", fontWeight: 700 }}>{alert.stationId}</span>
        <span
          style={{
            background: "var(--lc-surface, #13151f)",
            border: "1px solid var(--lc-border-light, #32374f)",
            borderRadius: "3px",
            color: "var(--lc-text-muted, #7b83a3)",
            fontSize: "10px",
            padding: "1px 6px",
          }}
        >
          {alert.lineId}
        </span>
      </div>

      <div
        style={{
          color: "var(--lc-text, #eef1ff)",
          fontSize: "11px",
          fontWeight: 600,
          letterSpacing: ".04em",
          marginBottom: "4px",
          textTransform: "uppercase",
        }}
      >
        {alert.predictedFailureType}
      </div>

      <div
        style={{
          color: "var(--lc-text-muted, #7b83a3)",
          fontSize: "10px",
          lineHeight: 1.5,
          marginBottom: "8px",
        }}
      >
        {alert.recommendedAction}
      </div>

      <div style={{ display: "flex", gap: "8px" }}>
        <button
          type="button"
          onClick={() => onAcknowledge(alert.alertId)}
          style={{
            background: "var(--lc-bmw-blue, #0066b1)",
            border: "none",
            borderRadius: "5px",
            color: "#fff",
            cursor: "pointer",
            fontSize: "10px",
            fontWeight: 600,
            padding: "4px 10px",
          }}
        >
          {t("command_center.acknowledge")}
        </button>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// CommandCenterPage
// ---------------------------------------------------------------------------

interface Props {
  lineId?: string;
}

const OEE_POLL_MS = 25_000;
const ALERTS_POLL_MS = 60_000;

export function CommandCenterPage({ lineId }: Props) {
  const { t } = useTranslation();

  // OEE state
  const [oeeSnapshots, setOeeSnapshots] = useState<OeeSnapshot[]>([]);
  const [oeeLoading, setOeeLoading] = useState(true);
  const [oeeError, setOeeError] = useState<string | null>(null);

  // Alerts state
  const [alerts, setAlerts] = useState<AndonAlert[]>([]);
  const [alertsLoading, setAlertsLoading] = useState(true);
  const [alertsError, setAlertsError] = useState<string | null>(null);

  // Handover state
  const [pendingHandover, setPendingHandover] = useState<Handover | null>(null);

  // Tracks how many times alerts have been re-polled, so countdown components can resync
  const alertResyncRef = useRef(0);

  // Track OTel load event — emitted once per mount
  const loadEventFiredRef = useRef(false);
  const mountTimeRef = useRef(performance.now());

  // ── OEE fetcher ────────────────────────────────────────────────────────────

  const loadOee = useCallback(async (signal: AbortSignal) => {
    try {
      const data = await fetchOeeSnapshots(signal);
      if (signal.aborted) return;
      setOeeSnapshots(data);
      setOeeError(null);
    } catch (err) {
      if (signal.aborted) return;
      setOeeError(err instanceof Error ? err.message : "Failed to load OEE data");
    } finally {
      if (!signal.aborted) setOeeLoading(false);
    }
  }, []);

  // ── Alerts fetcher ─────────────────────────────────────────────────────────

  const loadAlerts = useCallback(async (signal: AbortSignal) => {
    try {
      const data = await fetchAndonAlerts(signal);
      if (signal.aborted) return;
      setAlerts(data);
      setAlertsError(null);
      alertResyncRef.current += 1;
    } catch (err) {
      if (signal.aborted) return;
      setAlertsError(err instanceof Error ? err.message : "Failed to load alerts");
    } finally {
      if (!signal.aborted) setAlertsLoading(false);
    }
  }, []);

  // ── OEE polling ────────────────────────────────────────────────────────────

  useEffect(() => {
    const controller = new AbortController();
    void loadOee(controller.signal);

    const id = setInterval(() => {
      void loadOee(controller.signal);
    }, OEE_POLL_MS);

    return () => {
      controller.abort();
      clearInterval(id);
    };
  }, [loadOee]);

  // ── Alerts polling ─────────────────────────────────────────────────────────

  useEffect(() => {
    const controller = new AbortController();
    void loadAlerts(controller.signal);

    const id = setInterval(() => {
      void loadAlerts(controller.signal);
    }, ALERTS_POLL_MS);

    return () => {
      controller.abort();
      clearInterval(id);
    };
  }, [loadAlerts]);

  // ── Handover fetch ─────────────────────────────────────────────────────────

  useEffect(() => {
    const controller = new AbortController();

    fetchHandovers(lineId, controller.signal).then((handovers) => {
      if (controller.signal.aborted) return;
      const unacked = handovers.find((h) => !h.acknowledged);
      setPendingHandover(unacked ?? null);
    });

    return () => controller.abort();
  }, [lineId]);

  // ── OTel adoption event — once, within 1 s of interactive ─────────────────

  useEffect(() => {
    if (loadEventFiredRef.current) return;
    if (oeeLoading || alertsLoading) return;

    loadEventFiredRef.current = true;
    const elapsed = performance.now() - mountTimeRef.current;
    emitAdoptionEvent("command_center_load", {
      oeeCount: oeeSnapshots.length,
      alertCount: alerts.length,
      hasHandover: pendingHandover !== null,
      elapsedMs: Math.round(elapsed),
    });
  }, [oeeLoading, alertsLoading, oeeSnapshots.length, alerts.length, pendingHandover]);

  // ── Handlers ───────────────────────────────────────────────────────────────

  const handleAcknowledgeAlert = useCallback(async (alertId: string, stationId: string) => {
    try {
      await acknowledgeAlert(alertId, "resolved", stationId);
      setAlerts((prev) => prev.filter((a) => a.alertId !== alertId));
    } catch {
      // leave the alert in place; the user can retry
    }
  }, []);

  const handleAcknowledgeHandover = useCallback(async (_handoverId: string) => {
    try {
      if (pendingHandover?.alertId) {
        // Find the alert with this ID to get its stationId
        const alert = alerts.find((a) => a.alertId === pendingHandover.alertId);
        if (alert) {
          await acknowledgeAlert(pendingHandover.alertId, "resolved", alert.stationId);
        }
      }
    } catch {
      // best-effort
    }
    // Clear optimistically regardless of API result
    setPendingHandover(null);
  }, [pendingHandover, alerts]);

  const handleRetryOee = useCallback(() => {
    const controller = new AbortController();
    setOeeLoading(true);
    void loadOee(controller.signal);
  }, [loadOee]);

  // ── Alert count per line (for tile annotations) ────────────────────────────

  const alertsByLine = alerts.reduce<Record<string, number>>((acc, a) => {
    acc[a.lineId] = (acc[a.lineId] ?? 0) + 1;
    return acc;
  }, {});

  // ── Render ─────────────────────────────────────────────────────────────────

  const isLoading = oeeLoading && alertsLoading;
  const openAlertCount = alerts.length;

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 380px",
        height: "100%",
        minHeight: "560px",
        overflow: "hidden",
      }}
    >
      {/* ── OEE panel ──────────────────────────────────────────────────────── */}
      <section
        aria-labelledby="oee-heading"
        style={{
          borderRight: "1px solid var(--lc-border, #272b3d)",
          overflowY: "auto",
          padding: "20px",
        }}
      >
        <div
          style={{
            alignItems: "center",
            borderBottom: "1px solid var(--lc-border, #272b3d)",
            display: "flex",
            gap: "8px",
            justifyContent: "space-between",
            paddingBottom: "12px",
            marginBottom: "16px",
          }}
        >
          <h2
            id="oee-heading"
            style={{
              color: "var(--lc-text-muted, #7b83a3)",
              fontSize: "10px",
              fontWeight: 700,
              letterSpacing: ".12em",
              margin: 0,
              textTransform: "uppercase",
            }}
          >
            {t("command_center.oee_card_title")}
          </h2>

          {!oeeLoading && !oeeError && (
            <span
              role="status"
              aria-live="polite"
              style={{
                alignItems: "center",
                background: "rgba(34,197,94,.12)",
                border: "1px solid rgba(34,197,94,.25)",
                borderRadius: "4px",
                color: "var(--lc-good, #22c55e)",
                display: "inline-flex",
                fontSize: "10px",
                fontWeight: 600,
                gap: "5px",
                padding: "2px 8px",
              }}
            >
              <span
                aria-hidden="true"
                style={{
                  animation: "lc-pulse 2s infinite",
                  background: "currentColor",
                  borderRadius: "50%",
                  height: "6px",
                  width: "6px",
                }}
              />
              {t("command_center.oee_live_badge")}
            </span>
          )}
        </div>

        {/* Error banner */}
        {oeeError && (
          <div
            role="alert"
            style={{
              alignItems: "flex-start",
              background: "rgba(239,68,68,.12)",
              border: "1px solid rgba(239,68,68,.3)",
              borderRadius: "6px",
              display: "flex",
              gap: "12px",
              marginBottom: "16px",
              padding: "14px 16px",
            }}
          >
            <div
              aria-hidden="true"
              style={{
                background: "var(--lc-crit, #ef4444)",
                borderRadius: "50%",
                color: "#fff",
                flexShrink: 0,
                fontSize: "12px",
                fontWeight: 700,
                height: "20px",
                lineHeight: "20px",
                textAlign: "center",
                width: "20px",
              }}
            >
              !
            </div>
            <div>
              <div
                style={{
                  color: "var(--lc-crit, #ef4444)",
                  fontSize: "12px",
                  fontWeight: 700,
                  marginBottom: "3px",
                }}
              >
                {t("command_center.oee_error_title")}
              </div>
              <div
                style={{
                  color: "var(--lc-text-muted, #7b83a3)",
                  fontSize: "11px",
                  lineHeight: 1.5,
                }}
              >
                {oeeError}
              </div>
              <button
                type="button"
                onClick={handleRetryOee}
                style={{
                  background: "var(--lc-crit, #ef4444)",
                  border: "none",
                  borderRadius: "4px",
                  color: "#fff",
                  cursor: "pointer",
                  fontSize: "11px",
                  fontWeight: 600,
                  marginTop: "8px",
                  padding: "5px 14px",
                }}
              >
                {t("command_center.retry_oee")}
              </button>
            </div>
          </div>
        )}

        {/* OEE grid — loading skeleton */}
        {oeeLoading ? (
          <div
            style={{
              display: "grid",
              gap: "12px",
              gridTemplateColumns: "1fr 1fr",
            }}
            aria-label={t("command_center.oee_loading")}
            aria-busy="true"
          >
            {[1, 2, 3, 4].map((n) => (
              <div
                key={n}
                aria-hidden="true"
                style={{
                  background: "var(--lc-card, #1a1d2a)",
                  border: "1px solid var(--lc-border, #272b3d)",
                  borderRadius: "8px",
                  padding: "14px",
                }}
              >
                <div
                  style={{
                    animation: "lc-shimmer 1.8s infinite linear",
                    background: "linear-gradient(90deg,#1e2233 25%,#252840 50%,#1e2233 75%)",
                    backgroundSize: "400% 100%",
                    borderRadius: "4px",
                    height: "38px",
                    marginBottom: "8px",
                    width: "80px",
                  }}
                />
                <div
                  style={{
                    animation: "lc-shimmer 1.8s infinite linear",
                    background: "linear-gradient(90deg,#1e2233 25%,#252840 50%,#1e2233 75%)",
                    backgroundSize: "400% 100%",
                    borderRadius: "4px",
                    height: "10px",
                    marginBottom: "6px",
                    width: "100%",
                  }}
                />
              </div>
            ))}
          </div>
        ) : (
          /* OEE grid — populated (or stale) */
          <div
            role="list"
            aria-label={t("command_center.oee_grid_label")}
            style={{
              display: "grid",
              gap: "12px",
              gridTemplateColumns: "1fr 1fr",
              opacity: oeeError ? 0.35 : 1,
            }}
          >
            {oeeSnapshots.length === 0 && !oeeError ? (
              <div
                style={{
                  alignItems: "center",
                  display: "flex",
                  flexDirection: "column",
                  gap: "8px",
                  gridColumn: "1 / -1",
                  justifyContent: "center",
                  minHeight: "200px",
                  padding: "32px 20px",
                  textAlign: "center",
                }}
              >
                <div style={{ color: "var(--lc-text-muted, #7b83a3)", fontSize: "11px" }}>
                  {t("command_center.oee_empty")}
                </div>
              </div>
            ) : (
              oeeSnapshots.map((snap) => (
                <OeeTile
                  key={snap.lineId}
                  snapshot={snap}
                  alertCount={alertsByLine[snap.lineId] ?? 0}
                />
              ))
            )}
          </div>
        )}
      </section>

      {/* ── Alert queue sidebar ────────────────────────────────────────────── */}
      <aside
        style={{
          display: "flex",
          flexDirection: "column",
          overflowY: "auto",
        }}
      >
        <div
          style={{
            alignItems: "center",
            borderBottom: "1px solid var(--lc-border, #272b3d)",
            display: "flex",
            gap: "8px",
            justifyContent: "space-between",
            padding: "12px 16px",
          }}
        >
          <h2
            style={{
              color: "var(--lc-text-muted, #7b83a3)",
              fontSize: "10px",
              fontWeight: 700,
              letterSpacing: ".12em",
              margin: 0,
              textTransform: "uppercase",
            }}
          >
            {t("command_center.andon_open_title")}
          </h2>
          {!alertsLoading && (
            <span
              style={{
                background:
                  openAlertCount === 0
                    ? "rgba(34,197,94,.12)"
                    : "rgba(239,68,68,.12)",
                border: `1px solid ${openAlertCount === 0 ? "rgba(34,197,94,.3)" : "rgba(239,68,68,.3)"}`,
                borderRadius: "10px",
                color:
                  openAlertCount === 0
                    ? "var(--lc-good, #22c55e)"
                    : "var(--lc-crit, #ef4444)",
                fontSize: "10px",
                fontWeight: 700,
                padding: "1px 8px",
              }}
            >
              {openAlertCount === 0
                ? t("command_center.alerts_clear")
                : t("command_center.andon_active", { count: openAlertCount })}
            </span>
          )}
        </div>

        {/* Loading skeleton */}
        {isLoading && (
          <div aria-busy="true" aria-label={t("command_center.alerts_loading")}>
            {[1, 2, 3].map((n) => (
              <div
                key={n}
                aria-hidden="true"
                style={{
                  borderBottom: "1px solid var(--lc-border, #272b3d)",
                  padding: "14px 16px",
                }}
              >
                <div
                  style={{
                    animation: "lc-shimmer 1.8s infinite linear",
                    background: "linear-gradient(90deg,#1e2233 25%,#252840 50%,#1e2233 75%)",
                    backgroundSize: "400% 100%",
                    borderRadius: "4px",
                    height: "10px",
                    marginBottom: "6px",
                    width: "100%",
                  }}
                />
                <div
                  style={{
                    animation: "lc-shimmer 1.8s infinite linear",
                    background: "linear-gradient(90deg,#1e2233 25%,#252840 50%,#1e2233 75%)",
                    backgroundSize: "400% 100%",
                    borderRadius: "4px",
                    height: "10px",
                    width: "70%",
                  }}
                />
              </div>
            ))}
          </div>
        )}

        {/* Alert list */}
        {!isLoading && (
          <ul
            role="list"
            aria-label={t("command_center.alert_queue_label")}
            style={{ flex: 1, listStyle: "none", margin: 0, padding: 0 }}
          >
            {/* Handover as first item */}
            {pendingHandover && (
              <HandoverQueueItem
                handover={pendingHandover}
                onAcknowledge={(id) => void handleAcknowledgeHandover(id)}
              />
            )}

            {/* Alerts in server order (sorted by minutesToPredictedFailure ASC) */}
            {alerts.map((alert) => (
              <AlertQueueItem
                key={alert.alertId}
                alert={alert}
                serverResyncRef={alertResyncRef}
                onAcknowledge={(id) => void handleAcknowledgeAlert(id, alert.stationId)}
              />
            ))}

            {/* Empty state */}
            {!pendingHandover && alerts.length === 0 && (
              <li
                style={{
                  alignItems: "center",
                  display: "flex",
                  flexDirection: "column",
                  gap: "8px",
                  justifyContent: "center",
                  listStyle: "none",
                  minHeight: "200px",
                  padding: "32px 20px",
                  textAlign: "center",
                }}
              >
                <div
                  aria-hidden="true"
                  style={{ color: "var(--lc-text-dim, #4a5070)", fontSize: "32px" }}
                >
                  ✓
                </div>
                <div
                  style={{
                    color: "var(--lc-text, #eef1ff)",
                    fontSize: "13px",
                    fontWeight: 600,
                  }}
                >
                  {t("command_center.alerts_empty_title")}
                </div>
                <div
                  style={{
                    color: "var(--lc-text-muted, #7b83a3)",
                    fontSize: "11px",
                    lineHeight: 1.6,
                    maxWidth: "240px",
                  }}
                >
                  {t("command_center.alerts_empty_sub")}
                </div>
              </li>
            )}
          </ul>
        )}

        {/* Alerts error */}
        {alertsError && !isLoading && (
          <div
            role="alert"
            style={{
              borderTop: "1px solid var(--lc-border, #272b3d)",
              color: "var(--lc-crit, #ef4444)",
              fontSize: "11px",
              padding: "10px 16px",
            }}
          >
            {alertsError}
          </div>
        )}
      </aside>

    </div>
  );
}
