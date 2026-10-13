import { useCallback, useEffect, useId, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { apiRequest, ForbiddenError } from "../services/api";
import type { AndonAlert, OeeSnapshot } from "./CommandCenterPage";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type DispositionValue = "resolved" | "escalated" | "monitoring";

interface DispositionEntry {
  alertId: string;
  disposition: DispositionValue | "";
  note: string;
}

type FormPhase = "loading" | "form" | "submitting" | "submitted" | "error";

interface SubmitResult {
  handoverId: string;
  submittedAt: string;
  submittedByUserId: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const VIN_RE = /^[A-HJ-NPR-Z0-9]{17}$/;

function generateUuid(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // fallback for non-secure contexts
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// ---------------------------------------------------------------------------
// DispositionSelect
// ---------------------------------------------------------------------------

interface DispositionSelectProps {
  alertId: string;
  value: DispositionValue | "";
  onChange: (alertId: string, value: DispositionValue) => void;
  selectId: string;
}

function DispositionSelect({ alertId, value, onChange, selectId }: DispositionSelectProps) {
  const { t } = useTranslation();

  return (
    <select
      id={selectId}
      value={value}
      aria-required="true"
      onChange={(e) => onChange(alertId, e.target.value as DispositionValue)}
      style={{
        background:
          value === "resolved"
            ? "rgba(34,197,94,.12)"
            : value === "escalated"
              ? "rgba(239,68,68,.12)"
              : value === "monitoring"
                ? "rgba(245,158,11,.12)"
                : "var(--lc-card, #1a1d2a)",
        border: `1px solid ${
          value === "resolved"
            ? "rgba(34,197,94,.4)"
            : value === "escalated"
              ? "rgba(239,68,68,.4)"
              : value === "monitoring"
                ? "rgba(245,158,11,.4)"
                : "var(--lc-border, #272b3d)"
        }`,
        borderRadius: "5px",
        color:
          value === "resolved"
            ? "var(--lc-good, #22c55e)"
            : value === "escalated"
              ? "var(--lc-crit, #ef4444)"
              : value === "monitoring"
                ? "var(--lc-warn, #f59e0b)"
                : "var(--lc-text, #eef1ff)",
        fontSize: "12px",
        padding: "5px 8px",
        width: "100%",
      }}
    >
      <option value="">{t("handover_builder.disposition_placeholder")}</option>
      <option value="resolved">{t("handover_builder.disposition_resolved")}</option>
      <option value="escalated">{t("handover_builder.disposition_escalated")}</option>
      <option value="monitoring">{t("handover_builder.disposition_monitoring")}</option>
    </select>
  );
}

// ---------------------------------------------------------------------------
// HandoverBuilderPage
// ---------------------------------------------------------------------------

interface Props {
  oeeSnapshots?: OeeSnapshot[];
  userId?: string;
}

export function HandoverBuilderPage({ oeeSnapshots = [], userId = "" }: Props) {
  const { t } = useTranslation();
  const formLabelId = useId();
  const notesId = useId();
  const vinInputId = useId();

  // Idempotency key generated once per mount — survives retries, cleared on success
  const idempotencyKeyRef = useRef<string>(generateUuid());

  // Form phase
  const [phase, setPhase] = useState<FormPhase>("loading");

  // Alerts fetched from shift
  const [shiftAlerts, setShiftAlerts] = useState<AndonAlert[]>([]);
  const [alertsError, setAlertsError] = useState<string | null>(null);

  // Alert dispositions — keyed by alertId
  const [dispositions, setDispositions] = useState<Record<string, DispositionEntry>>({});

  // OEE delta — composite from the snapshots passed in
  const [oeeDeltaPct, setOeeDeltaPct] = useState<string>(() => {
    if (!oeeSnapshots.length) return "0";
    const avg = oeeSnapshots.reduce((s, o) => s + o.oeePercent, 0) / oeeSnapshots.length;
    return avg.toFixed(1);
  });

  // Held VINs
  const [heldVins, setHeldVins] = useState<string[]>([]);
  const [vinInput, setVinInput] = useState("");
  const [vinError, setVinError] = useState<string | null>(null);

  // Notes
  const [notes, setNotes] = useState("");

  // Shift metadata — in production sourced from OpCenter session; using placeholders here
  const shiftId = useRef<string>(generateUuid());
  const lineId = "WELD-LINE-01";
  const shiftStartTs = useRef<string>(new Date(Date.now() - 8 * 3600 * 1000).toISOString());
  const shiftEndTs = useRef<string>(new Date().toISOString());

  // Submit result + error
  const [submitResult, setSubmitResult] = useState<SubmitResult | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // ── Fetch open alerts on mount ────────────────────────────────────────────

  useEffect(() => {
    const controller = new AbortController();

    apiRequest<{ data: AndonAlert[]; page: { next_cursor: string | null; limit: number } }>(
      "/api/v1/line-command/andon/alerts?status=open&limit=200",
      { signal: controller.signal },
    )
      .then((res) => {
        if (controller.signal.aborted) return;
        const items = Array.isArray(res) ? res : (res?.data ?? []);
        setShiftAlerts(items);
        // Seed dispositions from fetched alerts — all empty to start
        setDispositions(
          Object.fromEntries(
            items.map((a) => [
              a.alertId,
              { alertId: a.alertId, disposition: "" as const, note: "" },
            ]),
          ),
        );
        setPhase("form");
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setAlertsError(err instanceof Error ? err.message : "Failed to load alerts");
        setPhase("form"); // show the form anyway so user can proceed with empty dispositions
      });

    return () => controller.abort();
  }, []);

  // ── Disposition handlers ──────────────────────────────────────────────────

  const handleDispositionChange = useCallback(
    (alertId: string, value: DispositionValue) => {
      setDispositions((prev) => ({
        ...prev,
        [alertId]: { ...prev[alertId]!, disposition: value },
      }));
    },
    [],
  );

  const handleNoteChange = useCallback((alertId: string, note: string) => {
    setDispositions((prev) => ({
      ...prev,
      [alertId]: { ...prev[alertId]!, note },
    }));
  }, []);

  // ── VIN handlers ──────────────────────────────────────────────────────────

  const handleAddVin = useCallback(() => {
    const vin = vinInput.trim().toUpperCase();
    if (!VIN_RE.test(vin)) {
      setVinError(t("handover_builder.vin_invalid"));
      return;
    }
    if (heldVins.includes(vin)) {
      setVinError(t("handover_builder.vin_duplicate"));
      return;
    }
    setVinError(null);
    setHeldVins((prev) => [...prev, vin]);
    setVinInput("");
  }, [vinInput, heldVins, t]);

  const handleRemoveVin = useCallback((vin: string) => {
    setHeldVins((prev) => prev.filter((v) => v !== vin));
  }, []);

  const handleVinKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleAddVin();
      }
    },
    [handleAddVin],
  );

  // ── Submit ────────────────────────────────────────────────────────────────

  const allDispositionsComplete = shiftAlerts.every(
    (a) => (dispositions[a.alertId]?.disposition ?? "") !== "",
  );
  const canSubmit = allDispositionsComplete && phase === "form";

  const handleSubmit = useCallback(async () => {
    if (!canSubmit) return;

    setPhase("submitting");
    setSubmitError(null);

    const payload = {
      shiftId: shiftId.current,
      lineId,
      shiftStartTs: shiftStartTs.current,
      shiftEndTs: shiftEndTs.current,
      alertDispositions: shiftAlerts.map((a) => ({
        alertId: a.alertId,
        disposition: dispositions[a.alertId]?.disposition ?? "monitoring",
        note: dispositions[a.alertId]?.note || undefined,
      })),
      oeeDeltaPct: parseFloat(oeeDeltaPct) || 0,
      heldVins,
      notes: notes.trim() || undefined,
      idempotencyKey: idempotencyKeyRef.current,
    };

    try {
      const result = await apiRequest<SubmitResult>("/api/v1/handover", {
        method: "POST",
        body: JSON.stringify(payload),
        userId,
      });
      setSubmitResult(result);
      setPhase("submitted");
    } catch (err) {
      if (err instanceof ForbiddenError) {
        setSubmitError(t("handover_builder.error_forbidden"));
      } else if (err instanceof Error && err.message.includes("409")) {
        setSubmitError(t("handover_builder.error_conflict"));
      } else {
        setSubmitError(err instanceof Error ? err.message : t("handover_builder.error"));
      }
      setPhase("form");
    }
  }, [canSubmit, shiftAlerts, dispositions, oeeDeltaPct, heldVins, notes, userId, t]);

  // ── Render helpers ────────────────────────────────────────────────────────

  const cellStyle: CSSProperties = {
    border: "1px solid var(--lc-border, #272b3d)",
    fontSize: "11px",
    padding: "8px 10px",
    verticalAlign: "middle",
  };

  // ── Render ────────────────────────────────────────────────────────────────

  if (phase === "submitted" && submitResult) {
    return (
      <div style={{ padding: "28px 24px" }}>
        <div
          role="status"
          aria-live="polite"
          style={{
            background: "rgba(34,197,94,.07)",
            border: "1px solid rgba(34,197,94,.3)",
            borderRadius: "8px",
            padding: "24px 28px",
            textAlign: "center",
            maxWidth: "520px",
            margin: "0 auto",
          }}
        >
          <div
            aria-hidden="true"
            style={{
              background: "var(--lc-good, #22c55e)",
              borderRadius: "50%",
              color: "#fff",
              fontSize: "22px",
              height: "44px",
              lineHeight: "44px",
              margin: "0 auto 12px",
              width: "44px",
            }}
          >
            ✓
          </div>
          <h2
            style={{
              color: "var(--lc-good, #22c55e)",
              fontSize: "16px",
              fontWeight: 800,
              margin: "0 0 6px",
            }}
          >
            {t("handover_builder.success")}
          </h2>
          <p style={{ color: "var(--lc-text-muted, #7b83a3)", fontSize: "12px", margin: "0 0 14px" }}>
            {t("handover_builder.success_sub")}
          </p>
          <dl
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "16px",
              justifyContent: "center",
              margin: 0,
            }}
          >
            <div style={{ textAlign: "center" }}>
              <dt style={{ color: "var(--lc-text-muted, #7b83a3)", fontSize: "10px", fontWeight: 700, textTransform: "uppercase" }}>
                {t("handover_builder.field_handover_id")}
              </dt>
              <dd style={{ color: "var(--lc-text, #eef1ff)", fontFamily: "monospace", fontSize: "11px", fontWeight: 600, margin: 0 }}>
                {submitResult.handoverId}
              </dd>
            </div>
            <div style={{ textAlign: "center" }}>
              <dt style={{ color: "var(--lc-text-muted, #7b83a3)", fontSize: "10px", fontWeight: 700, textTransform: "uppercase" }}>
                {t("handover_builder.field_submitted_at")}
              </dt>
              <dd style={{ color: "var(--lc-text, #eef1ff)", fontSize: "12px", fontWeight: 600, margin: 0 }}>
                {new Date(submitResult.submittedAt).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    );
  }

  if (phase === "loading") {
    return (
      <div style={{ padding: "28px 24px" }}>
        <div
          role="status"
          aria-busy="true"
          aria-live="polite"
          aria-label={t("handover_builder.loading_alerts")}
          style={{
            alignItems: "center",
            color: "var(--lc-text-muted, #7b83a3)",
            display: "flex",
            fontSize: "13px",
            gap: "10px",
            padding: "12px 0",
          }}
        >
          <span
            aria-hidden="true"
            style={{
              animation: "lc-shimmer 1.8s infinite linear",
              background: "var(--lc-border, #272b3d)",
              borderRadius: "50%",
              display: "inline-block",
              height: "18px",
              width: "18px",
            }}
          />
          {t("handover_builder.loading_alerts")}
        </div>
        {[1, 2, 3].map((n) => (
          <div
            key={n}
            aria-hidden="true"
            style={{
              animation: "lc-shimmer 1.8s infinite linear",
              background: "linear-gradient(90deg,#1e2233 25%,#252840 50%,#1e2233 75%)",
              backgroundSize: "400% 100%",
              borderRadius: "6px",
              height: "40px",
              marginBottom: "10px",
            }}
          />
        ))}
      </div>
    );
  }

  return (
    <div style={{ overflowY: "auto", padding: "20px 24px 72px" }}>
      {/* Page heading */}
      <header style={{ marginBottom: "20px" }}>
        <h1
          id={formLabelId}
          style={{
            color: "var(--lc-text, #eef1ff)",
            fontSize: "17px",
            fontWeight: 700,
            margin: "0 0 4px",
          }}
        >
          {t("handover_builder.title")}
        </h1>
        <p style={{ color: "var(--lc-text-muted, #7b83a3)", fontSize: "12px", margin: 0 }}>
          {lineId}
        </p>
      </header>

      {/* Alerts-fetch error */}
      {alertsError && (
        <div
          role="alert"
          style={{
            background: "rgba(239,68,68,.12)",
            border: "1px solid rgba(239,68,68,.3)",
            borderRadius: "6px",
            color: "var(--lc-crit, #ef4444)",
            fontSize: "12px",
            marginBottom: "16px",
            padding: "10px 14px",
          }}
        >
          {alertsError}
        </div>
      )}

      {/* Submit error */}
      {submitError && (
        <div
          role="alert"
          style={{
            background: "rgba(239,68,68,.12)",
            border: "1px solid rgba(239,68,68,.3)",
            borderRadius: "6px",
            color: "var(--lc-crit, #ef4444)",
            fontSize: "12px",
            marginBottom: "16px",
            padding: "10px 14px",
          }}
        >
          {submitError}
        </div>
      )}

      {/* Incomplete alert banner */}
      {shiftAlerts.length > 0 && !allDispositionsComplete && (
        <div
          role="status"
          style={{
            alignItems: "center",
            background: "rgba(245,158,11,.1)",
            border: "1px solid rgba(245,158,11,.3)",
            borderRadius: "6px",
            color: "var(--lc-warn, #f59e0b)",
            display: "flex",
            fontSize: "12px",
            fontWeight: 600,
            gap: "8px",
            marginBottom: "16px",
            padding: "9px 14px",
          }}
        >
          <span aria-hidden="true">!</span>
          {t("handover_builder.alerts_incomplete_banner", {
            count: shiftAlerts.filter((a) => !dispositions[a.alertId]?.disposition).length,
          })}
        </div>
      )}

      {/* ── Section 1: OEE Delta ──────────────────────────────────────────── */}
      <section
        aria-labelledby="sec-oee-heading"
        style={{
          background: "var(--lc-card, #1a1d2a)",
          border: "1px solid var(--lc-border, #272b3d)",
          borderRadius: "8px",
          marginBottom: "16px",
          padding: "16px 18px",
        }}
      >
        <h2
          id="sec-oee-heading"
          style={{
            color: "var(--lc-text-muted, #7b83a3)",
            fontSize: "10px",
            fontWeight: 700,
            letterSpacing: ".1em",
            margin: "0 0 12px",
            textTransform: "uppercase",
          }}
        >
          <span aria-hidden="true">1 · </span>
          {t("handover_builder.section_oee")}
        </h2>

        {/* OEE tiles per line */}
        {oeeSnapshots.length > 0 && (
          <div
            role="list"
            aria-label={t("handover_builder.oee_tiles_label")}
            style={{
              display: "grid",
              gap: "8px",
              gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
              marginBottom: "14px",
            }}
          >
            {oeeSnapshots.map((snap) => {
              const delta = snap.oeePercent - 89.3; // 30-day avg placeholder
              const positive = delta >= 0;
              return (
                <div
                  key={snap.lineId}
                  role="listitem"
                  style={{
                    background: "var(--lc-surface, #13151f)",
                    border: "1px solid var(--lc-border, #272b3d)",
                    borderRadius: "6px",
                    padding: "10px 12px",
                  }}
                >
                  <div
                    style={{
                      color: "var(--lc-text-muted, #7b83a3)",
                      fontSize: "10px",
                      fontWeight: 700,
                      letterSpacing: ".06em",
                      marginBottom: "4px",
                      textTransform: "uppercase",
                    }}
                  >
                    {snap.lineId}
                  </div>
                  <div
                    style={{
                      color: "var(--lc-text, #eef1ff)",
                      fontSize: "20px",
                      fontVariantNumeric: "tabular-nums",
                      fontWeight: 800,
                      letterSpacing: "-.5px",
                    }}
                  >
                    {snap.oeePercent.toFixed(1)}%
                  </div>
                  <div
                    aria-label={
                      positive
                        ? t("handover_builder.oee_delta_above", { value: Math.abs(delta).toFixed(1) })
                        : t("handover_builder.oee_delta_below", { value: Math.abs(delta).toFixed(1) })
                    }
                    style={{
                      color: positive ? "var(--lc-good, #22c55e)" : "var(--lc-crit, #ef4444)",
                      fontSize: "12px",
                      fontWeight: 700,
                      marginTop: "3px",
                    }}
                  >
                    {positive ? "+" : "−"}{Math.abs(delta).toFixed(1)} pp
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Composite OEE delta sent to API */}
        <div style={{ maxWidth: "220px" }}>
          <label
            htmlFor="oee-delta-input"
            style={{
              color: "var(--lc-text-muted, #7b83a3)",
              display: "block",
              fontSize: "12px",
              fontWeight: 600,
              marginBottom: "4px",
            }}
          >
            {t("handover_builder.field_oee_delta")}
            <span aria-hidden="true" style={{ color: "var(--lc-crit, #ef4444)" }}> *</span>
          </label>
          <input
            id="oee-delta-input"
            type="text"
            value={oeeDeltaPct}
            aria-required="true"
            onChange={(e) => setOeeDeltaPct(e.target.value)}
            style={{
              background: "var(--lc-surface, #13151f)",
              border: "1px solid var(--lc-border, #272b3d)",
              borderRadius: "6px",
              color: "var(--lc-text, #eef1ff)",
              fontSize: "13px",
              padding: "7px 10px",
              width: "100%",
            }}
          />
        </div>
      </section>

      {/* ── Section 2: Alert Dispositions ────────────────────────────────── */}
      <section
        aria-labelledby="sec-alerts-heading"
        style={{
          background: "var(--lc-card, #1a1d2a)",
          border: "1px solid var(--lc-border, #272b3d)",
          borderRadius: "8px",
          marginBottom: "16px",
          padding: "16px 18px",
        }}
      >
        <div style={{ alignItems: "center", display: "flex", gap: "8px", justifyContent: "space-between", marginBottom: "12px" }}>
          <h2
            id="sec-alerts-heading"
            style={{
              color: "var(--lc-text-muted, #7b83a3)",
              fontSize: "10px",
              fontWeight: 700,
              letterSpacing: ".1em",
              margin: 0,
              textTransform: "uppercase",
            }}
          >
            <span aria-hidden="true">2 · </span>
            {t("handover_builder.section_alerts")}
          </h2>
          {shiftAlerts.length > 0 && (
            <span
              style={{
                background: allDispositionsComplete ? "rgba(34,197,94,.12)" : "rgba(239,68,68,.12)",
                border: `1px solid ${allDispositionsComplete ? "rgba(34,197,94,.3)" : "rgba(239,68,68,.3)"}`,
                borderRadius: "10px",
                color: allDispositionsComplete ? "var(--lc-good, #22c55e)" : "var(--lc-crit, #ef4444)",
                fontSize: "10px",
                fontWeight: 700,
                padding: "1px 8px",
              }}
            >
              {allDispositionsComplete
                ? t("handover_builder.alerts_all_set")
                : t("handover_builder.alerts_unresolved", { count: shiftAlerts.length })}
            </span>
          )}
        </div>

        {shiftAlerts.length === 0 ? (
          <div
            style={{
              alignItems: "center",
              color: "var(--lc-text-muted, #7b83a3)",
              display: "flex",
              flexDirection: "column",
              fontSize: "12px",
              gap: "6px",
              minHeight: "80px",
              justifyContent: "center",
              textAlign: "center",
            }}
          >
            <span aria-hidden="true" style={{ fontSize: "24px", color: "var(--lc-good, #22c55e)" }}>✓</span>
            <span>{t("handover_builder.alerts_empty")}</span>
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table
              aria-label={t("handover_builder.alerts_table_label")}
              style={{ borderCollapse: "collapse", fontSize: "12px", width: "100%" }}
            >
              <thead>
                <tr style={{ background: "var(--lc-surface, #13151f)" }}>
                  {[
                    t("handover_builder.col_alert_id"),
                    t("handover_builder.col_station"),
                    t("handover_builder.col_failure_type"),
                    t("handover_builder.col_eta"),
                    t("handover_builder.col_disposition"),
                    t("handover_builder.col_note"),
                  ].map((h) => (
                    <th
                      key={h}
                      scope="col"
                      style={{
                        borderBottom: "1px solid var(--lc-border, #272b3d)",
                        color: "var(--lc-text-muted, #7b83a3)",
                        fontSize: "10px",
                        fontWeight: 700,
                        letterSpacing: ".07em",
                        padding: "7px 10px",
                        textAlign: "left",
                        textTransform: "uppercase",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {h}
                      {(h === t("handover_builder.col_disposition") ||
                        h === t("handover_builder.col_note")) && (
                        <span aria-hidden="true" style={{ color: "var(--lc-crit, #ef4444)" }}> *</span>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shiftAlerts.map((alert) => {
                  const entry = dispositions[alert.alertId] ?? { alertId: alert.alertId, disposition: "", note: "" };
                  const missing = entry.disposition === "";
                  const dispSelectId = `disp-${alert.alertId}`;
                  const noteId = `note-${alert.alertId}`;
                  return (
                    <tr
                      key={alert.alertId}
                      style={{ borderBottom: "1px solid var(--lc-border, #272b3d)" }}
                    >
                      <td style={{ ...cellStyle, fontFamily: "monospace", fontSize: "10px" }}>
                        {alert.alertId.slice(0, 8)}…
                      </td>
                      <td style={cellStyle}>{alert.stationId}</td>
                      <td style={{ ...cellStyle, fontWeight: 600 }}>
                        {alert.predictedFailureType}
                      </td>
                      <td style={{ ...cellStyle, fontVariantNumeric: "tabular-nums" }}>
                        {alert.minutesToPredictedFailure !== null
                          ? `${alert.minutesToPredictedFailure} min`
                          : "—"}
                      </td>
                      <td style={{ ...cellStyle, minWidth: "130px" }}>
                        <label htmlFor={dispSelectId} className="sr-only">
                          {t("handover_builder.col_disposition")} {alert.alertId}
                        </label>
                        <DispositionSelect
                          alertId={alert.alertId}
                          value={entry.disposition}
                          onChange={handleDispositionChange}
                          selectId={dispSelectId}
                        />
                        {missing && (
                          <span
                            role="alert"
                            style={{
                              color: "var(--lc-crit, #ef4444)",
                              display: "block",
                              fontSize: "10px",
                              marginTop: "3px",
                            }}
                          >
                            {t("handover_builder.disposition_required")}
                          </span>
                        )}
                      </td>
                      <td style={{ ...cellStyle, minWidth: "200px" }}>
                        <label htmlFor={noteId} className="sr-only">
                          {t("handover_builder.col_note")} {alert.alertId}
                        </label>
                        <input
                          id={noteId}
                          type="text"
                          value={entry.note}
                          maxLength={1000}
                          placeholder={t("handover_builder.note_placeholder")}
                          onChange={(e) => handleNoteChange(alert.alertId, e.target.value)}
                          style={{
                            background: "var(--lc-surface, #13151f)",
                            border: `1px solid ${missing ? "rgba(239,68,68,.5)" : "var(--lc-border, #272b3d)"}`,
                            borderRadius: "4px",
                            color: "var(--lc-text, #eef1ff)",
                            fontSize: "11px",
                            padding: "4px 7px",
                            width: "100%",
                          }}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Section 3: Held VINs ─────────────────────────────────────────── */}
      <section
        aria-labelledby="sec-vins-heading"
        style={{
          background: "var(--lc-card, #1a1d2a)",
          border: "1px solid var(--lc-border, #272b3d)",
          borderRadius: "8px",
          marginBottom: "16px",
          padding: "16px 18px",
        }}
      >
        <h2
          id="sec-vins-heading"
          style={{
            color: "var(--lc-text-muted, #7b83a3)",
            fontSize: "10px",
            fontWeight: 700,
            letterSpacing: ".1em",
            margin: "0 0 12px",
            textTransform: "uppercase",
          }}
        >
          <span aria-hidden="true">3 · </span>
          {t("handover_builder.section_vins")}
        </h2>

        {heldVins.length > 0 && (
          <ul
            role="list"
            aria-label={t("handover_builder.vins_list_label")}
            style={{ display: "flex", flexWrap: "wrap", gap: "7px", listStyle: "none", margin: "0 0 10px", padding: 0 }}
          >
            {heldVins.map((vin) => (
              <li key={vin}>
                <span
                  style={{
                    alignItems: "center",
                    background: "rgba(124,58,237,.12)",
                    border: "1px solid rgba(124,58,237,.3)",
                    borderRadius: "5px",
                    color: "#c4b5fd",
                    display: "inline-flex",
                    fontFamily: "monospace",
                    fontSize: "11px",
                    fontWeight: 700,
                    gap: "6px",
                    padding: "4px 9px",
                  }}
                >
                  {vin}
                  <button
                    type="button"
                    onClick={() => handleRemoveVin(vin)}
                    aria-label={t("handover_builder.vin_remove_label", { vin })}
                    style={{
                      alignItems: "center",
                      background: "rgba(196,181,253,.2)",
                      border: "none",
                      borderRadius: "50%",
                      color: "#c4b5fd",
                      cursor: "pointer",
                      display: "inline-flex",
                      fontSize: "9px",
                      fontWeight: 800,
                      height: "14px",
                      justifyContent: "center",
                      padding: 0,
                      width: "14px",
                    }}
                  >
                    ×
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}

        <div style={{ alignItems: "flex-start", display: "flex", gap: "8px" }}>
          <div style={{ flex: 1, maxWidth: "280px" }}>
            <label
              htmlFor={vinInputId}
              style={{
                color: "var(--lc-text-muted, #7b83a3)",
                display: "block",
                fontSize: "12px",
                fontWeight: 600,
                marginBottom: "4px",
              }}
            >
              {t("handover_builder.vin_input_label")}
            </label>
            <input
              id={vinInputId}
              type="text"
              value={vinInput}
              maxLength={17}
              placeholder={t("handover_builder.vin_input_placeholder")}
              aria-describedby={vinError ? "vin-error" : undefined}
              onChange={(e) => setVinInput(e.target.value)}
              onKeyDown={handleVinKeyDown}
              style={{
                background: "var(--lc-surface, #13151f)",
                border: `1px solid ${vinError ? "rgba(239,68,68,.5)" : "var(--lc-border, #272b3d)"}`,
                borderRadius: "6px",
                color: "var(--lc-text, #eef1ff)",
                fontSize: "13px",
                fontFamily: "monospace",
                padding: "7px 10px",
                textTransform: "uppercase",
                width: "100%",
              }}
            />
            {vinError && (
              <span
                id="vin-error"
                role="alert"
                style={{ color: "var(--lc-crit, #ef4444)", display: "block", fontSize: "10px", marginTop: "3px" }}
              >
                {vinError}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={handleAddVin}
            style={{
              alignSelf: "flex-end",
              background: "transparent",
              border: "1px solid var(--lc-border-light, #32374f)",
              borderRadius: "6px",
              color: "var(--lc-text, #eef1ff)",
              cursor: "pointer",
              fontSize: "12px",
              fontWeight: 600,
              padding: "7px 14px",
            }}
          >
            {t("handover_builder.vin_add")}
          </button>
        </div>
      </section>

      {/* ── Section 4: Notes ─────────────────────────────────────────────── */}
      <section
        aria-labelledby="sec-notes-heading"
        style={{
          background: "var(--lc-card, #1a1d2a)",
          border: "1px solid var(--lc-border, #272b3d)",
          borderRadius: "8px",
          marginBottom: "16px",
          padding: "16px 18px",
        }}
      >
        <h2
          id="sec-notes-heading"
          style={{
            color: "var(--lc-text-muted, #7b83a3)",
            fontSize: "10px",
            fontWeight: 700,
            letterSpacing: ".1em",
            margin: "0 0 12px",
            textTransform: "uppercase",
          }}
        >
          <span aria-hidden="true">4 · </span>
          {t("handover_builder.section_notes")}
        </h2>
        <label
          htmlFor={notesId}
          style={{
            color: "var(--lc-text-muted, #7b83a3)",
            display: "block",
            fontSize: "12px",
            fontWeight: 600,
            marginBottom: "4px",
          }}
        >
          {t("handover_builder.notes_label")}
        </label>
        <textarea
          id={notesId}
          value={notes}
          maxLength={4000}
          rows={4}
          onChange={(e) => setNotes(e.target.value)}
          placeholder={t("handover_builder.notes_placeholder")}
          style={{
            background: "var(--lc-surface, #13151f)",
            border: "1px solid var(--lc-border, #272b3d)",
            borderRadius: "6px",
            color: "var(--lc-text, #eef1ff)",
            fontFamily: "inherit",
            fontSize: "13px",
            minHeight: "80px",
            padding: "8px 11px",
            resize: "vertical",
            width: "100%",
          }}
        />
        <div style={{ color: "var(--lc-text-muted, #7b83a3)", fontSize: "10px", marginTop: "3px", textAlign: "right" }}>
          {notes.length}/4000
        </div>
      </section>

      {/* ── Footer ───────────────────────────────────────────────────────── */}
      <footer
        style={{
          alignItems: "center",
          background: "var(--lc-surface, #13151f)",
          borderTop: "1px solid var(--lc-border, #272b3d)",
          bottom: 0,
          display: "flex",
          gap: "12px",
          justifyContent: "space-between",
          left: 0,
          padding: "14px 24px",
          position: "sticky",
          right: 0,
        }}
      >
        <div>
          {!allDispositionsComplete && shiftAlerts.length > 0 && (
            <p style={{ color: "var(--lc-crit, #ef4444)", fontSize: "12px", fontWeight: 600, margin: 0 }}>
              {t("handover_builder.submit_blocked_message")}
            </p>
          )}
          <p style={{ color: "var(--lc-text-muted, #7b83a3)", fontSize: "11px", margin: 0 }}>
            {t("handover_builder.required_fields_note")}
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button
            type="button"
            disabled={phase === "submitting"}
            style={{
              background: "transparent",
              border: "1px solid var(--lc-border-light, #32374f)",
              borderRadius: "6px",
              color: "var(--lc-text, #eef1ff)",
              cursor: "pointer",
              fontSize: "13px",
              fontWeight: 700,
              padding: "8px 18px",
            }}
          >
            {t("handover_builder.save_draft")}
          </button>
          <button
            type="button"
            disabled={!canSubmit || phase === "submitting"}
            aria-disabled={!canSubmit || phase === "submitting"}
            onClick={() => void handleSubmit()}
            style={{
              background: canSubmit && phase !== "submitting" ? "var(--lc-bmw-blue, #0066b1)" : "rgba(0,102,177,.35)",
              border: "none",
              borderRadius: "6px",
              color: "#fff",
              cursor: canSubmit && phase !== "submitting" ? "pointer" : "not-allowed",
              fontSize: "13px",
              fontWeight: 700,
              opacity: canSubmit && phase !== "submitting" ? 1 : 0.5,
              padding: "8px 18px",
            }}
          >
            {phase === "submitting" ? t("handover_builder.submitting") : t("handover_builder.submit")}
          </button>
        </div>
      </footer>
    </div>
  );
}
