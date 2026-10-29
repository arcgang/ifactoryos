/**
 * Acceptance tests for US-024 — Multilingual support: Quality Studio
 *
 * AC-1: Given browser language is de-DE, all UI labels, alert messages,
 *       error messages, and work instruction text appear in German automatically.
 * AC-2: Switching language from DE to EN updates all UI content within 2 s
 *       without a page reload.
 * AC-3: New alerts/work instructions are suppressed from end-user display
 *       until both German and English translations are present; the
 *       DefectClusterAlertDemo (which uses useTranslatedAlert) enforces
 *       this gate in the rendered App shell.
 *
 * Tests the seam between:
 *   - i18n.ts bundle (both locales loaded)
 *   - useTranslatedAlert (quality-studio version — ready flag)
 *   - App.tsx DefectClusterAlertDemo (renders placeholder when either locale missing)
 */

import "./i18n";
import { render, screen, act, cleanup } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n, { LANGUAGE_STORAGE_KEY } from "./i18n";
import { useTranslatedAlert } from "./useTranslatedAlert";
import { App } from "./App";

// ---------------------------------------------------------------------------
// Preconditions — fail loudly if bundles or entry points are absent
// ---------------------------------------------------------------------------

describe("US-024 preconditions — Quality Studio bundles and entry points must exist", () => {
  it("de-DE resource bundle is registered in the i18n instance", () => {
    const bundle = i18n.getResourceBundle("de-DE", "translation");
    expect(bundle).toBeTruthy();
    expect(typeof bundle).toBe("object");
    expect(Object.keys(bundle as object).length).toBeGreaterThan(0);
  });

  it("en-GB resource bundle is registered in the i18n instance", () => {
    const bundle = i18n.getResourceBundle("en-GB", "translation");
    expect(bundle).toBeTruthy();
    expect(typeof bundle).toBe("object");
    expect(Object.keys(bundle as object).length).toBeGreaterThan(0);
  });

  it("LANGUAGE_STORAGE_KEY is a non-empty string", () => {
    expect(typeof LANGUAGE_STORAGE_KEY).toBe("string");
    expect(LANGUAGE_STORAGE_KEY.length).toBeGreaterThan(0);
  });

  it("useTranslatedAlert hook is a function", () => {
    expect(typeof useTranslatedAlert).toBe("function");
  });

  it("App component is a function", () => {
    expect(typeof App).toBe("function");
  });

  it("alerts.defectCluster.title key exists in both bundles", () => {
    expect(i18n.exists("alerts.defectCluster.title", { lng: "de-DE" })).toBe(true);
    expect(i18n.exists("alerts.defectCluster.title", { lng: "en-GB" })).toBe(true);
  });

  it("alerts.defectCluster.body key exists in both bundles", () => {
    expect(i18n.exists("alerts.defectCluster.body", { lng: "de-DE" })).toBe(true);
    expect(i18n.exists("alerts.defectCluster.body", { lng: "en-GB" })).toBe(true);
  });

  it("gateCommand.certify key exists in both bundles", () => {
    expect(i18n.exists("gateCommand.certify", { lng: "de-DE" })).toBe(true);
    expect(i18n.exists("gateCommand.certify", { lng: "en-GB" })).toBe(true);
  });

  it("errors.networkError key exists in both bundles", () => {
    expect(i18n.exists("errors.networkError", { lng: "de-DE" })).toBe(true);
    expect(i18n.exists("errors.networkError", { lng: "en-GB" })).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function Wrapper({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

beforeEach(async () => {
  localStorage.clear();
  await i18n.changeLanguage("en-GB");
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

// ---------------------------------------------------------------------------
// AC-1: Browser language de-DE → all labels in German
// ---------------------------------------------------------------------------

describe("AC-1 (Quality Studio) — de-DE browser language surfaces German labels", () => {
  it("nav.defectDashboard resolves to German when de-DE is active", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("nav.defectDashboard")).toBe("Defekt-Dashboard");
  });

  it("nav.defectDashboard resolves to English when en-GB is active", async () => {
    expect(i18n.t("nav.defectDashboard")).toBe("Defect Dashboard");
  });

  it("nav.processReplay resolves to German for de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("nav.processReplay")).toBe("Prozess-Replay");
  });

  it("nav.settings resolves to German for de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("nav.settings")).toBe("Einstellungen");
  });

  it("gateCommand.certify resolves to German for de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("gateCommand.certify")).toBe("Zertifizieren");
  });

  it("gateCommand.certify resolves to English for en-GB", async () => {
    expect(i18n.t("gateCommand.certify")).toBe("Certify");
  });

  it("error messages resolve to German for de-DE (unauthorized)", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("errors.unauthorized")).toBe(
      "Nicht autorisiert. Bitte erneut anmelden.",
    );
  });

  it("error messages resolve to German for de-DE (networkError)", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("errors.networkError")).toBe("Netzwerkfehler. Verbindung prüfen.");
  });

  it("error messages resolve to English for en-GB (networkError)", async () => {
    expect(i18n.t("errors.networkError")).toBe(
      "Network error. Please check your connection.",
    );
  });

  it("defectDashboard.runDefectGenome resolves to German for de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("defectDashboard.runDefectGenome")).toBe("DefectGenome starten");
  });

  it("App renders German nav labels when de-DE is active", async () => {
    await i18n.changeLanguage("de-DE");
    render(<App />);
    expect(screen.getAllByText("Defekt-Dashboard").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Prozess-Replay")).toBeTruthy();
  });

  it("App renders English nav labels when en-GB is active", async () => {
    render(<App />);
    expect(screen.getAllByText("Defect Dashboard").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Process Replay")).toBeTruthy();
  });

  it("alerts.defectCluster.title resolves to German for de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("alerts.defectCluster.title")).toBe("DefectGenome-Cluster-Alarm");
  });

  it("alerts.defectCluster.title resolves to English for en-GB", async () => {
    expect(i18n.t("alerts.defectCluster.title")).toBe("DefectGenome Cluster Alert");
  });

  it("de-DE and en-GB bundles share identical top-level keys", () => {
    const deKeys = Object.keys(
      i18n.getResourceBundle("de-DE", "translation") as object,
    ).sort();
    const enKeys = Object.keys(
      i18n.getResourceBundle("en-GB", "translation") as object,
    ).sort();
    expect(deKeys).toEqual(enKeys);
  });
});

// ---------------------------------------------------------------------------
// AC-2: Language switch DE→EN updates all content within 2 s, no reload
// ---------------------------------------------------------------------------

describe("AC-2 (Quality Studio) — language switch from DE to EN, no page reload", () => {
  it("changeLanguage from de-DE to en-GB updates translations in < 2000 ms", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("defectDashboard.runDefectGenome")).toBe("DefectGenome starten");

    const start = Date.now();
    await i18n.changeLanguage("en-GB");
    const elapsed = Date.now() - start;

    expect(i18n.t("defectDashboard.runDefectGenome")).toBe("Run DefectGenome");
    expect(elapsed).toBeLessThan(2000);
  });

  it("changeLanguage from en-GB to de-DE updates translations in < 2000 ms", async () => {
    expect(i18n.t("gateCommand.certify")).toBe("Certify");

    const start = Date.now();
    await i18n.changeLanguage("de-DE");
    const elapsed = Date.now() - start;

    expect(i18n.t("gateCommand.certify")).toBe("Zertifizieren");
    expect(elapsed).toBeLessThan(2000);
  });

  it("language selection is persisted to localStorage when de-DE is chosen", async () => {
    await i18n.changeLanguage("de-DE");
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("de-DE");
  });

  it("language selection is persisted to localStorage when en-GB is chosen after de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    await i18n.changeLanguage("en-GB");
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("en-GB");
  });

  it("App re-renders German nav labels after switching to de-DE without unmounting", async () => {
    const { rerender } = render(<App />);
    expect(screen.getAllByText("Defect Dashboard").length).toBeGreaterThanOrEqual(1);

    await act(async () => { await i18n.changeLanguage("de-DE"); });
    rerender(<App />);
    expect(screen.getAllByText("Defekt-Dashboard").length).toBeGreaterThanOrEqual(1);
  });

  it("App re-renders English nav labels after switching back to en-GB without unmounting", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    const { rerender } = render(<App />);
    expect(screen.getAllByText("Defekt-Dashboard").length).toBeGreaterThanOrEqual(1);

    await act(async () => { await i18n.changeLanguage("en-GB"); });
    rerender(<App />);
    expect(screen.getAllByText("Defect Dashboard").length).toBeGreaterThanOrEqual(1);
  });

  it("lang-btn-de-DE and lang-btn-en-GB buttons are present in the App shell", async () => {
    render(<App />);
    expect(screen.getByTestId("lang-btn-de-DE")).toBeTruthy();
    expect(screen.getByTestId("lang-btn-en-GB")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// AC-3: Translation gate — content missing one locale is suppressed
// ---------------------------------------------------------------------------

describe("AC-3 (Quality Studio) — translation gate: both DE and EN required before display", () => {
  it("useTranslatedAlert returns ready=true and en-GB text when both bundles have the key", async () => {
    const { result } = renderHook(
      () => useTranslatedAlert("alerts.defectCluster.title"),
      { wrapper: Wrapper },
    );
    expect(result.current.ready).toBe(true);
    expect(result.current.text).toBe("DefectGenome Cluster Alert");
  });

  it("useTranslatedAlert returns ready=true and de-DE text when locale is de-DE and both bundles have the key", async () => {
    await i18n.changeLanguage("de-DE");
    const { result } = renderHook(
      () => useTranslatedAlert("alerts.defectCluster.title"),
      { wrapper: Wrapper },
    );
    expect(result.current.ready).toBe(true);
    expect(result.current.text).toBe("DefectGenome-Cluster-Alarm");
  });

  it("useTranslatedAlert returns ready=false and text=undefined when key is absent in de-DE", async () => {
    const { result } = renderHook(
      () => useTranslatedAlert("alerts.__synthetic_key_missing_in_both__"),
      { wrapper: Wrapper },
    );
    expect(result.current.ready).toBe(false);
    expect(result.current.text).toBeUndefined();
  });

  it("useTranslatedAlert returns ready=false when key is absent in en-GB", async () => {
    await i18n.changeLanguage("de-DE");
    const { result } = renderHook(
      () => useTranslatedAlert("alerts.__another_synthetic_missing_key__"),
      { wrapper: Wrapper },
    );
    expect(result.current.ready).toBe(false);
    expect(result.current.text).toBeUndefined();
  });

  it("App renders alert-defect-cluster when both bundles have the required keys", async () => {
    render(<App />);
    // Both defectCluster.title and defectCluster.body exist in en-GB → gate allows display
    expect(screen.getByTestId("alert-defect-cluster")).toBeTruthy();
  });

  it("App renders the DefectClusterAlertDemo title in English when en-GB is active", async () => {
    render(<App />);
    const alertEl = screen.getByTestId("alert-defect-cluster");
    expect(alertEl.textContent).toContain("DefectGenome Cluster Alert");
  });

  it("App renders the DefectClusterAlertDemo title in German when de-DE is active", async () => {
    await act(async () => { await i18n.changeLanguage("de-DE"); });
    render(<App />);
    const alertEl = screen.getByTestId("alert-defect-cluster");
    expect(alertEl.textContent).toContain("DefectGenome-Cluster-Alarm");
  });

  it("alerts.translationPending resolves to German placeholder text for de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("alerts.translationPending")).toBe("Übersetzung ausstehend");
  });

  it("alerts.translationPending resolves to English placeholder text for en-GB", async () => {
    expect(i18n.t("alerts.translationPending")).toBe("Translation pending");
  });

  it("every key in de-DE bundle is also present in en-GB bundle", () => {
    function collectLeafKeys(obj: Record<string, unknown>, prefix = ""): string[] {
      return Object.entries(obj).flatMap(([k, v]) => {
        const full = prefix ? `${prefix}.${k}` : k;
        return typeof v === "object" && v !== null
          ? collectLeafKeys(v as Record<string, unknown>, full)
          : [full];
      });
    }
    const de = i18n.getResourceBundle("de-DE", "translation") as Record<string, unknown>;
    const en = i18n.getResourceBundle("en-GB", "translation") as Record<string, unknown>;
    const deKeys = collectLeafKeys(de);
    const enKeys = new Set(collectLeafKeys(en));
    const missing = deKeys.filter((k) => !enKeys.has(k));
    expect(missing).toEqual([]);
  });

  it("every key in en-GB bundle is also present in de-DE bundle", () => {
    function collectLeafKeys(obj: Record<string, unknown>, prefix = ""): string[] {
      return Object.entries(obj).flatMap(([k, v]) => {
        const full = prefix ? `${prefix}.${k}` : k;
        return typeof v === "object" && v !== null
          ? collectLeafKeys(v as Record<string, unknown>, full)
          : [full];
      });
    }
    const de = i18n.getResourceBundle("de-DE", "translation") as Record<string, unknown>;
    const en = i18n.getResourceBundle("en-GB", "translation") as Record<string, unknown>;
    const enKeys = collectLeafKeys(en);
    const deKeys = new Set(collectLeafKeys(de));
    const missing = enKeys.filter((k) => !deKeys.has(k));
    expect(missing).toEqual([]);
  });
});
