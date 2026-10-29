/**
 * Acceptance tests for US-024 — Multilingual support: Line Command
 *
 * AC-1: Given browser language is de-DE, all UI labels/alert messages/OEE labels
 *       and Andon alerts appear in German automatically.
 * AC-2: Switching language from DE to EN updates all UI content within 2 s
 *       without a page reload.
 * AC-3: Andon alerts with only one language translation are suppressed; both
 *       DE and EN translations must be present before content is shown.
 *
 * Tests the seam between i18n bundle (i18n.ts), LanguageToggle component,
 * useBilingualContent gate hook, and AndonAlert component.
 */

import "./i18n";
import { render, screen, act, cleanup } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n, { LOCALE_STORAGE_KEY } from "./i18n";
import { LanguageToggle } from "./components/LanguageToggle";
import { useBilingualContent } from "./hooks/useBilingualContent";
import { AndonAlert, type AndonAlertContent } from "./components/AndonAlert";

// ---------------------------------------------------------------------------
// Preconditions — fail loudly if bundles or entry points are missing
// ---------------------------------------------------------------------------

describe("US-024 preconditions — Line Command bundles and entry points must exist", () => {
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

  it("LOCALE_STORAGE_KEY is a non-empty string", () => {
    expect(typeof LOCALE_STORAGE_KEY).toBe("string");
    expect(LOCALE_STORAGE_KEY.length).toBeGreaterThan(0);
  });

  it("LanguageToggle component is a function", () => {
    expect(typeof LanguageToggle).toBe("function");
  });

  it("useBilingualContent hook is a function", () => {
    expect(typeof useBilingualContent).toBe("function");
  });

  it("AndonAlert component is a function", () => {
    expect(typeof AndonAlert).toBe("function");
  });

  it("command_center.title key exists in both bundles", () => {
    expect(i18n.exists("command_center.title", { lng: "de-DE" })).toBe(true);
    expect(i18n.exists("command_center.title", { lng: "en-GB" })).toBe(true);
  });

  it("alerts.electrode_wear_title key exists in both bundles", () => {
    expect(i18n.exists("alerts.electrode_wear_title", { lng: "de-DE" })).toBe(true);
    expect(i18n.exists("alerts.electrode_wear_title", { lng: "en-GB" })).toBe(true);
  });

  it("oee_table column keys exist in both bundles", () => {
    for (const key of ["oee_table.col_line", "oee_table.col_current", "oee_table.col_status"]) {
      expect(i18n.exists(key, { lng: "de-DE" }), `de-DE missing ${key}`).toBe(true);
      expect(i18n.exists(key, { lng: "en-GB" }), `en-GB missing ${key}`).toBe(true);
    }
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

describe("AC-1 (Line Command) — de-DE browser language surfaces German labels", () => {
  it("command_center.title resolves to German when de-DE is active", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("command_center.title")).toBe("Schichtleitstand");
  });

  it("command_center.title resolves to English when en-GB is active", async () => {
    expect(i18n.t("command_center.title")).toBe("Shift Command Centre");
  });

  it("OEE table column headers resolve to German for de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("oee_table.col_line")).toBe("Linie");
    expect(i18n.t("oee_table.col_current")).toBe("OEE aktuell");
    expect(i18n.t("oee_table.status_normal")).toBe("Normal");
    expect(i18n.t("oee_table.status_critical")).toBe("Kritisch");
  });

  it("OEE table column headers resolve to English for en-GB", async () => {
    expect(i18n.t("oee_table.col_line")).toBe("Line");
    expect(i18n.t("oee_table.col_current")).toBe("OEE Current");
    expect(i18n.t("oee_table.status_normal")).toBe("Normal");
    expect(i18n.t("oee_table.status_critical")).toBe("Critical");
  });

  it("predictive Andon alert title resolves to German for de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("alerts.electrode_wear_title")).toBe(
      "Prädikativer Andon — Elektrodenverschleiß Schweißzelle 7",
    );
  });

  it("predictive Andon alert title resolves to English for en-GB", async () => {
    const title = i18n.t("alerts.electrode_wear_title");
    expect(title).toContain("Electrode Wear");
  });

  it("maintenance dispatch button label resolves to German for de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("command_center.maintenance_dispatch")).toBe("Wartung beauftragen");
  });

  it("acknowledge button label resolves to English for en-GB", async () => {
    expect(i18n.t("command_center.acknowledge")).toBe("Acknowledge");
  });

  it("nav labels render in German when de-DE is active", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("nav.command_center")).toBe("Leitstand");
    expect(i18n.t("nav.settings")).toBe("Einstellungen");
  });

  it("nav labels render in English when en-GB is active", async () => {
    expect(i18n.t("nav.command_center")).toBe("Command Centre");
    expect(i18n.t("nav.settings")).toBe("Settings");
  });

  it("AndonAlert renders German title when locale is de-DE and both translations are present", async () => {
    await i18n.changeLanguage("de-DE");
    const content: AndonAlertContent = {
      title: {
        "de-DE": "Prädikativer Andon — Elektrodenverschleiß Schweißzelle 7",
        "en-GB": "Predictive Andon — Electrode Wear Weld Cell 7",
      },
      body: {
        "de-DE": "Prognostizierter Ausfall in 14 Minuten. Sofort Wartung beauftragen.",
        "en-GB": "Predicted failure in 14 minutes. Dispatch maintenance immediately.",
      },
    };
    render(<Wrapper><AndonAlert content={content} /></Wrapper>);
    expect(
      screen.getByText("Prädikativer Andon — Elektrodenverschleiß Schweißzelle 7"),
    ).toBeTruthy();
  });

  it("AndonAlert renders English title when locale is en-GB and both translations are present", async () => {
    const content: AndonAlertContent = {
      title: {
        "de-DE": "Prädikativer Andon — Elektrodenverschleiß Schweißzelle 7",
        "en-GB": "Predictive Andon — Electrode Wear Weld Cell 7",
      },
      body: {
        "de-DE": "Prognostizierter Ausfall in 14 Minuten.",
        "en-GB": "Predicted failure in 14 minutes.",
      },
    };
    render(<Wrapper><AndonAlert content={content} /></Wrapper>);
    expect(screen.getByText("Predictive Andon — Electrode Wear Weld Cell 7")).toBeTruthy();
  });

  it("de-DE and en-GB bundles share identical top-level keys (no key drift)", () => {
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

describe("AC-2 (Line Command) — language switch from DE to EN, no page reload", () => {
  it("changeLanguage from de-DE to en-GB updates translations in < 2000 ms", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("command_center.title")).toBe("Schichtleitstand");

    const start = Date.now();
    await i18n.changeLanguage("en-GB");
    const elapsed = Date.now() - start;

    expect(i18n.t("command_center.title")).toBe("Shift Command Centre");
    expect(elapsed).toBeLessThan(2000);
  });

  it("changeLanguage from en-GB to de-DE updates translations in < 2000 ms", async () => {
    expect(i18n.t("oee_table.col_line")).toBe("Line");

    const start = Date.now();
    await i18n.changeLanguage("de-DE");
    const elapsed = Date.now() - start;

    expect(i18n.t("oee_table.col_line")).toBe("Linie");
    expect(elapsed).toBeLessThan(2000);
  });

  it("locale selection is persisted to localStorage when de-DE is chosen", async () => {
    await i18n.changeLanguage("de-DE");
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("de-DE");
  });

  it("locale selection is persisted to localStorage when en-GB is chosen after de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    await i18n.changeLanguage("en-GB");
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en-GB");
  });

  it("LanguageToggle click switches i18n language to de-DE", async () => {
    render(<Wrapper><LanguageToggle /></Wrapper>);
    const deutschBtn = screen.getByRole("button", { name: "Deutsch" });
    await act(async () => { deutschBtn.click(); });
    expect(i18n.language).toBe("de-DE");
  });

  it("LanguageToggle click switches i18n language to en-GB after de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    render(<Wrapper><LanguageToggle /></Wrapper>);
    const englishBtn = screen.getByRole("button", { name: "English" });
    await act(async () => { englishBtn.click(); });
    expect(i18n.language).toBe("en-GB");
  });

  it("AndonAlert re-renders German content after switching to de-DE without unmounting", async () => {
    const content: AndonAlertContent = {
      title: { "de-DE": "OEE unter Tagesziel", "en-GB": "OEE below daily target" },
      body: { "de-DE": "Tendenz fallend.", "en-GB": "Trend falling." },
    };
    const { rerender } = render(<Wrapper><AndonAlert content={content} /></Wrapper>);
    expect(screen.getByText("OEE below daily target")).toBeTruthy();

    await act(async () => { await i18n.changeLanguage("de-DE"); });
    rerender(<Wrapper><AndonAlert content={content} /></Wrapper>);
    expect(screen.getByText("OEE unter Tagesziel")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// AC-3: Translation gate — content missing one locale is suppressed
// ---------------------------------------------------------------------------

describe("AC-3 (Line Command) — translation gate: both DE and EN required before display", () => {
  it("useBilingualContent returns the en-GB string when both translations present and locale is en-GB", () => {
    const { result } = renderHook(
      () => useBilingualContent({ "de-DE": "Schichtleitstand", "en-GB": "Shift Command Centre" }),
      { wrapper: Wrapper },
    );
    expect(result.current).toBe("Shift Command Centre");
  });

  it("useBilingualContent returns the de-DE string when both translations present and locale is de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    const { result } = renderHook(
      () => useBilingualContent({ "de-DE": "Schichtleitstand", "en-GB": "Shift Command Centre" }),
      { wrapper: Wrapper },
    );
    expect(result.current).toBe("Schichtleitstand");
  });

  it("useBilingualContent returns null when en-GB translation is absent (empty string)", () => {
    const { result } = renderHook(
      () => useBilingualContent({ "de-DE": "Nur Deutsch", "en-GB": "" }),
      { wrapper: Wrapper },
    );
    expect(result.current).toBeNull();
  });

  it("useBilingualContent returns null when de-DE translation is absent (empty string)", async () => {
    await i18n.changeLanguage("de-DE");
    const { result } = renderHook(
      () => useBilingualContent({ "de-DE": "", "en-GB": "English only" }),
      { wrapper: Wrapper },
    );
    expect(result.current).toBeNull();
  });

  it("AndonAlert renders 'Translation pending' placeholder when en-GB translation is absent", () => {
    const content: AndonAlertContent = {
      title: { "de-DE": "Nur Deutsch Alarm-Titel", "en-GB": "" },
      body: { "de-DE": "Nur Deutsch Alarm-Text", "en-GB": "" },
    };
    render(<Wrapper><AndonAlert content={content} /></Wrapper>);
    // German-only title must NOT be shown to end users
    expect(screen.queryByText("Nur Deutsch Alarm-Titel")).toBeNull();
    // Instead, the "translation pending" placeholder must appear
    expect(screen.getByText(i18n.t("andon.translation_pending"))).toBeTruthy();
  });

  it("AndonAlert renders 'Translation pending' placeholder when de-DE translation is absent", async () => {
    await i18n.changeLanguage("de-DE");
    const content: AndonAlertContent = {
      title: { "de-DE": "", "en-GB": "English only title" },
      body: { "de-DE": "", "en-GB": "English only body" },
    };
    render(<Wrapper><AndonAlert content={content} /></Wrapper>);
    expect(screen.queryByText("English only title")).toBeNull();
    expect(screen.getByText(i18n.t("andon.translation_pending"))).toBeTruthy();
  });

  it("AndonAlert renders bilingual content (not placeholder) when both translations are present", () => {
    const content: AndonAlertContent = {
      title: { "de-DE": "OEE Warnung", "en-GB": "OEE Warning" },
      body: { "de-DE": "Schwellenwert unterschritten.", "en-GB": "Threshold breached." },
    };
    render(<Wrapper><AndonAlert content={content} /></Wrapper>);
    expect(screen.getByText("OEE Warning")).toBeTruthy();
    expect(screen.queryByText(i18n.t("andon.translation_pending"))).toBeNull();
  });

  it("andon.translation_pending key resolves correctly in German for de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("andon.translation_pending")).toBe("Übersetzung ausstehend");
  });

  it("andon.translation_pending key resolves correctly in English for en-GB", async () => {
    expect(i18n.t("andon.translation_pending")).toBe("Translation pending");
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
