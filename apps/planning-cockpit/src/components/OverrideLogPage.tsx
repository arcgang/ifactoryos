import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { listOverrides, exportOverridesPdf } from "../services/api";
import type { OverrideRecord } from "../types/overrides";

function getCurrentWeekIso(): string {
  const now = new Date();
  const year = now.getUTCFullYear();
  // ISO week: Jan 4 is always in week 1
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const startOfWeek1 = new Date(jan4);
  startOfWeek1.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7));
  const diff = now.getTime() - startOfWeek1.getTime();
  const weekNum = Math.floor(diff / (7 * 24 * 60 * 60 * 1000)) + 1;
  return `${year}-W${String(weekNum).padStart(2, "0")}`;
}

function navigateWeek(weekIso: string, delta: number): string {
  const match = /^(\d{4})-W(\d{2})$/.exec(weekIso);
  if (!match) return weekIso;
  const year = parseInt(match[1], 10);
  const week = parseInt(match[2], 10);

  // Compute the Monday of the given ISO week
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const startOfWeek1 = new Date(jan4);
  startOfWeek1.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7));
  const targetMs =
    startOfWeek1.getTime() + (week - 1 + delta) * 7 * 24 * 60 * 60 * 1000;
  const target = new Date(targetMs);

  const targetYear = target.getUTCFullYear();
  const jan4t = new Date(Date.UTC(targetYear, 0, 4));
  const startOfWeek1t = new Date(jan4t);
  startOfWeek1t.setUTCDate(jan4t.getUTCDate() - ((jan4t.getUTCDay() + 6) % 7));
  const diffT = target.getTime() - startOfWeek1t.getTime();
  const weekNumT = Math.floor(diffT / (7 * 24 * 60 * 60 * 1000)) + 1;
  return `${targetYear}-W${String(weekNumT).padStart(2, "0")}`;
}

type LoadState = "idle" | "loading" | "error";

export function OverrideLogPage() {
  const { t } = useTranslation();
  const [weekIso, setWeekIso] = useState<string>(getCurrentWeekIso);
  const [records, setRecords] = useState<OverrideRecord[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [exportState, setExportState] = useState<"idle" | "loading" | "error">("idle");
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const loadPage = useCallback(
    async (week: string, cursor?: string) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      if (!cursor) {
        setLoadState("loading");
        setRecords([]);
        setNextCursor(null);
      } else {
        setLoadingMore(true);
      }

      try {
        const resp = await listOverrides({ weekIso: week, cursor, limit: 50 });
        if (controller.signal.aborted) return;
        if (!cursor) {
          setRecords(resp.data);
        } else {
          setRecords((prev: OverrideRecord[]) => [...prev, ...resp.data]);
        }
        setNextCursor(resp.page.next_cursor);
        setLoadState("idle");
      } catch (err) {
        if (controller.signal.aborted) return;
        setErrorMsg(err instanceof Error ? err.message : t("errors.requestFailed"));
        setLoadState("error");
      } finally {
        setLoadingMore(false);
      }
    },
    [t],
  );

  useEffect(() => {
    void loadPage(weekIso);
    return () => {
      abortRef.current?.abort();
    };
  }, [weekIso, loadPage]);

  // Intersection observer for infinite scroll
  useEffect(() => {
    if (!sentinelRef.current || !nextCursor) return;
    const el = sentinelRef.current;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && nextCursor && !loadingMore) {
          void loadPage(weekIso, nextCursor);
        }
      },
      { threshold: 0.1 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [nextCursor, loadingMore, weekIso, loadPage]);

  const handleExport = useCallback(async () => {
    setExportState("loading");
    try {
      const blob = await exportOverridesPdf(weekIso);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `VIN-Override-Log_${weekIso}.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setExportState("idle");
    } catch {
      setExportState("error");
    }
  }, [weekIso]);

  const handlePrevWeek = useCallback(() => {
    setWeekIso((w: string) => navigateWeek(w, -1));
  }, []);

  const handleNextWeek = useCallback(() => {
    setWeekIso((w: string) => navigateWeek(w, 1));
  }, []);

  const hasOverrides = records.length > 0;
  const canExport = hasOverrides && loadState !== "loading";

  return (
    <main className="main-content" id="override-log" data-testid="override-log-page">
      <div className="page-header">
        <div className="page-header-left">
          <h2>{t("overrideLog.title")}</h2>
          <p className="subtitle">{t("overrideLog.subtitle")}</p>
        </div>
      </div>

      <section className="seq-card" aria-label={t("overrideLog.sectionLabel")}>
        {/* Toolbar */}
        <div className="override-log-toolbar">
          <div className="override-log-toolbar-left">
            <button
              type="button"
              className="btn btn-secondary override-week-nav"
              onClick={handlePrevWeek}
              aria-label={t("overrideLog.prevWeek")}
            >
              ←
            </button>
            <span className="override-week-label" aria-label={t("overrideLog.currentWeekLabel", { week: weekIso })}>
              {weekIso}
            </span>
            <button
              type="button"
              className="btn btn-secondary override-week-nav"
              onClick={handleNextWeek}
              aria-label={t("overrideLog.nextWeek")}
            >
              →
            </button>
            <span
              className="override-count-badge"
              role="status"
              aria-live="polite"
              aria-label={t("overrideLog.countLabel", { count: records.length })}
            >
              {t("overrideLog.countBadge", { count: records.length })}
            </span>
          </div>
          <div className="override-log-toolbar-right">
            {!canExport && (
              <span className="override-export-note">{t("overrideLog.exportNote")}</span>
            )}
            <button
              type="button"
              className="btn btn-secondary override-export-btn"
              onClick={() => void handleExport()}
              disabled={!canExport || exportState === "loading"}
              aria-disabled={!canExport || exportState === "loading"}
              aria-label={t("overrideLog.exportButton")}
              data-testid="export-pdf-btn"
            >
              <span className="override-pdf-icon" aria-hidden="true">PDF</span>
              {exportState === "loading" ? t("overrideLog.exporting") : t("overrideLog.exportButton")}
            </button>
          </div>
        </div>

        {exportState === "error" && (
          <div className="seq-error-wrap" role="alert" style={{ margin: "0.75rem 1rem" }}>
            <p className="seq-error-sub">{t("overrideLog.exportFailed")}</p>
          </div>
        )}

        {/* Loading */}
        {loadState === "loading" && (
          <div className="seq-empty-state" role="status" aria-live="polite" data-testid="override-log-loading">
            <p>{t("overrideLog.loading")}</p>
          </div>
        )}

        {/* Error */}
        {loadState === "error" && (
          <div className="seq-error-wrap" role="alert" data-testid="override-log-error">
            <div className="seq-error-icon" aria-hidden="true">!</div>
            <h3 className="seq-error-title">{t("errors.loadFailed")}</h3>
            <p className="seq-error-sub">{errorMsg}</p>
            <div className="seq-error-actions">
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void loadPage(weekIso)}
              >
                {t("errors.retry")}
              </button>
            </div>
          </div>
        )}

        {/* Empty state */}
        {loadState === "idle" && records.length === 0 && (
          <div className="seq-empty-state" role="status" aria-live="polite" data-testid="override-log-empty">
            <div className="seq-empty-icon" aria-hidden="true">📋</div>
            <h3 className="seq-empty-title">{t("overrideLog.emptyTitle")}</h3>
            <p className="seq-empty-desc">{t("overrideLog.emptyDesc", { week: weekIso })}</p>
          </div>
        )}

        {/* Table */}
        {loadState === "idle" && records.length > 0 && (
          <div className="seq-table-wrap" data-testid="override-log-table">
            <table className="seq-table override-log-table" aria-label={t("overrideLog.tableLabel", { week: weekIso })}>
              <thead>
                <tr>
                  <th scope="col">{t("overrideLog.columns.overrideId")}</th>
                  <th scope="col">{t("overrideLog.columns.vin")}</th>
                  <th scope="col">{t("overrideLog.columns.positionChange")}</th>
                  <th scope="col">{t("overrideLog.columns.justification")}</th>
                  <th scope="col">{t("overrideLog.columns.plannerId")}</th>
                  <th scope="col">{t("overrideLog.columns.createdAt")}</th>
                </tr>
              </thead>
              <tbody>
                {records.map((rec) => (
                  <OverrideRow key={rec.overrideId} record={rec} />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Infinite scroll sentinel */}
        {nextCursor && (
          <div ref={sentinelRef} className="override-scroll-sentinel" aria-hidden="true" data-testid="override-scroll-sentinel" />
        )}
        {loadingMore && (
          <div className="seq-empty-state" role="status" aria-live="polite">
            <p>{t("overrideLog.loadingMore")}</p>
          </div>
        )}
      </section>
    </main>
  );
}

function OverrideRow({ record }: { record: OverrideRecord }) {
  const { t } = useTranslation();
  const moved = record.newPosition < record.originalPosition ? "up" : "down";

  return (
    <tr>
      <td>
        <span className="vin-mono override-id-cell">{record.overrideId}</span>
      </td>
      <td>
        <span className="vin-mono">{record.vin}</span>
      </td>
      <td>
        <span
          className="override-pos-change"
          aria-label={t("overrideLog.positionChangeLabel", {
            orig: record.originalPosition,
            next: record.newPosition,
          })}
        >
          <span className="override-pos-orig">{record.originalPosition}</span>
          <span className="override-pos-arrow" aria-hidden="true">→</span>
          <span className={`override-pos-new override-pos-new--${moved}`}>{record.newPosition}</span>
        </span>
      </td>
      <td className="override-justification-cell">
        <span className="override-justification-text">{record.justification}</span>
      </td>
      <td>
        <span className="override-planner">{record.plannerId}</span>
      </td>
      <td>
        <time className="override-ts" dateTime={record.createdAt}>
          {record.createdAt}
        </time>
      </td>
    </tr>
  );
}
