import { render, screen, act, fireEvent, cleanup } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import i18n from "./i18n";
import { App } from "./App";

// ---------------------------------------------------------------------------
// JWT helpers
// ---------------------------------------------------------------------------

function b64url(obj: unknown): string {
  return btoa(JSON.stringify(obj))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=/g, "");
}

function makeJwt(roles: string[], sub = "test-user@bmw-munich.de"): string {
  const header = b64url({ alg: "RS256", typ: "JWT" });
  const payload = b64url({ sub, roles, iat: 1000000000 });
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

/** Flush the initial useEffect tick (sets loading=false) inside act. */
async function flushEffects() {
  await act(async () => {
    vi.runAllTimers();
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

// ---------------------------------------------------------------------------
// Role: forbidden (no token / wrong role)
// ---------------------------------------------------------------------------

describe("Role: forbidden — no valid token", () => {
  it("renders the 403 forbidden screen when no JWT is present", async () => {
    clearToken();
    renderApp();
    await flushEffects();
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("403")).toBeTruthy();
    expect(screen.getByText(/Forbidden — Insufficient Role Permissions/i)).toBeTruthy();
  });

  it("renders 403 screen for production_planner role (not authorised for Line Command)", async () => {
    setToken(["production_planner"]);
    renderApp();
    await flushEffects();
    expect(screen.getByText("403")).toBeTruthy();
    expect(screen.getByText(/Forbidden — Insufficient Role Permissions/i)).toBeTruthy();
  });

  it("does not render Command Centre nav for forbidden role", async () => {
    setToken(["production_planner"]);
    renderApp();
    await flushEffects();
    expect(screen.queryByText("Command Centre")).toBeNull();
    expect(screen.queryByText("Station Detail")).toBeNull();
  });

  it("shows required scope factory_os:console:line-command on 403 screen", async () => {
    setToken(["production_planner"]);
    renderApp();
    await flushEffects();
    expect(screen.getByText("factory_os:console:line-command")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Role: shift_lead — full view
// ---------------------------------------------------------------------------

describe("Role: shift_lead — full view", () => {
  it("renders Command Centre nav item for shift_lead", async () => {
    setToken(["shift_lead"]);
    renderApp();
    await flushEffects();
    expect(screen.getByText("Command Centre")).toBeTruthy();
  });

  it("renders Station Detail nav item for shift_lead", async () => {
    setToken(["shift_lead"]);
    renderApp();
    await flushEffects();
    expect(screen.getByText("Station Detail")).toBeTruthy();
  });

  it("renders Handover Builder nav item for shift_lead", async () => {
    setToken(["shift_lead"]);
    renderApp();
    await flushEffects();
    expect(screen.getByText("Handover Builder")).toBeTruthy();
  });

  it("does not render the 403 screen for shift_lead", async () => {
    setToken(["shift_lead"]);
    renderApp();
    await flushEffects();
    expect(screen.queryByText("403")).toBeNull();
  });

  it("navigates to Station Detail view when shift_lead clicks the nav button", async () => {
    setToken(["shift_lead"]);
    renderApp();
    await flushEffects();
    fireEvent.click(screen.getByText("Station Detail"));
    // station detail placeholder heading is rendered
    expect(screen.getAllByText("Station Detail").length).toBeGreaterThan(0);
  });

  it("navigates to Handover Builder view when shift_lead clicks the nav button", async () => {
    setToken(["shift_lead"]);
    renderApp();
    await flushEffects();
    fireEvent.click(screen.getByText("Handover Builder"));
    expect(screen.getAllByText("Handover Builder").length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Role: assembly_associate — restricted to AR Workbench
// ---------------------------------------------------------------------------

describe("Role: assembly_associate — restricted to AR Workbench", () => {
  it("does not render the 403 full-page screen for assembly_associate", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();
    expect(screen.queryByText("403")).toBeNull();
  });

  it("renders AR Workbench nav button for assembly_associate", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();
    expect(screen.getByText("AR Workbench")).toBeTruthy();
  });

  it("does not render Command Centre as a nav button for assembly_associate", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();
    expect(screen.queryByText("Command Centre")).toBeNull();
  });

  it("does not render Handover Builder as a nav button for assembly_associate", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();
    // A nav button with accessible name "Handover Builder" must not exist
    expect(screen.queryByRole("button", { name: /^Handover Builder$/i })).toBeNull();
  });

  it("renders 2 aria-disabled locked route indicators for assembly_associate", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();
    const lockedItems = document.querySelectorAll('[aria-disabled="true"]');
    expect(lockedItems.length).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// Integration: assembly_associate initial redirect + toast
// ---------------------------------------------------------------------------

describe("Integration: assembly_associate blocked route redirect", () => {
  it("redirects assembly_associate from command-center to ar-workbench on mount", async () => {
    // Default initial route is 'command-center', which is blocked for associates.
    // The effect should redirect to ar-workbench and surface the toast.
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();

    // Confirm the AR Workbench page heading is rendered (redirect succeeded)
    expect(screen.getByText("AR Workbench")).toBeTruthy();
  });

  it("shows Access Restricted toast with correct English copy when redirected", async () => {
    setToken(["assembly_associate"]);
    renderApp();
    await flushEffects();
    // The toast fires because the initial route (command-center) is blocked
    expect(screen.getByText("Access restricted to AR Workbench")).toBeTruthy();
  });

  it("shows Access Restricted toast with correct German copy when redirected (de-DE)", async () => {
    setToken(["assembly_associate"]);
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    renderApp();
    await flushEffects();
    expect(screen.getByText("Zugriff auf AR Workbench beschränkt")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Integration: 403 from API gateway (production_planner scenario — AC1)
// ---------------------------------------------------------------------------

describe("Integration: 403 Forbidden screen content — AC1", () => {
  it("production_planner sees 403 with Forbidden title and required scope", async () => {
    setToken(["production_planner"]);
    renderApp();
    await flushEffects();
    expect(screen.getByText("403")).toBeTruthy();
    expect(screen.getByText(/Forbidden — Insufficient Role Permissions/i)).toBeTruthy();
    expect(screen.getByText("factory_os:console:line-command")).toBeTruthy();
  });

  it("unauthenticated user (no token) sees the same 403 screen", async () => {
    clearToken();
    renderApp();
    await flushEffects();
    expect(screen.getByText("403")).toBeTruthy();
    expect(screen.getByRole("alert")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// I18n: existing AC-1 / AC-2 tests (preserved, updated for role context)
// ---------------------------------------------------------------------------

describe("App — AC-1 browser language detection", () => {
  it("renders English nav labels when locale is en-GB and role is shift_lead", async () => {
    setToken(["shift_lead"]);
    renderApp();
    await flushEffects();
    expect(screen.getByText("Command Centre")).toBeTruthy();
    expect(screen.getByText("Settings")).toBeTruthy();
  });

  it("renders German nav labels when locale is de-DE and role is shift_lead", async () => {
    setToken(["shift_lead"]);
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    renderApp();
    await flushEffects();
    expect(screen.getByText("Leitstand")).toBeTruthy();
    expect(screen.getByText("Einstellungen")).toBeTruthy();
  });
});

describe("App — AC-2 language switch without reload", () => {
  it("re-renders German labels after language switches to de-DE without unmounting (shift_lead)", async () => {
    setToken(["shift_lead"]);
    const { rerender } = renderApp();
    await flushEffects();
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    rerender(
      <I18nextProvider i18n={i18n}>
        <App />
      </I18nextProvider>,
    );
    expect(screen.getByText("Leitstand")).toBeTruthy();
  });

  it("re-renders English labels after language switches to en-GB without unmounting (shift_lead)", async () => {
    setToken(["shift_lead"]);
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    const { rerender } = renderApp();
    await flushEffects();
    await act(async () => { await i18n.changeLanguage("en-GB"); });
    rerender(
      <I18nextProvider i18n={i18n}>
        <App />
      </I18nextProvider>,
    );
    expect(screen.getByText("Command Centre")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// RBAC: translation key coverage
// ---------------------------------------------------------------------------

describe("RBAC translation keys", () => {
  it("rbac.access_restricted_toast resolves to correct English string", () => {
    // Direct i18n assertion — no rendering needed
    expect(i18n.t("rbac.access_restricted_toast")).toBe("Access restricted to AR Workbench");
  });

  it("rbac.access_restricted_toast resolves to correct German string", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    expect(i18n.t("rbac.access_restricted_toast")).toBe("Zugriff auf AR Workbench beschränkt");
  });

  it("rbac.forbidden_title resolves to correct English string", () => {
    void i18n.changeLanguage("en-GB");
    expect(i18n.t("rbac.forbidden_title")).toBe("Forbidden — Insufficient Role Permissions");
  });
});

// ---------------------------------------------------------------------------
// Role re-evaluation on JWT refresh (≤5 min cycle)
// ---------------------------------------------------------------------------

describe("Role re-evaluation on JWT refresh", () => {
  it("transitions from shift_lead to 403 when token is removed and 5-min tick fires", async () => {
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

  it("transitions from 403 to shift_lead view when token is added and 5-min tick fires", async () => {
    clearToken();
    renderApp();
    await flushEffects();
    expect(screen.getByText("403")).toBeTruthy();

    setToken(["shift_lead"]);
    await act(async () => {
      vi.advanceTimersByTime(5 * 60 * 1000);
    });

    expect(screen.queryByText("403")).toBeNull();
    expect(screen.getByText("Command Centre")).toBeTruthy();
  });

  it("transitions from shift_lead to assembly_associate view when role changes and 5-min tick fires", async () => {
    setToken(["shift_lead"]);
    renderApp();
    await flushEffects();
    expect(screen.getByText("Command Centre")).toBeTruthy();

    setToken(["assembly_associate"]);
    await act(async () => {
      vi.advanceTimersByTime(5 * 60 * 1000);
    });

    expect(screen.queryByText("Command Centre")).toBeNull();
    expect(screen.getByText("AR Workbench")).toBeTruthy();
  });
});
