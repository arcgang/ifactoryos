import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ConstraintTileState,
  ConstraintType,
  LiveConstraintTelemetry,
} from "../types/constraints";
import { isAmberBreach, isStalenessTriggered } from "../utils/constraintUtils";

const STALENESS_THRESHOLD_MS = 1_000;
const ROLLING_WINDOW_MS = 30_000;
const TICK_INTERVAL_MS = 1_000;

type LagWindow = Array<{ lag: number; timestamp: number }>;

const CONSTRAINT_TYPES: ConstraintType[] = [
  "paint_capacity",
  "body_wip_queue",
  "kitting_readiness",
  "maintenance_window",
];

function makeInitialTiles(): Map<ConstraintType, ConstraintTileState> {
  return new Map(
    CONSTRAINT_TYPES.map((ct) => [
      ct,
      {
        constraintType: ct,
        currentValue: null,
        unit: "",
        measuredAt: null,
        isAmber: false,
        isStale: false,
        lastKnownGoodAt: null,
      },
    ]),
  );
}

export interface UseConstraintStreamResult {
  tiles: Map<ConstraintType, ConstraintTileState>;
  /** null = connected, string = error message */
  streamError: string | null;
  warningLevels: Map<ConstraintType, number>;
  setWarningLevels: (wl: Map<ConstraintType, number>) => void;
}

export function useConstraintStream(
  streamUrl: string,
): UseConstraintStreamResult {
  const [tiles, setTiles] = useState<Map<ConstraintType, ConstraintTileState>>(
    makeInitialTiles,
  );
  const [streamError, setStreamError] = useState<string | null>(null);
  const [warningLevels, setWarningLevels] = useState<
    Map<ConstraintType, number>
  >(new Map());

  // Lag windows keyed by constraintType — held in a ref to avoid re-render on every sample
  const lagWindowsRef = useRef<Map<ConstraintType, LagWindow>>(new Map());
  // Latest measuredAt per type — used to track last-known-good
  const lastGoodRef = useRef<Map<ConstraintType, string>>(new Map());
  const warningLevelsRef = useRef(warningLevels);

  useEffect(() => {
    warningLevelsRef.current = warningLevels;
  }, [warningLevels]);

  const handleMessage = useCallback((telemetry: LiveConstraintTelemetry) => {
    const { constraintType, currentValue, unit, measuredAt, receivedAt } =
      telemetry;

    const measuredMs = new Date(measuredAt).getTime();
    const receivedMs = new Date(receivedAt).getTime();
    const lag = receivedMs - measuredMs;
    const now = Date.now();

    // Update lag window
    const lagWindows = lagWindowsRef.current;
    const lagBuf = lagWindows.get(constraintType) ?? [];
    lagBuf.push({ lag, timestamp: now });
    lagWindows.set(constraintType, lagBuf);

    // Track last known good timestamp when data is fresh (<60 s old)
    const ageMs = now - measuredMs;
    if (ageMs <= 60_000) {
      lastGoodRef.current.set(constraintType, measuredAt);
    }

    setTiles((prev) => {
      const next = new Map(prev);
      const existing = next.get(constraintType) ?? {
        constraintType,
        currentValue: null,
        unit: "",
        measuredAt: null,
        isAmber: false,
        isStale: false,
        lastKnownGoodAt: null,
      };

      const warnLevel = warningLevelsRef.current.get(constraintType);
      const isAmber =
        warnLevel !== undefined ? isAmberBreach(currentValue, warnLevel) : false;

      const stale = isStalenessTriggered(
        lagWindowsRef.current.get(constraintType) ?? [],
        now,
        STALENESS_THRESHOLD_MS,
        ROLLING_WINDOW_MS,
      );

      next.set(constraintType, {
        ...existing,
        currentValue,
        unit,
        measuredAt,
        isAmber,
        isStale: stale,
        lastKnownGoodAt: lastGoodRef.current.get(constraintType) ?? null,
      });
      return next;
    });
  }, []);

  useEffect(() => {
    if (!streamUrl) return;

    let es: EventSource | null = null;
    let closed = false;

    function connect() {
      if (closed) return;
      es = new EventSource(streamUrl);

      es.addEventListener("telemetry", ((evt: Event) => {
        try {
          const data = JSON.parse((evt as MessageEvent).data) as LiveConstraintTelemetry;
          handleMessage(data);
          setStreamError(null);
        } catch {
          // malformed message — ignore
        }
      }) as EventListener);

      es.addEventListener("error", (() => {
        setStreamError("UNS stream disconnected");
      }) as EventListener);
    }

    connect();

    // Periodic tick: recompute staleness flags using current lag windows
    const tickId = setInterval(() => {
      const now = Date.now();
      setTiles((prev) => {
        let changed = false;
        const next = new Map(prev);
        for (const [ct, tile] of next) {
          const stale = isStalenessTriggered(
            lagWindowsRef.current.get(ct) ?? [],
            now,
            STALENESS_THRESHOLD_MS,
            ROLLING_WINDOW_MS,
          );
          if (stale !== tile.isStale) {
            next.set(ct, { ...tile, isStale: stale });
            changed = true;
          }
        }
        return changed ? next : prev;
      });
    }, TICK_INTERVAL_MS);

    return () => {
      closed = true;
      es?.close();
      clearInterval(tickId);
    };
  }, [streamUrl, handleMessage]);

  return {
    tiles,
    streamError,
    warningLevels,
    setWarningLevels,
  };
}
