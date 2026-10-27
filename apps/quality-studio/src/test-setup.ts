import "@testing-library/jest-dom";

// EventSource is not implemented in jsdom — stub it so components that open
// SSE connections don't throw "EventSource is not defined" in unit tests.
class NoopEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  readyState = NoopEventSource.CLOSED;
  url: string;
  constructor(url: string) {
    this.url = url;
  }
  close() {}
  addEventListener() {}
  removeEventListener() {}
  dispatchEvent() { return false; }
}

if (typeof globalThis.EventSource === "undefined") {
  (globalThis as unknown as Record<string, unknown>).EventSource = NoopEventSource;
}
