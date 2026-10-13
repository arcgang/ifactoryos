import { describe, expect, it } from "vitest";
import {
  isAmberBreach,
  isStalenessTriggered,
  p99,
  rollingP99Lag,
} from "./constraintUtils";

describe("isAmberBreach", () => {
  it("returns false when currentValue equals warningLevel", () => {
    expect(isAmberBreach(85, 85)).toBe(false);
  });

  it("returns false when currentValue is below warningLevel", () => {
    expect(isAmberBreach(78, 85)).toBe(false);
  });

  it("returns true when currentValue strictly exceeds warningLevel", () => {
    expect(isAmberBreach(86, 85)).toBe(true);
  });

  it("returns true for a fractional breach", () => {
    expect(isAmberBreach(85.1, 85)).toBe(true);
  });
});

describe("p99", () => {
  it("returns null for an empty array", () => {
    expect(p99([])).toBeNull();
  });

  it("returns the single element for a one-element array", () => {
    expect(p99([42])).toBe(42);
  });

  it("returns the maximum of a small sorted array", () => {
    // 100 elements: p99 is the 99th sorted value (index 98)
    const samples = Array.from({ length: 100 }, (_, i) => i + 1);
    expect(p99(samples)).toBe(99);
  });

  it("returns the highest value for 2-element array", () => {
    expect(p99([100, 200])).toBe(200);
  });

  it("does not mutate the input array", () => {
    const arr = [5, 3, 1, 4, 2];
    p99(arr);
    expect(arr).toEqual([5, 3, 1, 4, 2]);
  });
});

describe("rollingP99Lag", () => {
  const NOW = 1_000_000;

  it("returns null when window is empty", () => {
    expect(rollingP99Lag([], NOW)).toBeNull();
  });

  it("excludes samples older than windowMs", () => {
    const window = [
      { lag: 5000, timestamp: NOW - 31_000 }, // outside 30s window
      { lag: 100, timestamp: NOW - 5_000 },   // inside
    ];
    expect(rollingP99Lag(window, NOW)).toBe(100);
  });

  it("includes samples exactly at the window boundary", () => {
    const window = [
      { lag: 999, timestamp: NOW - 30_000 },
    ];
    expect(rollingP99Lag(window, NOW)).toBe(999);
  });

  it("computes p99 of recent samples only", () => {
    const recent = Array.from({ length: 99 }, (_, i) => ({
      lag: i + 1,
      timestamp: NOW - 1_000,
    }));
    recent.push({ lag: 2000, timestamp: NOW - 1_000 }); // 100th sample
    expect(rollingP99Lag(recent, NOW)).toBe(99);
  });
});

describe("isStalenessTriggered", () => {
  const NOW = 2_000_000;

  it("returns false when window is empty", () => {
    expect(isStalenessTriggered([], NOW)).toBe(false);
  });

  it("returns false when p99 lag is below 1000 ms threshold", () => {
    const window = [{ lag: 800, timestamp: NOW - 1_000 }];
    expect(isStalenessTriggered(window, NOW)).toBe(false);
  });

  it("returns false when p99 lag equals exactly 1000 ms", () => {
    const window = [{ lag: 1000, timestamp: NOW - 1_000 }];
    expect(isStalenessTriggered(window, NOW)).toBe(false);
  });

  it("returns true when p99 lag exceeds 1000 ms", () => {
    const window = [{ lag: 1001, timestamp: NOW - 1_000 }];
    expect(isStalenessTriggered(window, NOW)).toBe(true);
  });

  it("is not triggered by old samples outside the 30 s window", () => {
    const window = [
      { lag: 5000, timestamp: NOW - 60_000 }, // outside
      { lag: 200, timestamp: NOW - 1_000 },   // inside, low lag
    ];
    expect(isStalenessTriggered(window, NOW)).toBe(false);
  });
});
