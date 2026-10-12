/**
 * Pure utility functions for constraint radar calculations.
 * Kept dependency-free so they are trivial to unit-test.
 */

/** Return true when currentValue exceeds warningLevel (breach trigger). */
export function isAmberBreach(
  currentValue: number,
  warningLevel: number,
): boolean {
  return currentValue > warningLevel;
}

/**
 * Compute the p99 of an array of numbers.
 * Returns null when the sample is empty.
 */
export function p99(samples: number[]): number | null {
  if (samples.length === 0) return null;
  const sorted = [...samples].sort((a, b) => a - b);
  const idx = Math.ceil(sorted.length * 0.99) - 1;
  return sorted[Math.max(0, idx)];
}

/**
 * Compute the rolling p99 lag (ms) from a window of {lag, timestamp} samples.
 * Only samples whose `timestamp` is within the last `windowMs` milliseconds
 * (relative to `now`) are included.
 *
 * @param window   Circular buffer of lag observations
 * @param now      Current epoch ms (passed in to keep the function pure)
 * @param windowMs Width of the rolling window in ms (default 30 000)
 */
export function rollingP99Lag(
  window: Array<{ lag: number; timestamp: number }>,
  now: number,
  windowMs = 30_000,
): number | null {
  const cutoff = now - windowMs;
  const recent = window.filter((s) => s.timestamp >= cutoff).map((s) => s.lag);
  return p99(recent);
}

/**
 * Return true when the rolling p99 lag for a constraint's recent samples
 * exceeds the staleness threshold.
 */
export function isStalenessTriggered(
  window: Array<{ lag: number; timestamp: number }>,
  now: number,
  thresholdMs = 1_000,
  windowMs = 30_000,
): boolean {
  const lag = rollingP99Lag(window, now, windowMs);
  if (lag === null) return false;
  return lag > thresholdMs;
}
