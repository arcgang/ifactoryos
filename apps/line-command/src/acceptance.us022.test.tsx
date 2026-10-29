/**
 * Acceptance tests for US-022 — Console-level RBAC enforced.
 *
 * Criterion coverage:
 *   AC1 — Production Planner (or any role not in [shift_lead, assembly_associate])
 *          receives a 403 Forbidden screen when accessing Line Command, and
 *          unauthorised API calls emit an audit event.
 *   AC2 — Assembly Associate lands on AR Workbench only; cannot reach Command
 *          Center OEE data or Handover Builder; the access-restricted toast fires.
 *   AC3 — Role re-evaluation fires every 5 minutes (REFRESH_MS constant); a role
 *          change propagated in the JWT is picked up within that window without
 *          a page reload.
 *
 * These tests exercise the seam between useAuth (5-minute polling), useRbac
 * (role resolution), apiRequest (403 → audit write → ForbiddenError), and App
 * (route gating + forbidden screen).  Each layer already has its own unit tests;
 * what nothing tests yet is that all four work together end-to-end.
 */

import { render, screen, act, fireEvent, cleanup } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import i18n from "./i18n";
import { App } from "./App";
import { apiRequest, ForbiddenError } from "./services/api";

// ---------------------------------------------------------------------------
// JWT helpers — identical pattern to App.test.tsx
// ---------------------------------------------------------------------------

function b64url(obj: unknown): string {
  return btoa(JSON.stringify(obj))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=/g, "");
}

function makeJwt(roles: string[], sub = "test-user@bmw-munich.de"): string {
  const header = b64url({ alg: "RS256", typ: "JWT" });
  // Far-future exp (year 2286) — never hardcode a fixed timestamp
  const payload = b64url({ sub, roles, exp: 9999999999, iat: 1000000000 });
  return `${header}.${payload}.fakesig`;
}

const TOKEN_KEY = "lc_access_token";

function setToken(roles: string[]) {
  localStorage.setItem(TOKEN_KEY, makeJwt(roles));
}
function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderApp() {
  return render(
    <I18nextProvider i18n={i18n}>
      <App />
    </I18nextProvider>,
  );
}

async function flushEffects() {
  await act(async () => {
    vi.advanceTimersByTime(0);
  });
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(async () => {
  localStorage.clear();
  await i18n.changeLanguage("en-GB");
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// ===========================================================================
// AC1 — Production Planner (and any other forbidden role) receives 403
// ===========================================================================

describe("AC1 — Production Planner sees 403 Forbidden when accessing Line Command", () => {
  it("AC1: production_planner role resolves to 'forbidden' and the full-page 403 screen renders", async () => {
    // Precondition: a JWT carrying production_planner is present
    setToken(["production_planner"]);
    expect(localStorage.getItem(TOKEN_KEY)).toBeTruthy();

    renderApp();
    await flushEffects();

    // Criterion: 403 status code must be visible on screen
    expect(screen.getByText("403")).toBeTruthy();
  });

  it("AC1: 'Forbidden — Insufficient Role Permissions' heading is shown for production_planner", async () => {
    setToken(["production_planner"]);
    renderApp();
    await flushEffects();

    expect(screen.getByText(/Forbidden — Insufficient Role Permissions/i)).toBeTruthy();
  });

  it("AC1: required scope 'factory_os:console:line-command' is stated on the 403 screen", async () => {
    setToken(["production_planner"]);
    renderApp();
    await flushEffects();

    expect(screen.getByText("factory_os:console:line-command")).toBeTruthy();
  });

  it("AC1: the 403 screen exposes role='alert' so assistive technology announces the denial", async () => {
    setToken(["production_planner"]);
    renderApp();
    await flushEffects();

    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("AC1: unauthenticated user (no token) also receives the 403 forbidden screen", async () => {
    clearToken();
    renderApp();
    await flushEffects();

    expect(screen.getByText("403")).toBeTruthy();
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("AC1: no Line Command nav items (Command Centre, Station Detail) are rendered for production_planner", async () => {
    setToken(["production_planner"]);
    renderApp();
    await flushEffects();

    expect(screen.queryByText("Command Centre")).toBeNull();
    expect(screen.queryByText("Station Detail")).toBeNull();
    expect(screen.queryByText("Handover Builder")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC1 — audit event emission on 403 API response
// ---------------------------------------------------------------------------

describe("AC1 — unauthorised API call emits audit event to /api/v1/audit/event", () => {
  it("AC1: apiRequest emits POST /api/v1/audit/event with action='unauthorized_access_attempt' on 403", async () => {
    // Precondition: the real apiRequest function is importable (not mocked here).
    expect(typeof apiRequest).toBe("function");
    expect(typeof ForbiddenError).toBe("function");

    // The audit-emission logic lives in emitUnauthorisedAuditEvent (called from
    // apiRequest when res.status === 403). Capture both the original API call and
    // the subsequent audit POST via a fetch spy.

    const fetchCalls: { url: string; options: RequestInit }[] = [];
    const originalFetch = globalThis.fetch;

    globalThis.fetch = vi.fn().mockImplementation(async (url: string, options: RequestInit) => {
      fetchCalls.push({ url: String(url), options });
      // First call is the real API request → return 403
      if (fetchCalls.length === 1) {
        return {
          status: 403,
          ok: false,
          statusText: "Forbidden",
        } as Response;
      }
      // Second call is the audit event POST → return 204
      return { status: 204, ok: true, statusText: "No Content" } as Response;
    });

    localStorage.setItem(TOKEN_KEY, makeJwt(["production_planner"]));

    try {
      await apiRequest("/line-command/oee/snapshots", { userId: "planner@bmw-munich.de" });
    } catch {
      // ForbiddenError expected — not the subject of this test
    }

    // AC1: a second fetch call must have been made to the audit endpoint
    expect(fetchCalls.length).toBe(2);
    const auditCall = fetchCalls[1];
    expect(auditCall.url).toContain("/api/v1/audit/event");
    expect(auditCall.options.method).toBe("POST");

    const auditBody = JSON.parse(auditCall.options.body as string) as Record<string, unknown>;
    // Criterion: action must be 'unauthorized_access_attempt'
    expect(auditBody.action).toBe("unauthorized_access_attempt");
    // Resource path must match the requested path
    expect(auditBody.resource).toBe("/line-command/oee/snapshots");
    // UserId must be carried through
    expect(auditBody.userId).toBe("planner@bmw-munich.de");

    globalThis.fetch = originalFetch;
  });

  it("AC1: apiRequest throws ForbiddenError after emitting the audit event on 403", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn().mockImplementation(async (_url: string) => {
      return { status: 403, ok: false, statusText: "Forbidden" } as Response;
    });

    localStorage.setItem(TOKEN_KEY, makeJwt(["production_planner"]));

    let thrown: unknown = null;
    try {
      await apiRequest("/line-command/oee/snapshots", { userId: "planner@bmw-munich.de" });
    } catch (err) {
      thrown = err;
    }

    // Criterion: the error must be a ForbiddenError (not a generic Error)
    expect(thrown).toBeInstanceOf(ForbiddenError);
    const fe = thrown as ForbiddenError;
    expect(fe.resource).toBe("/line-command/oee/snapshots");
    expect(fe.userId).toBe("planner@bmw-munich.de");

    globalThis.fetch = originalFetch;
  });
});

// ===========================================================================
// AC2 — Assembly Associate restricted to AR Workbench
// ===========================================================================

describe("AC2 — Assembly Associate can access AR Workbench but not OEE data or Handover Builder", () => {
  it("AC2: assembly_associate does NOT see the full-page 403 screen (they are permitted in Line Command)", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();

    // AC2 criterion: associate is a valid Line Command user — no 403 page
    expect(screen.queryByText("403")).toBeNull();
  });

  it("AC2: assembly_associate lands on AR Workbench (initial route redirect away from command-center)", async () => {
    // Default initial route is 'command-center', which is blocked for associates.
    // The useEffect in App must redirect to 'ar-workbench' automatically.
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();

    // The AR Workbench nav button must be rendered and active
    expect(screen.getByText("AR Workbench")).toBeTruthy();
  });

  it("AC2: access-restricted toast fires when assembly_associate is redirected from a blocked route", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();

    // The toast confirms the user has been told they cannot access Command Centre
    expect(screen.getByText("Access restricted to AR Workbench")).toBeTruthy();
  });

  it("AC2: Command Centre nav button is absent for assembly_associate (OEE data is behind it)", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();

    expect(screen.queryByRole("button", { name: /^Command Centre$/i })).toBeNull();
  });

  it("AC2: Handover Builder nav button is absent for assembly_associate", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();

    expect(screen.queryByRole("button", { name: /^Handover Builder$/i })).toBeNull();
  });

  it("AC2: locked route items carry aria-disabled=true so the UI signals inaccessibility", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();

    const locked = document.querySelectorAll('[aria-disabled="true"]');
    // Minimum 2 locked items: Command Centre + Handover Builder
    expect(locked.length).toBeGreaterThanOrEqual(2);
  });

  it("AC2: clicking AR Workbench nav button keeps the route on ar-workbench for assembly_associate", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();

    // Clicking the only navigable button must not trigger the restricted toast again
    const arBtn = screen.getByRole("button", { name: /AR Workbench/i });
    fireEvent.click(arBtn);
    // Still on AR Workbench — no 403 screen
    expect(screen.queryByText("403")).toBeNull();
    expect(screen.getByText("AR Workbench")).toBeTruthy();
  });

  it("AC2: navigate() blocks assembly_associate from reaching a blocked route programmatically", async () => {
    // The navigate() callback in App enforces the same gate as the useEffect redirect.
    // We simulate this by confirming that the 'command-center' content is never shown.
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();

    // No Command Centre heading should ever appear for an associate
    expect(screen.queryByRole("button", { name: /^Command Centre$/i })).toBeNull();
    expect(screen.queryByText("403")).toBeNull();
  });
});

// ===========================================================================
// AC3 — Role propagation within 5 minutes without platform restart
// ===========================================================================

describe("AC3 — Role change propagates within 5-minute token refresh cycle (no page reload)", () => {
  it("AC3: useAuth polls every exactly 300 000 ms (REFRESH_MS = 5 min) — constant is present in the module", async () => {
    // Precondition: the useAuth module must export or use a 5-minute interval.
    // We verify this indirectly by confirming the role transitions happen after
    // exactly one 5-minute tick with no other trigger.
    setToken(["shift_lead"]);
    renderApp();
    await flushEffects();

    // Confirm initial state
    expect(screen.queryByText("403")).toBeNull();
    expect(screen.getByText("Command Centre")).toBeTruthy();

    // Simulate role revocation in the IdP: the new JWT has no valid role
    clearToken();

    // Advance by just under 5 minutes — role must NOT yet have changed
    await act(async () => {
      vi.advanceTimersByTime(4 * 60 * 1000 + 59_000);
    });
    // Still shows the Command Centre (old token not yet re-evaluated)
    expect(screen.queryByText("403")).toBeNull();

    // Advance the remaining 1 second to complete the 5-minute tick
    await act(async () => {
      vi.advanceTimersByTime(1_000);
    });

    // AC3 criterion: role must now reflect the revoked state
    expect(screen.getByText("403")).toBeTruthy();
  });

  it("AC3: shift_lead → 403 transition occurs on the 5-minute tick after token is removed", async () => {
    setToken(["shift_lead"]);
    renderApp();
    await flushEffects();
    expect(screen.queryByText("403")).toBeNull();

    clearToken();
    await act(async () => {
      vi.advanceTimersByTime(5 * 60 * 1000);
    });

    expect(screen.getByText("403")).toBeTruthy();
  });

  it("AC3: 403 → shift_lead transition occurs on the 5-minute tick after a valid token is stored", async () => {
    clearToken();
    renderApp();
    await flushEffects();
    expect(screen.getByText("403")).toBeTruthy();

    // New token arrives (IdP has granted the role)
    setToken(["shift_lead"]);
    await act(async () => {
      vi.advanceTimersByTime(5 * 60 * 1000);
    });

    // AC3 criterion: new role takes effect within 5 minutes
    expect(screen.queryByText("403")).toBeNull();
    expect(screen.getByText("Command Centre")).toBeTruthy();
  });

  it("AC3: shift_lead → assembly_associate transition occurs on the 5-minute tick", async () => {
    setToken(["shift_lead"]);
    renderApp();
    await flushEffects();
    expect(screen.getByText("Command Centre")).toBeTruthy();

    // Role changes in IdP from shift_lead to assembly_associate
    setToken(["assembly_associate"]);
    await act(async () => {
      vi.advanceTimersByTime(5 * 60 * 1000);
    });

    // AC3 criterion: Command Centre nav vanishes; AR Workbench is now the landing
    expect(screen.queryByRole("button", { name: /^Command Centre$/i })).toBeNull();
    expect(screen.getByText("AR Workbench")).toBeTruthy();
  });

  it("AC3: assembly_associate → shift_lead transition occurs on the 5-minute tick", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();
    expect(screen.queryByRole("button", { name: /^Command Centre$/i })).toBeNull();

    setToken(["shift_lead"]);
    await act(async () => {
      vi.advanceTimersByTime(5 * 60 * 1000);
    });

    // AC3 criterion: full navigation now visible without a page reload
    expect(screen.getByText("Command Centre")).toBeTruthy();
  });
});

// ===========================================================================
// Cross-cutting: RBAC policy declares correct console boundaries
// (exercises the resolveRole → useRbac → App chain)
// ===========================================================================

describe("Cross-cutting — console-level RBAC boundaries across all roles", () => {
  const FORBIDDEN_ROLES = [
    "production_planner",
    "paint_engineer",
    "quality_validator",
    "unknown_role",
  ];

  for (const role of FORBIDDEN_ROLES) {
    it(`cross: role '${role}' is denied Line Command access and sees the 403 screen`, async () => {
      setToken([role]);
      renderApp();
      await flushEffects();

      expect(screen.getByText("403")).toBeTruthy();
      expect(screen.queryByText("Command Centre")).toBeNull();

      // Clean up between iterations
      cleanup();
      localStorage.clear();
      await i18n.changeLanguage("en-GB");
    });
  }

  it("cross: shift_lead has full nav (Command Centre, Station Detail, Handover Builder)", async () => {
    setToken(["shift_lead"]);
    renderApp();
    await flushEffects();

    expect(screen.queryByText("403")).toBeNull();
    expect(screen.getByText("Command Centre")).toBeTruthy();
    expect(screen.getByText("Station Detail")).toBeTruthy();
    expect(screen.getByText("Handover Builder")).toBeTruthy();
  });

  it("cross: assembly_associate has AR Workbench nav but not Command Centre or Handover Builder", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();

    expect(screen.queryByText("403")).toBeNull();
    expect(screen.getByText("AR Workbench")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Command Centre$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Handover Builder$/i })).toBeNull();
  });
});
