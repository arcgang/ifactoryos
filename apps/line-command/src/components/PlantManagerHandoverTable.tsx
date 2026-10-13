import { Fragment, useCallback, useEffect, useState, type CSSProperties, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { apiRequest } from "../services/api";

// ---------------------------------------------------------------------------
// Types — aligned with HandoverRecord in LLD
// ---------------------------------------------------------------------------

interface AlertDispositionItem {
  alertId: string;
  disposition: "resolved" | "escalated" | "monitoring";
  note: string | null;
}

export interface HandoverRecord {
  handoverId: string;
  shiftId: string;
  lineId: string;
  shiftStartTs: string;
  shiftEndTs: string;
  submittedByUserId: string;
  alertDispositions: AlertDispositionItem[];
  oeeDeltaPct: number;
  heldVins: string[];
  notes: string | null;
  createdAt: string;
  updatedAt: string | null;
}

interface HandoverListResponse {
  data: HandoverRecord[];
  page: { next_cursor: string | null; limit: number };
}

type TablePhase = "loading" | "loaded" | "error" | "load-more-pending";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTs(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// Expanded row detail
// ---------------------------------------------------------------------------

interface ExpandedRowProps {
  record: HandoverRecord;
}

function ExpandedRow({ record }: ExpandedRowProps) {
  const { t } = useTranslation();

  return (
    <tr>
      <td
        colSpan={5}
        style={{
          background: "var(--lc-surface, #13151f)",
          borderBottom: "1px solid var(--lc-border, #272b3d)",
          padding: "14px 16px",
        }}
      >
        {/* Alert dispositions */}
        <div style={{ marginBottom: "12px" }}>
          <h3
            style={{
              color: "var(--lc-text-muted, #7b83a3)",
              fontSize: "10px",
              fontWeight: 700,
              letterSpacing: ".08em",
              margin: "0 0 8px",
              textTransform: "uppercase",
            }}
          >
            {t("handover_builder.section_alerts")}
          </h3>
          {record.alertDispositions.length === 0 ? (
            <p style={{ color: "var(--lc-text-muted, #7b83a3)", fontSize: "11px", margin: 0 }}>
              {t("handover_builder.alerts_empty")}
            </p>
          ) : (
            <ul style={{ display: "flex", flexDirection: "column", gap: "6px", listStyle: "none", margin: 0, padding: 0 }}>
              {record.alertDispositions.map((d) => (
                <li
                  key={d.alertId}
                  style={{
                    alignItems: "flex-start",
                    background: "var(--lc-card, #1a1d2a)",
                    border: "1px solid var(--lc-border, #272b3d)",
                    borderRadius: "5px",
                    display: "flex",
                    gap: "10px",
                    padding: "8px 10px",
                  }}
                >
                  <span
                    style={{
                      background:
                        d.disposition === "resolved"
                          ? "rgba(34,197,94,.12)"
                          : d.disposition === "escalated"
                            ? "rgba(239,68,68,.12)"
                            : "rgba(245,158,11,.12)",
                      border: `1px solid ${
                        d.disposition === "resolved"
                          ? "rgba(34,197,94,.3)"
                          : d.disposition === "escalated"
                            ? "rgba(239,68,68,.3)"
                            : "rgba(245,158,11,.3)"
                      }`,
                      borderRadius: "4px",
                      color:
                        d.disposition === "resolved"
                          ? "var(--lc-good, #22c55e)"
                          : d.disposition === "escalated"
                            ? "var(--lc-crit, #ef4444)"
                            : "var(--lc-warn, #f59e0b)",
                      flexShrink: 0,
                      fontSize: "9px",
                      fontWeight: 700,
                      letterSpacing: ".07em",
                      padding: "2px 7px",
                      textTransform: "uppercase",
                    }}
                  >
                    {d.disposition}
                  </span>
                  <div>
                    <div style={{ color: "var(--lc-text-muted, #7b83a3)", fontFamily: "monospace", fontSize: "10px" }}>
                      {d.alertId}
                    </div>
                    {d.note && (
                      <div style={{ color: "var(--lc-text, #eef1ff)", fontSize: "11px", marginTop: "2px" }}>
                        {d.note}
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Held VINs */}
        {record.heldVins.length > 0 && (
          <div style={{ marginBottom: "12px" }}>
            <h3
              style={{
                color: "var(--lc-text-muted, #7b83a3)",
                fontSize: "10px",
                fontWeight: 700,
                letterSpacing: ".08em",
                margin: "0 0 8px",
                textTransform: "uppercase",
              }}
            >
              {t("handover_builder.section_vins")}
            </h3>
            <ul style={{ display: "flex", flexWrap: "wrap", gap: "6px", listStyle: "none", margin: 0, padding: 0 }}>
              {record.heldVins.map((vin) => (
                <li key={vin}>
                  <span
                    style={{
                      background: "rgba(124,58,237,.12)",
                      border: "1px solid rgba(124,58,237,.3)",
                      borderRadius: "4px",
                      color: "#c4b5fd",
                      fontFamily: "monospace",
                      fontSize: "11px",
                      fontWeight: 700,
                      padding: "3px 8px",
                    }}
                  >
                    {vin}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Notes */}
        {record.notes && (
          <div>
            <h3
              style={{
                color: "var(--lc-text-muted, #7b83a3)",
                fontSize: "10px",
                fontWeight: 700,
                letterSpacing: ".08em",
                margin: "0 0 6px",
                textTransform: "uppercase",
              }}
            >
              {t("handover_builder.notes_label")}
            </h3>
            <p style={{ color: "var(--lc-text, #eef1ff)", fontSize: "12px", lineHeight: 1.6, margin: 0 }}>
              {record.notes}
            </p>
          </div>
        )}
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// PlantManagerHandoverTable
// ---------------------------------------------------------------------------

interface Props {
  lineId?: string;
  fromTs?: string;
  toTs?: string;
}

export function PlantManagerHandoverTable({ lineId, fromTs, toTs }: Props) {
  const { t } = useTranslation();

  const [records, setRecords] = useState<HandoverRecord[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [phase, setPhase] = useState<TablePhase>("loading");
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // ── Initial fetch ─────────────────────────────────────────────────────────

  useEffect(() => {
    const controller = new AbortController();
    setPhase("loading");
    setError(null);
    setRecords([]);
    setNextCursor(null);
    setExpandedId(null);

    const qs = new URLSearchParams({ limit: "20" });
    if (lineId) qs.set("lineId", lineId);
    if (fromTs) qs.set("fromTs", fromTs);
    if (toTs) qs.set("toTs", toTs);

    apiRequest<HandoverListResponse>(`/api/v1/handovers?${qs.toString()}`, {
      signal: controller.signal,
    })
      .then((res) => {
        if (controller.signal.aborted) return;
        const items = Array.isArray(res) ? res : (res?.data ?? []);
        const page = Array.isArray(res) ? null : res?.page;
        setRecords(items);
        setNextCursor(page?.next_cursor ?? null);
        setPhase("loaded");
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : t("handover_builder.error"));
        setPhase("error");
      });

    return () => controller.abort();
  }, [lineId, fromTs, toTs, t]);

  // ── Load more ─────────────────────────────────────────────────────────────

  const handleLoadMore = useCallback(async () => {
    if (!nextCursor) return;
    setPhase("load-more-pending");

    const qs = new URLSearchParams({ limit: "20", cursor: nextCursor });
    if (lineId) qs.set("lineId", lineId);
    if (fromTs) qs.set("fromTs", fromTs);
    if (toTs) qs.set("toTs", toTs);

    try {
      const res = await apiRequest<HandoverListResponse>(`/api/v1/handovers?${qs.toString()}`);
      const items = Array.isArray(res) ? res : (res?.data ?? []);
      const page = Array.isArray(res) ? null : res?.page;
      setRecords((prev) => [...prev, ...items]);
      setNextCursor(page?.next_cursor ?? null);
      setPhase("loaded");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("handover_builder.error"));
      setPhase("loaded"); // revert to loaded so user can retry
    }
  }, [nextCursor, lineId, fromTs, toTs, t]);

  // ── Row click ─────────────────────────────────────────────────────────────

  const handleRowClick = useCallback((id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  }, []);

  const handleRowKeyDown = useCallback(
    (e: KeyboardEvent, id: string) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        handleRowClick(id);
      }
    },
    [handleRowClick],
  );

  // ── Render ────────────────────────────────────────────────────────────────

  const thStyle: CSSProperties = {
    borderBottom: "1px solid var(--lc-border, #272b3d)",
    color: "var(--lc-text-muted, #7b83a3)",
    fontSize: "10px",
    fontWeight: 700,
    letterSpacing: ".07em",
    padding: "8px 12px",
    textAlign: "left",
    textTransform: "uppercase",
    whiteSpace: "nowrap",
  };

  const tdStyle: CSSProperties = {
    borderBottom: "1px solid var(--lc-border, #272b3d)",
    fontSize: "12px",
    padding: "10px 12px",
    verticalAlign: "middle",
  };

  if (phase === "loading") {
    return (
      <div
        role="status"
        aria-busy="true"
        aria-live="polite"
        aria-label={t("plant_manager.loading")}
        style={{ padding: "20px 24px" }}
      >
        {[1, 2, 3, 4].map((n) => (
          <div
            key={n}
            aria-hidden="true"
            style={{
              animation: "lc-shimmer 1.8s infinite linear",
              background: "linear-gradient(90deg,#1e2233 25%,#252840 50%,#1e2233 75%)",
              backgroundSize: "400% 100%",
              borderRadius: "6px",
              height: "36px",
              marginBottom: "8px",
            }}
          />
        ))}
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div
        role="alert"
        style={{
          alignItems: "flex-start",
          background: "rgba(239,68,68,.12)",
          border: "1px solid rgba(239,68,68,.3)",
          borderRadius: "6px",
          display: "flex",
          gap: "10px",
          margin: "20px 24px",
          padding: "14px 16px",
        }}
      >
        <div style={{ color: "var(--lc-crit, #ef4444)", fontSize: "13px", fontWeight: 700 }}>
          {t("plant_manager.error_title")}
        </div>
        <div style={{ color: "var(--lc-text-muted, #7b83a3)", fontSize: "11px", marginTop: "2px" }}>
          {error}
        </div>
      </div>
    );
  }

  if (records.length === 0 && phase === "loaded") {
    return (
      <div
        style={{
          alignItems: "center",
          display: "flex",
          flexDirection: "column",
          gap: "8px",
          justifyContent: "center",
          minHeight: "200px",
          padding: "32px 20px",
          textAlign: "center",
        }}
      >
        <div aria-hidden="true" style={{ color: "var(--lc-text-dim, #4a5070)", fontSize: "32px" }}>
          ○
        </div>
        <div style={{ color: "var(--lc-text, #eef1ff)", fontSize: "13px", fontWeight: 600 }}>
          {t("plant_manager.empty_title")}
        </div>
        <div style={{ color: "var(--lc-text-muted, #7b83a3)", fontSize: "11px", maxWidth: "240px" }}>
          {t("plant_manager.empty_sub")}
        </div>
      </div>
    );
  }

  return (
    <section aria-labelledby="pm-table-heading" style={{ overflowY: "auto", padding: "20px 24px" }}>
      <h2
        id="pm-table-heading"
        style={{
          color: "var(--lc-text-muted, #7b83a3)",
          fontSize: "10px",
          fontWeight: 700,
          letterSpacing: ".1em",
          margin: "0 0 12px",
          textTransform: "uppercase",
        }}
      >
        {t("plant_manager.table_title")}
      </h2>

      <div style={{ overflowX: "auto" }}>
        <table
          aria-label={t("plant_manager.table_aria_label")}
          style={{ borderCollapse: "collapse", fontSize: "12px", width: "100%" }}
        >
          <thead>
            <tr style={{ background: "var(--lc-surface, #13151f)" }}>
              <th scope="col" style={thStyle}>{t("plant_manager.col_submitted_by")}</th>
              <th scope="col" style={thStyle}>{t("plant_manager.col_shift_start")}</th>
              <th scope="col" style={thStyle}>{t("plant_manager.col_shift_end")}</th>
              <th scope="col" style={thStyle}>{t("plant_manager.col_alert_count")}</th>
              <th scope="col" style={thStyle}>{t("plant_manager.col_oee_delta")}</th>
            </tr>
          </thead>
          <tbody>
            {records.map((record) => {
              const isExpanded = expandedId === record.handoverId;
              const deltaPositive = record.oeeDeltaPct >= 0;
              return (
                <Fragment key={record.handoverId}>
                  <tr
                    role="row"
                    aria-expanded={isExpanded}
                    tabIndex={0}
                    onClick={() => handleRowClick(record.handoverId)}
                    onKeyDown={(e) => handleRowKeyDown(e, record.handoverId)}
                    style={{
                      background: isExpanded ? "var(--lc-surface, #13151f)" : "transparent",
                      borderBottom: isExpanded ? "none" : "1px solid var(--lc-border, #272b3d)",
                      cursor: "pointer",
                    }}
                  >
                    <td style={tdStyle}>
                      <span style={{ fontFamily: "monospace", fontSize: "11px" }}>
                        {record.submittedByUserId}
                      </span>
                    </td>
                    <td style={tdStyle}>{formatTs(record.shiftStartTs)}</td>
                    <td style={tdStyle}>{formatTs(record.shiftEndTs)}</td>
                    <td style={tdStyle}>{record.alertDispositions.length}</td>
                    <td style={{ ...tdStyle, fontVariantNumeric: "tabular-nums", fontWeight: 700 }}>
                      <span
                        aria-label={
                          deltaPositive
                            ? t("handover_builder.oee_delta_above", { value: Math.abs(record.oeeDeltaPct).toFixed(1) })
                            : t("handover_builder.oee_delta_below", { value: Math.abs(record.oeeDeltaPct).toFixed(1) })
                        }
                        style={{
                          color: deltaPositive ? "var(--lc-good, #22c55e)" : "var(--lc-crit, #ef4444)",
                        }}
                      >
                        {deltaPositive ? "+" : "−"}{Math.abs(record.oeeDeltaPct).toFixed(1)} pp
                      </span>
                    </td>
                  </tr>
                  {isExpanded && (
                    <ExpandedRow record={record} />
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Load more */}
      {nextCursor && (
        <div style={{ marginTop: "14px", textAlign: "center" }}>
          <button
            type="button"
            onClick={() => void handleLoadMore()}
            disabled={phase === "load-more-pending"}
            aria-disabled={phase === "load-more-pending"}
            style={{
              background: "transparent",
              border: "1px solid var(--lc-border-light, #32374f)",
              borderRadius: "6px",
              color: phase === "load-more-pending" ? "var(--lc-text-muted, #7b83a3)" : "var(--lc-text, #eef1ff)",
              cursor: phase === "load-more-pending" ? "wait" : "pointer",
              fontSize: "12px",
              fontWeight: 600,
              padding: "7px 18px",
            }}
          >
            {phase === "load-more-pending" ? t("plant_manager.load_more_pending") : t("plant_manager.load_more")}
          </button>
        </div>
      )}

      {/* Load-more error */}
      {error && phase === "loaded" && (
        <div
          role="alert"
          style={{
            color: "var(--lc-crit, #ef4444)",
            fontSize: "11px",
            marginTop: "8px",
            textAlign: "center",
          }}
        >
          {error}
        </div>
      )}
    </section>
  );
}
