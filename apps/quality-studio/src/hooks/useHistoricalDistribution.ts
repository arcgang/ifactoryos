import { useEffect, useState } from "react";
import type { GateType } from "../services/api";

/**
 * BLOCKING QUESTION FOR REVIEWER:
 * What endpoint serves historical measurement distribution for a given
 * measurementId + gateType? This hook is a stub returning synthesised sample
 * data. The real endpoint must be defined and wired here before AC2 can be
 * fully verified end-to-end. See PR comment for tracking.
 */

export interface DistributionPoint {
  bucket: number;
  count: number;
}

export interface HistoricalDistribution {
  measurementId: string;
  gateType: GateType;
  points: DistributionPoint[];
  p50: number;
  p95: number;
  sampleCount: number;
}

export interface HistoricalDistributionState {
  data: HistoricalDistribution | null;
  loading: boolean;
  error: string | null;
}

export function useHistoricalDistribution(
  measurementId: string,
  gateType: GateType,
): HistoricalDistributionState {
  const [state, setState] = useState<HistoricalDistributionState>({
    data: null,
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    // Stub: returns synthetic distribution data.
    // TODO: replace with real API call once endpoint is defined.
    const timer = setTimeout(() => {
      if (cancelled) return;
      setState({
        data: {
          measurementId,
          gateType,
          points: [
            { bucket: 60, count: 5 },
            { bucket: 62, count: 18 },
            { bucket: 64, count: 42 },
            { bucket: 66, count: 28 },
            { bucket: 68, count: 6 },
            { bucket: 70, count: 1 },
          ],
          p50: 64.1,
          p95: 67.3,
          sampleCount: 100,
        },
        loading: false,
        error: null,
      });
    }, 200);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [measurementId, gateType]);

  return state;
}
