import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ConstraintThreshold, ConstraintType } from "../types/constraints";
import type { ConstraintAlertRecord } from "../types/alerts";
import { listConstraintThresholds, getConstraintStreamUrl, listActiveAlerts } from "../services/api";
import { useConstraintStream } from "../hooks/useConstraintStream";
import { ConstraintTile } from "./ConstraintTile";
import { ThresholdConfigPanel } from "./ThresholdConfigPanel";
import { ConstraintAlertPanel } from "./ConstraintAlertPanel";

const ALERT_POLL_INTERVAL_MS = 30_000;

const TILE_CONSTRAINT_TYPES: ConstraintType[] = [
  "paint_capacity",
  "body_wip_queue",
  "kitting_readiness",
  "maintenance_window",
];

const TILE_LABELS: Record<ConstraintType, { label: string; sublabel: string }> = {
  paint_capacity: {
    label: "Paint Capacity",
    sublabel: "constraintType: paint_capacity",
  },
  body_wip_queue: {
    label: "Body WIP Queue",
    sublabel: "constraintType: body_wip_queue",
  },
  kitting_readiness: {
    label: "Kitting Readiness",
    sublabel: "constraintType: kitting_readiness",
  },
  maintenance_window: {
    label: "Maintenance Windows",
    sublabel: "constraintType: maintenance_window",
  },
  agv_delay: { label: "AGV Delay", sublabel: "constraintType: agv_delay" },
  press_die_overrun: {
    label: "Press Die Overrun",
    sublabel: "constraintType: press_die_overrun",
  },
};

function emitAdoptionEvent(userId: string, sessionId: string): void {
  const event = {
    action: "constraint_radar_viewed",
    userId,
    sessionId,
    timestamp: new Date().toISOString(),
  };
  window.dispatchEvent(new CustomEvent("ifos:adoption", { detail: event }));
}

export function ConstraintRadarPage() {
  const { t } = useTranslation();

  const [thresholds, setThresholds] = useState<ConstraintThreshold[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const adoptionFiredRef = useRef(false);

  const [alerts, setAlerts] = useState<ConstraintAlertRecord[]>([]);
  const alertPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const streamUrl = getConstraintStreamUrl();
  const { tiles, streamError, setWarningLevels } = useConstraintStream(streamUrl);

  // Load thresholds on mount
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);

    listConstraintThresholds()
      .then((resp) => {
        if (cancelled) return;
        setThresholds(resp.data);
        const wl = new Map<ConstraintType, number>();
        for (const thresh of resp.data) {
          wl.set(thresh.constraintType, thresh.warningLevel);
        }
        setWarningLevels(wl);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : "Load failed");
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [setWarningLevels]);

  // Poll for active constraint alerts every ≤30 s
  useEffect(() => {
    let cancelled = false;

    function fetchAlerts() {
      listActiveAlerts()
        .then((resp) => {
          if (!cancelled) setAlerts(resp.data);
        })
        .catch(() => {
          // best-effort — don't surface poll errors, keep existing alerts
        });
    }

    fetchAlerts();
    alertPollRef.current = setInterval(fetchAlerts, ALERT_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      if (alertPollRef.current !== null) {
        clearInterval(alertPollRef.current);
        alertPollRef.current = null;
      }
    };
  }, []);

  const handleAlertDismissed = useCallback((alertId: string) => {
    setAlerts((prev) => prev.filter((a) => a.alertId !== alertId));
  }, []);

  // Emit OTel adoption event within 1 second of page load
  useEffect(() => {
    if (adoptionFiredRef.current) return;
    adoptionFiredRef.current = true;
    const handle = setTimeout(() => {
      emitAdoptionEvent("anonymous", "session-" + Math.random().toString(36).slice(2));
    }, 0);
    return () => clearTimeout(handle);
  }, []);

  function handleThresholdSaved(updated: ConstraintThreshold) {
    setThresholds((prev) => {
      const idx = prev.findIndex((th) => th.thresholdId === updated.thresholdId);
      const next = idx >= 0 ? prev.map((th, i) => (i === idx ? updated : th)) : [...prev, updated];
      const wl = new Map<ConstraintType, number>();
      for (const th of next) wl.set(th.constraintType, th.warningLevel);
      setWarningLevels(wl);
      return next;
    });
  }

  function handleRefresh() {
    setLoading(true);
    setLoadError(null);
    listConstraintThresholds()
      .then((resp) => {
        setThresholds(resp.data);
        const wl = new Map<ConstraintType, number>();
        for (const thresh of resp.data) wl.set(thresh.constraintType, thresh.warningLevel);
        setWarningLevels(wl);
        setLoading(false);
      })
      .catch((err) => {
        setLoadError(err instanceof Error ? err.message : "Load failed");
        setLoading(false);
      });
  }

  const thresholdMap = new Map<ConstraintType, ConstraintThreshold>();
  for (const th of thresholds) thresholdMap.set(th.constraintType, th);

  return (
    <main className="main-content" id="constraint-radar">
      <div className="page-header">
        <div className="page-header-left">
          <h2>{t("nav.constraintRadar")}</h2>
          <p className="subtitle">
            {t("constraintRadar.subtitle")}
          </p>
        </div>
        <div className="page-header-right">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setShowConfig((s) => !s)}
            aria-expanded={showConfig}
            aria-controls="threshold-config-panel"
          >
            {t("constraintRadar.configureThresholds")}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleRefresh}
            aria-label={t("constraintRadar.refresh")}
          >
            {t("constraintRadar.refresh")}
          </button>
        </div>
      </div>

      {streamError && (
        <div className="alert-banner stale-banner" role="alert">
          <span className="alert-banner-icon" aria-hidden="true">⚠</span>
          <div className="alert-banner-body">
            <strong>{t("constraintRadar.streamDegraded")}</strong>
            <span>{streamError}</span>
          </div>
        </div>
      )}

      {loading && (
        <p role="status" aria-live="polite">
          {t("constraintRadar.loading")}
        </p>
      )}

      {loadError && !loading && (
        <div role="alert" className="alert-banner warn">
          <span className="alert-banner-icon" aria-hidden="true">⚠</span>
          <div className="alert-banner-body">
            <strong>{t("errors.loadFailed")}</strong>
            <button type="button" className="btn btn-secondary" onClick={handleRefresh}>
              {t("errors.retry")}
            </button>
          </div>
        </div>
      )}

      <ConstraintAlertPanel
        alerts={alerts}
        onAlertDismissed={handleAlertDismissed}
      />

      {!loading && !loadError && (
        <section aria-label={t("constraintRadar.tilesRegionLabel")}>
          <div className="constraint-grid">
            {TILE_CONSTRAINT_TYPES.map((ct) => {
              const tileState = tiles.get(ct) ?? {
                constraintType: ct,
                currentValue: null,
                unit: "",
                measuredAt: null,
                isAmber: false,
                isStale: false,
                lastKnownGoodAt: null,
              };
              const threshold = thresholdMap.get(ct);
              const { label, sublabel } = TILE_LABELS[ct];
              return (
                <ConstraintTile
                  key={ct}
                  state={tileState}
                  threshold={threshold}
                  label={label}
                  sublabel={sublabel}
                />
              );
            })}
          </div>
        </section>
      )}

      {showConfig && (
        <div id="threshold-config-panel">
          <ThresholdConfigPanel
            thresholds={thresholds}
            onSaved={handleThresholdSaved}
          />
        </div>
      )}
    </main>
  );
}
