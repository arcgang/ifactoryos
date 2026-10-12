import { vi } from "vitest";

// Provide a no-op fetch so components that call APIs in effects
// don't crash tests that aren't testing network behaviour.
if (typeof globalThis.fetch === "undefined") {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: false,
    status: 503,
    json: () => Promise.resolve({}),
    text: () => Promise.resolve(""),
  } as Response);
}

// Provide a no-op EventSource so SSE hooks don't crash in tests
// that aren't mocking the stream.
if (typeof (globalThis as unknown as Record<string, unknown>).EventSource === "undefined") {
  class NoopEventSource {
    addEventListener() {}
    close() {}
  }
  (globalThis as unknown as Record<string, unknown>).EventSource = NoopEventSource;
}
