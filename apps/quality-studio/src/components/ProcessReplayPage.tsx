"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { ApiError, getVinProcessReplay, getVinThreadSnapshot } from "../services/api";
import type {
  ProcessReplayResponse,
  ThreadSnapshotResponse,
} from "../types/processReplay";
import { useAuth } from "../hooks/useAuth";
import { emitProcessReplayLaunch } from "../instrumentation/adoptionEvents";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const SCRUB_STEP_MS = 5_000; // 5-second keyboard step
const DEBOUNCE_MS = 200;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function parseTs(ts: string): number {
  return new Date(ts).getTime();
}

function formatTs(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function clamp(val: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, val));
}

function positionPercent(ts: number, startMs: number, endMs: number): number {
  if (endMs === startMs) return 0;
  return clamp(((ts - startMs) / (endMs - startMs)) * 100, 0, 100);
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function CompletenessBar({ pct }: { pct: number }) {
  const { t } = useTranslation();
  const warn = pct < 95;
  return (
    <div
      style={{
        padding: "6px 20px",
        background: "var(--surface)",
        borderBottom: "1px solid var(--border)",
        display: "flex",
        alignItems: "center",
        gap: "10px",
      }}
    >
      <span
        style={{
          fontSize: "11px",
          color: "var(--grey-dk)",
          fontWeight: 600,
          whiteSpace: "nowrap",
        }}
      >
        {t("processReplay.completeness")}
      </span>
      <div
        style={{ flex: 1, maxWidth: "300px" }}
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${Math.round(pct)}% ${t("processReplay.completeness")}`}
      >
        <div
          style={{
            height: "8px",
            background: "var(--border)",
            borderRadius: "4px",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${pct}%`,
              background: warn ? "#f59e0b" : "var(--green)",
              borderRadius: "4px",
              transition: "width 0.3s ease",
            }}
          />
        </div>
      </div>
      <span
        style={{
          fontSize: "12px",
          fontWeight: 700,
          color: warn ? "#d97706" : "var(--green)",
        }}
        data-testid="completeness-pct"
      >
        {Math.round(pct)}%
      </span>
      {warn && (
        <span
          style={{ fontSize: "11px", color: "var(--grey)" }}
          role="note"
          aria-label={t("processReplay.completenessWarning")}
        >
          {t("processReplay.completenessWarning")}
        </span>
      )}
    </div>
  );
}

interface GapProps {
  leftPct: number;
  widthPct: number;
  label: string;
}

function GapSegment({ leftPct, widthPct, label }: GapProps) {
  const { t } = useTranslation();
  return (
    <div
      role="img"
      aria-label={t("processReplay.dataUnavailable")}
      data-testid="data-gap"
      style={{
        position: "absolute",
        top: 0,
        bottom: 0,
        left: `${leftPct}%`,
        width: `${widthPct}%`,
        background:
          "repeating-linear-gradient(45deg,#9ca3af,#9ca3af 4px,#d1d5db 4px,#d1d5db 8px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minWidth: "2px",
      }}
    >
      {widthPct > 8 && (
        <span
          style={{
            fontSize: "10px",
            color: "#374151",
            fontWeight: 600,
            background: "rgba(255,255,255,0.9)",
            padding: "2px 6px",
            borderRadius: "3px",
            border: "1px solid #9ca3af",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            maxWidth: "95%",
          }}
        >
          {label}
        </span>
      )}
    </div>
  );
}

interface TrackProps {
  label: string;
  sublabel?: string;
  available: boolean;
  startMs: number;
  endMs: number;
  timestamps: number[];
  dataUnavailableLabel: string;
}

function TimelineTrack({
  label,
  sublabel,
  available,
  startMs,
  endMs,
  timestamps,
  dataUnavailableLabel,
}: TrackProps) {
  const barColor = available ? "#93c5fd" : "transparent";
  const range = endMs - startMs || 1;

  return (
    <div
      className="timeline-row"
      role="row"
      aria-label={`${label}${sublabel ? ` — ${sublabel}` : ""}`}
    >
      <div className="trow-label" role="rowheader">
        <strong>{label}</strong>
        {sublabel && <span>{sublabel}</span>}
      </div>
      <div className="trow-chart" role="cell" aria-label={available ? label : dataUnavailableLabel}>
        {!available ? (
          <GapSegment leftPct={0} widthPct={100} label={dataUnavailableLabel} />
        ) : (
          <div
            style={{
              position: "absolute",
              top: 4,
              bottom: 4,
              left: 0,
              right: 0,
              display: "flex",
              alignItems: "flex-end",
              gap: "1px",
              padding: "0 2px",
            }}
            aria-hidden="true"
          >
            {timestamps.map((ts, i) => {
              const leftPct = positionPercent(ts, startMs, endMs);
              const nextTs = i < timestamps.length - 1 ? timestamps[i + 1] : endMs;
              const widthPct = ((nextTs - ts) / range) * 100;
              return (
                <div
                  key={`${ts}-${i}`}
                  style={{
                    position: "absolute",
                    left: `${leftPct}%`,
                    width: `${Math.max(widthPct, 0.5)}%`,
                    top: 0,
                    bottom: 0,
                    background: barColor,
                    borderRadius: "1px 1px 0 0",
                  }}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

interface DataPanelProps {
  snapshot: ThreadSnapshotResponse | null;
  loading: boolean;
  scrubTs: number | null;
}

function DataPanel({ snapshot, loading, scrubTs }: DataPanelProps) {
  const { t } = useTranslation();

  if (scrubTs === null) {
    return (
      <div className="replay-panel" role="complementary" aria-label={t("processReplay.dataPanel")}>
        <div className="panel-header">
          <p style={{ fontSize: "12px", color: "var(--grey)" }}>
            {t("processReplay.scrubHint")}
          </p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div
        className="replay-panel"
        role="complementary"
        aria-label={t("processReplay.dataPanel")}
        aria-busy="true"
      >
        <div className="panel-header">
          <div
            style={{
              width: "40px",
              height: "40px",
              border: "3px solid var(--border)",
              borderTopColor: "var(--blue)",
              borderRadius: "50%",
              animation: "spin 0.8s linear infinite",
              margin: "20px auto",
            }}
            aria-hidden="true"
          />
        </div>
      </div>
    );
  }

  if (!snapshot) {
    return (
      <div className="replay-panel" role="complementary" aria-label={t("processReplay.dataPanel")}>
        <div className="panel-header">
          <p style={{ fontSize: "12px", color: "var(--grey)" }}>
            {t("processReplay.dataUnavailable")}
          </p>
        </div>
      </div>
    );
  }

  const entries = Object.entries(snapshot.parameters);

  return (
    <div className="replay-panel" role="complementary" aria-label={t("processReplay.dataPanel")}>
      <div className="panel-header">
        <div className="panel-ts" data-testid="panel-ts">
          {formatTs(snapshot.atTs)}
        </div>
        <div
          className="panel-stage"
          data-testid="panel-stage-name"
          aria-label={`${t("processReplay.stage")} ${snapshot.stageName ?? "—"}`}
        >
          {snapshot.stageName ?? "—"}
        </div>
        <div
          className="panel-vin"
          aria-label={`VIN ${snapshot.vin}`}
          style={{ fontSize: "11px", color: "var(--grey-dk)", marginTop: "2px" }}
        >
          {snapshot.vin}
        </div>
        {snapshot.stageNumber != null && (
          <div
            style={{ fontSize: "11px", color: "var(--grey)", marginTop: "2px" }}
            data-testid="panel-stage-number"
          >
            {t("processReplay.stage")} {snapshot.stageNumber}
          </div>
        )}
        {snapshot.tunnelPositionMm != null && (
          <div
            className="panel-position"
            data-testid="panel-tunnel-position"
            aria-label={`${t("processReplay.tunnelPosition")}: ${snapshot.tunnelPositionMm} mm`}
          >
            <span style={{ fontSize: "11px", color: "var(--blue-dk)" }}>
              {t("processReplay.tunnelPosition")}
            </span>
            <strong style={{ display: "block", fontSize: "16px", fontWeight: 700 }}>
              {snapshot.tunnelPositionMm.toLocaleString()} mm
            </strong>
          </div>
        )}
      </div>

      {!snapshot.dataAvailable ? (
        <div
          style={{
            padding: "14px",
            background: "var(--grey-lt)",
            borderRadius: "var(--radius)",
            margin: "10px 14px",
            fontSize: "12px",
            color: "var(--grey-dk)",
            textAlign: "center",
          }}
          role="alert"
          data-testid="panel-data-unavailable"
        >
          {t("processReplay.dataUnavailable")}
        </div>
      ) : (
        <div className="param-section">
          <div className="param-section-title">{t("processReplay.parameters")}</div>
          {entries.length === 0 ? (
            <p style={{ fontSize: "12px", color: "var(--grey)" }}>{t("processReplay.empty")}</p>
          ) : (
            entries.map(([key, val]) => (
              <div
                className="param-row"
                key={key}
                data-testid={`param-row-${key}`}
              >
                <span className="param-name">{key}</span>
                <span
                  className="param-val"
                  aria-label={`${key}: ${String(val ?? "—")}`}
                >
                  {val == null ? "—" : String(val)}
                </span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface Props {
  vin: string;
  onBack: () => void;
}

type ReplayState =
  | { type: "loading" }
  | { type: "error"; message: string; status?: number }
  | { type: "loaded"; data: ProcessReplayResponse };

export function ProcessReplayPage({ vin, onBack }: Props) {
  const { t } = useTranslation();
  const { userId } = useAuth();
  const [replayState, setReplayState] = useState<ReplayState>({ type: "loading" });
  const [scrubPercent, setScrubPercent] = useState<number | null>(null);
  const [snapshot, setSnapshot] = useState<ThreadSnapshotResponse | null>(null);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const timelineRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Emit PROCESS_REPLAY_LAUNCH adoption event on mount
  useEffect(() => {
    void emitProcessReplayLaunch(vin);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vin]); // Emit once per vin, not on auth refresh

  // Fetch replay on mount
  useEffect(() => {
    let cancelled = false;
    setReplayState({ type: "loading" });

    getVinProcessReplay(vin)
      .then((data) => {
        if (!cancelled) setReplayState({ type: "loaded", data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof ApiError
            ? err.message
            : t("processReplay.error");
        const status = err instanceof ApiError ? err.status : undefined;
        setReplayState({ type: "error", message, status });
      });

    return () => {
      cancelled = true;
    };
  }, [vin, t]);

  const { startMs, endMs } = useMemo(() => {
    if (replayState.type !== "loaded") return { startMs: 0, endMs: 0 };
    const { replayWindowStart, replayWindowEnd } = replayState.data;
    return {
      startMs: replayWindowStart ? parseTs(replayWindowStart) : 0,
      endMs: replayWindowEnd ? parseTs(replayWindowEnd) : 0,
    };
  }, [replayState]);

  const scrubTs: number | null = useMemo(() => {
    if (scrubPercent === null || startMs === endMs) return null;
    return startMs + (scrubPercent / 100) * (endMs - startMs);
  }, [scrubPercent, startMs, endMs]);

  const fetchSnapshot = useCallback(
    (ts: number) => {
      if (abortRef.current) abortRef.current.abort();
      abortRef.current = new AbortController();
      setSnapshotLoading(true);

      getVinThreadSnapshot(vin, new Date(ts).toISOString())
        .then((data) => {
          setSnapshot(data);
          setSnapshotLoading(false);
        })
        .catch((err: unknown) => {
          if (err instanceof Error && err.name === "AbortError") return;
          setSnapshot(null);
          setSnapshotLoading(false);
        });
    },
    [vin],
  );

  // Debounce snapshot fetch while scrubbing
  useEffect(() => {
    if (scrubTs === null) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => fetchSnapshot(scrubTs), DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [scrubTs, fetchSnapshot]);

  // Cleanup abort controller on unmount
  useEffect(() => {
    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, []);

  function handleTimelinePointer(e: React.PointerEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const pct = clamp(((e.clientX - rect.left) / rect.width) * 100, 0, 100);
    setScrubPercent(pct);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (replayState.type !== "loaded") return;
    const range = endMs - startMs;
    if (range === 0) return;

    const stepPct = (SCRUB_STEP_MS / range) * 100;
    const current = scrubPercent ?? 0;

    if (e.key === "ArrowRight") {
      e.preventDefault();
      setScrubPercent(clamp(current + stepPct, 0, 100));
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      setScrubPercent(clamp(current - stepPct, 0, 100));
    } else if (e.key === "Home") {
      e.preventDefault();
      setScrubPercent(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setScrubPercent(100);
    }
  }

  // -------------------------------------------------------------------------
  // Render: loading
  // -------------------------------------------------------------------------
  if (replayState.type === "loading") {
    return (
      <section aria-labelledby="replay-heading" aria-busy="true">
        <div className="page-header">
          <div style={{ flex: 1 }}>
            <div className="breadcrumb">
              Quality Studio / {t("nav.defectDashboard")} / <span>{t("processReplay.title")}</span>
            </div>
            <h1 id="replay-heading">{t("processReplay.title")}</h1>
          </div>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onBack}>
            ← {t("processReplay.backToDashboard")}
          </button>
        </div>
        <div
          style={{ padding: "40px", textAlign: "center" }}
          role="status"
          aria-live="polite"
          aria-label={t("processReplay.loading")}
          data-testid="replay-loading"
        >
          <div
            style={{
              width: "40px",
              height: "40px",
              border: "3px solid var(--border)",
              borderTopColor: "var(--blue)",
              borderRadius: "50%",
              animation: "spin 0.8s linear infinite",
              margin: "0 auto 16px",
            }}
            aria-hidden="true"
          />
          <p style={{ fontSize: "15px", fontWeight: 600, marginBottom: "4px" }}>
            {t("processReplay.loading")}
          </p>
          <p style={{ fontSize: "13px", color: "var(--grey)" }}>
            {t("processReplay.loadingSubtext", { vin })}
          </p>
        </div>
      </section>
    );
  }

  // -------------------------------------------------------------------------
  // Render: error
  // -------------------------------------------------------------------------
  if (replayState.type === "error") {
    const { message, status } = replayState;
    const isNotFound = status === 404;

    return (
      <section aria-labelledby="replay-heading">
        <div className="page-header">
          <div style={{ flex: 1 }}>
            <div className="breadcrumb">
              Quality Studio / {t("nav.defectDashboard")} / <span>{t("processReplay.title")}</span>
            </div>
            <h1 id="replay-heading">{t("processReplay.title")}</h1>
          </div>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onBack}>
            ← {t("processReplay.backToDashboard")}
          </button>
        </div>
        <div
          role="alert"
          aria-live="assertive"
          style={{
            padding: "32px",
            textAlign: "center",
            maxWidth: "440px",
            margin: "0 auto",
          }}
          data-testid={isNotFound ? "replay-not-found" : "replay-error"}
        >
          <p style={{ fontSize: "36px", marginBottom: "12px" }} aria-hidden="true">
            {isNotFound ? "🔍" : "⚠"}
          </p>
          <h2
            style={{
              fontSize: "16px",
              fontWeight: 700,
              color: "var(--red)",
              marginBottom: "8px",
            }}
          >
            {isNotFound ? t("processReplay.notFound") : t("processReplay.errorTitle")}
          </h2>
          <p
            style={{
              fontSize: "13px",
              color: "var(--ink2)",
              marginBottom: "16px",
              lineHeight: 1.6,
            }}
          >
            {isNotFound ? t("processReplay.notFoundMessage", { vin }) : message}
          </p>
          {!isNotFound && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setReplayState({ type: "loading" })}
              data-testid="replay-retry"
            >
              {t("processReplay.retry")}
            </button>
          )}
        </div>
      </section>
    );
  }

  // -------------------------------------------------------------------------
  // Render: loaded
  // -------------------------------------------------------------------------
  const { data } = replayState;
  const { prepStages, boothParameters, climateData, overallCompletenessPercent } = data;
  const replayStart = data.replayWindowStart
    ? new Date(data.replayWindowStart).toLocaleString("en-GB")
    : "—";
  const replayEnd = data.replayWindowEnd
    ? new Date(data.replayWindowEnd).toLocaleString("en-GB")
    : "—";

  return (
    <section aria-labelledby="replay-heading">
      <div className="page-header">
        <div style={{ flex: 1 }}>
          <div className="breadcrumb">
            Quality Studio / {t("nav.defectDashboard")} / <span>{t("processReplay.title")}</span>
          </div>
          <h1 id="replay-heading" style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            {t("processReplay.title")}
            <span
              style={{ fontFamily: "monospace", fontSize: "15px", fontWeight: 400, color: "var(--ink2)" }}
              aria-label={`VIN ${vin}`}
            >
              {vin}
            </span>
          </h1>
          <p style={{ marginTop: "4px", fontSize: "12px", color: "var(--grey)" }}>
            {t("processReplay.replayWindow")}: {replayStart} → {replayEnd}
          </p>
        </div>
        <button type="button" className="btn btn-secondary btn-sm" onClick={onBack}>
          ← {t("processReplay.backToDashboard")}
        </button>
      </div>

      <CompletenessBar pct={overallCompletenessPercent} />

      <div
        style={{ display: "grid", gridTemplateColumns: "1fr 300px", minHeight: "540px" }}
        data-testid="replay-shell"
      >
        {/* Timeline */}
        <div style={{ overflow: "auto", borderRight: "1px solid var(--border)" }}>
          {/* Time ruler */}
          <div
            ref={timelineRef}
            role="slider"
            aria-label={t("processReplay.scrubberLabel")}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(scrubPercent ?? 0)}
            tabIndex={0}
            style={{ position: "relative", cursor: "crosshair", userSelect: "none" }}
            onPointerDown={handleTimelinePointer}
            onPointerMove={(e) => {
              if (e.buttons > 0) handleTimelinePointer(e);
            }}
            onKeyDown={handleKeyDown}
          >
            {/* Ruler */}
            <div
              style={{
                display: "flex",
                borderBottom: "1px solid var(--border)",
                background: "#f8fafc",
                height: "28px",
              }}
              aria-hidden="true"
            >
              {[0, 25, 50, 75, 100].map((pct) => {
                const ts = startMs + (pct / 100) * (endMs - startMs);
                return (
                  <div
                    key={pct}
                    style={{
                      flex: 1,
                      borderLeft: pct > 0 ? "1px solid var(--border)" : undefined,
                      fontSize: "10px",
                      color: "var(--grey)",
                      padding: "6px 4px",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                    }}
                  >
                    {ts > 0 ? formatTs(new Date(ts).toISOString()) : ""}
                  </div>
                );
              })}
            </div>

            {/* Scrub line */}
            {scrubPercent !== null && (
              <div
                style={{
                  position: "absolute",
                  top: "28px",
                  bottom: 0,
                  left: `${scrubPercent}%`,
                  width: "2px",
                  background: "var(--blue)",
                  zIndex: 10,
                  pointerEvents: "none",
                }}
                aria-hidden="true"
              >
                <div
                  style={{
                    position: "absolute",
                    top: "-4px",
                    left: "-6px",
                    width: "14px",
                    height: "14px",
                    background: "var(--blue)",
                    borderRadius: "50%",
                    border: "2px solid #fff",
                    boxShadow: "0 0 0 2px var(--blue)",
                  }}
                />
              </div>
            )}

            {/* Prep stage tracks */}
            <div role="rowgroup" aria-label={t("processReplay.prepStagesGroup")}>
              <div
                style={{
                  padding: "6px 12px",
                  background: "#f0f4fa",
                  fontSize: "11px",
                  fontWeight: 700,
                  color: "var(--ink2)",
                  textTransform: "uppercase",
                  letterSpacing: ".06em",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                {t("processReplay.prepStageGroupLabel")}
              </div>
              {prepStages.map((stage) => {
                const stageTs = stage.samples.map((s) => parseTs(s.ts));
                return (
                  <TimelineTrack
                    key={stage.stageNumber}
                    label={stage.stageName}
                    sublabel={`${t("processReplay.stage")} ${stage.stageNumber}`}
                    available={stage.available}
                    startMs={startMs}
                    endMs={endMs}
                    timestamps={stageTs}
                    dataUnavailableLabel={t("processReplay.dataUnavailable")}
                  />
                );
              })}
            </div>

            {/* Booth tracks */}
            {boothParameters.length > 0 && (
              <div role="rowgroup" aria-label={t("processReplay.boothGroup")}>
                <div
                  style={{
                    padding: "6px 12px",
                    background: "#f0f4fa",
                    fontSize: "11px",
                    fontWeight: 700,
                    color: "var(--ink2)",
                    textTransform: "uppercase",
                    letterSpacing: ".06em",
                    borderBottom: "1px solid var(--border)",
                  }}
                >
                  {t("processReplay.boothGroupLabel")}
                </div>
                {boothParameters.map((booth) => {
                  const boothTs = booth.samples.map((s) => parseTs(s.ts));
                  return (
                    <TimelineTrack
                      key={booth.boothId}
                      label={booth.boothId}
                      available={booth.available}
                      startMs={startMs}
                      endMs={endMs}
                      timestamps={boothTs}
                      dataUnavailableLabel={t("processReplay.dataUnavailable")}
                    />
                  );
                })}
              </div>
            )}

            {/* Climate track */}
            <div role="rowgroup" aria-label={t("processReplay.climateGroup")}>
              <div
                style={{
                  padding: "6px 12px",
                  background: "#f0f4fa",
                  fontSize: "11px",
                  fontWeight: 700,
                  color: "var(--ink2)",
                  textTransform: "uppercase",
                  letterSpacing: ".06em",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                {t("processReplay.climateGroupLabel")}
              </div>
              <TimelineTrack
                label={t("processReplay.climateLabel")}
                available={climateData.available}
                startMs={startMs}
                endMs={endMs}
                timestamps={climateData.samples.map((s) => parseTs(s.ts))}
                dataUnavailableLabel={t("processReplay.dataUnavailable")}
              />
            </div>
          </div>
        </div>

        {/* Data panel */}
        <DataPanel snapshot={snapshot} loading={snapshotLoading} scrubTs={scrubTs} />
      </div>
    </section>
  );
}
