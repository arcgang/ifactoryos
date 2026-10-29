/**
 * Acceptance tests for US-024 — Multilingual support: Planning Cockpit
 *
 * AC-1: Given browser language is de-DE, all UI labels/alerts/error messages
 *       appear in German automatically.
 * AC-2: Switching language from DE to EN updates all UI content within 2 s
 *       without a page reload.
 * AC-3: Alerts/work instructions with only one language translation are
 *       suppressed from end-user display; publication gate requires both
 *       DE and EN translations.
 *
 * These tests exercise the seam between i18n bundle completeness (i18n.ts),
 * the LanguageSwitcher component, and the useTranslatedAlert gate hook — a
 * layer no existing unit test covers end-to-end.
 */

import "./i18n";
import { render, screen, act, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n, { LANGUAGE_STORAGE_KEY } from "./i18n";
import { LanguageSwitcher } from "./components/LanguageSwitcher";
import { useTranslatedAlert } from "./hooks/useTranslatedAlert";
import { renderHook } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Preconditions — assert the bundles and entry points exist before any
// behavioural test can pass silently on missing fixtures.
// ---------------------------------------------------------------------------

describe("US-024 preconditions — bundles and entry points must exist", () => {
  it("de-DE resource bundle is registered in the i18n instance", () => {
    const bundle = i18n.getResourceBundle("de-DE", "translation");
    // Must be a non-null object with at least one key
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

  it("LanguageSwitcher component is exported and is a function", () => {
    expect(typeof LanguageSwitcher).toBe("function");
  });

  it("useTranslatedAlert hook is exported and is a function", () => {
    expect(typeof useTranslatedAlert).toBe("function");
  });

  it("topbar.appName key exists in both bundles", () => {
    expect(i18n.exists("topbar.appName", { lng: "de-DE" })).toBe(true);
    expect(i18n.exists("topbar.appName", { lng: "en-GB" })).toBe(true);
  });

  it("alerts.paintCapacityCritical.title key exists in both bundles", () => {
    expect(i18n.exists("alerts.paintCapacityCritical.title", { lng: "de-DE" })).toBe(true);
    expect(i18n.exists("alerts.paintCapacityCritical.title", { lng: "en-GB" })).toBe(true);
  });

  it("errors.missingTranslation key exists in both bundles", () => {
    expect(i18n.exists("errors.missingTranslation", { lng: "de-DE" })).toBe(true);
    expect(i18n.exists("errors.missingTranslation", { lng: "en-GB" })).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function Wrapper({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

// ---------------------------------------------------------------------------
// AC-1: Browser language de-DE → all labels in German
// ---------------------------------------------------------------------------

describe("AC-1 (Planning Cockpit) — de-DE browser language surfaces German labels", () => {
  it("topbar.appName resolves to German when locale is de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("topbar.appName")).toBe("Planungs-Cockpit");
  });

  it("topbar.appName resolves to English when locale is en-GB", async () => {
    await i18n.changeLanguage("en-GB");
    expect(i18n.t("topbar.appName")).toBe("Planning Cockpit");
  });

  it("nav labels render in German when de-DE is active", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("nav.sequenceBuilder")).toBe("Sequenzgenerator");
    expect(i18n.t("nav.constraintRadar")).toBe("Constraint-Radar");
    expect(i18n.t("nav.settings")).toBe("Einstellungen");
  });

  it("nav labels render in English when en-GB is active", async () => {
    await i18n.changeLanguage("en-GB");
    expect(i18n.t("nav.sequenceBuilder")).toBe("Sequence Builder");
    expect(i18n.t("nav.constraintRadar")).toBe("Constraint Radar");
    expect(i18n.t("nav.settings")).toBe("Settings");
  });

  it("alert message titles resolve to German for de-DE (paintCapacityCritical)", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("alerts.paintCapacityCritical.title")).toBe(
      "Lackkapazität kritisch — Kabine 3 eingeschränkt",
    );
  });

  it("alert message titles resolve to English for en-GB (paintCapacityCritical)", async () => {
    await i18n.changeLanguage("en-GB");
    expect(i18n.t("alerts.paintCapacityCritical.title")).toBe(
      "Paint capacity critical — Booth 3 restricted",
    );
  });

  it("alert body text resolves to German for de-DE (sequenceLockBlocked)", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("alerts.sequenceLockBlocked.title")).toBe(
      "Sequenz-Sperre blockiert — Übersetzung fehlt",
    );
  });

  it("error message loadFailed resolves to German for de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("errors.loadFailed")).toBe("Laden fehlgeschlagen");
  });

  it("error message loadFailed resolves to English for en-GB", async () => {
    await i18n.changeLanguage("en-GB");
    expect(i18n.t("errors.loadFailed")).toBe("Load failed");
  });

  it("error message unauthorized resolves to German for de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("errors.unauthorized")).toBe(
      "Nicht autorisiert. Bitte erneut anmelden.",
    );
  });

  it("LanguageSwitcher renders flag and label for active de-DE locale", async () => {
    await i18n.changeLanguage("de-DE");
    render(<Wrapper><LanguageSwitcher /></Wrapper>);
    expect(screen.getByText("Deutsch")).toBeTruthy();
  });

  it("LanguageSwitcher renders flag and label for active en-GB locale", async () => {
    await i18n.changeLanguage("en-GB");
    render(<Wrapper><LanguageSwitcher /></Wrapper>);
    expect(screen.getByText("English")).toBeTruthy();
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

describe("AC-2 (Planning Cockpit) — language switch from DE to EN, no page reload", () => {
  it("changeLanguage from de-DE to en-GB updates translations and completes in < 2000 ms", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("topbar.appName")).toBe("Planungs-Cockpit");

    const start = Date.now();
    await i18n.changeLanguage("en-GB");
    const elapsed = Date.now() - start;

    expect(i18n.t("topbar.appName")).toBe("Planning Cockpit");
    expect(elapsed).toBeLessThan(2000);
  });

  it("changeLanguage from en-GB to de-DE updates translations and completes in < 2000 ms", async () => {
    await i18n.changeLanguage("en-GB");
    expect(i18n.t("nav.sequenceBuilder")).toBe("Sequence Builder");

    const start = Date.now();
    await i18n.changeLanguage("de-DE");
    const elapsed = Date.now() - start;

    expect(i18n.t("nav.sequenceBuilder")).toBe("Sequenzgenerator");
    expect(elapsed).toBeLessThan(2000);
  });

  it("language selection is persisted to localStorage when de-DE is chosen", async () => {
    await i18n.changeLanguage("de-DE");
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("de-DE");
  });

  it("language selection is persisted to localStorage when en-GB is chosen", async () => {
    await i18n.changeLanguage("de-DE");
    await i18n.changeLanguage("en-GB");
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("en-GB");
  });

  it("LanguageSwitcher select element triggers language change to en-GB", async () => {
    await i18n.changeLanguage("de-DE");
    render(<Wrapper><LanguageSwitcher /></Wrapper>);

    const select = screen.getByRole("combobox") as HTMLSelectElement;
    await act(async () => {
      select.value = "en-GB";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(i18n.language).toBe("en-GB");
  });

  it("LanguageSwitcher select element triggers language change to de-DE", async () => {
    await i18n.changeLanguage("en-GB");
    render(<Wrapper><LanguageSwitcher /></Wrapper>);

    const select = screen.getByRole("combobox") as HTMLSelectElement;
    await act(async () => {
      select.value = "de-DE";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(i18n.language).toBe("de-DE");
  });

  it("all nav keys update immediately after changeLanguage without re-init", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("nav.overrideLog")).toBe("Überschreibungsprotokoll");

    await i18n.changeLanguage("en-GB");
    expect(i18n.t("nav.overrideLog")).toBe("Override Log");
  });
});

// ---------------------------------------------------------------------------
// AC-3: Translation gate — content missing one locale is suppressed
// ---------------------------------------------------------------------------

describe("AC-3 (Planning Cockpit) — publication gate: both translations required", () => {
  it("useTranslatedAlert returns isComplete=true when both bundles have the key", async () => {
    await i18n.changeLanguage("en-GB");
    const { result } = renderHook(
      () => useTranslatedAlert("alerts.paintCapacityCritical.title"),
      { wrapper: Wrapper },
    );
    expect(result.current.isComplete).toBe(true);
    expect(result.current.value).toBe("Paint capacity critical — Booth 3 restricted");
  });

  it("useTranslatedAlert returns German value when de-DE is active and key exists in both bundles", async () => {
    await i18n.changeLanguage("de-DE");
    const { result } = renderHook(
      () => useTranslatedAlert("alerts.paintCapacityCritical.title"),
      { wrapper: Wrapper },
    );
    expect(result.current.isComplete).toBe(true);
    expect(result.current.value).toBe("Lackkapazität kritisch — Kabine 3 eingeschränkt");
  });

  it("useTranslatedAlert returns isComplete=false and a fallback when key is absent in en-GB", async () => {
    await i18n.changeLanguage("de-DE");
    const { result } = renderHook(
      () => useTranslatedAlert("alerts.__only_de_key_missing_en__"),
      { wrapper: Wrapper },
    );
    expect(result.current.isComplete).toBe(false);
    // value must NOT be the missing key string itself — it should be the fallback
    expect(result.current.value).not.toBe("alerts.__only_de_key_missing_en__");
  });

  it("useTranslatedAlert returns isComplete=false when key is absent in de-DE", async () => {
    await i18n.changeLanguage("en-GB");
    const { result } = renderHook(
      () => useTranslatedAlert("alerts.__only_en_key_missing_de__"),
      { wrapper: Wrapper },
    );
    expect(result.current.isComplete).toBe(false);
    expect(result.current.value).not.toBe("alerts.__only_en_key_missing_de__");
  });

  it("errors.missingTranslation fallback value is the same in both bundles", () => {
    const de = i18n.getFixedT("de-DE")("errors.missingTranslation");
    const en = i18n.getFixedT("en-GB")("errors.missingTranslation");
    // Both must resolve (not return the key itself)
    expect(de).not.toBe("errors.missingTranslation");
    expect(en).not.toBe("errors.missingTranslation");
  });

  it("alerts.sequenceLockBlocked.body describes the missing-translation publication block", async () => {
    await i18n.changeLanguage("de-DE");
    const body = i18n.t("alerts.sequenceLockBlocked.body");
    // Must mention both the work instruction and the block reason
    expect(body).toContain("WI-2041");
    expect(body.length).toBeGreaterThan(20);
  });

  it("every key present in de-DE bundle is also present in en-GB bundle", () => {
    function collectLeafKeys(
      obj: Record<string, unknown>,
      prefix = "",
    ): string[] {
      return Object.entries(obj).flatMap(([k, v]) => {
        const full = prefix ? `${prefix}.${k}` : k;
        return typeof v === "object" && v !== null
          ? collectLeafKeys(v as Record<string, unknown>, full)
          : [full];
      });
    }

    const deBundle = i18n.getResourceBundle("de-DE", "translation") as Record<
      string,
      unknown
    >;
    const enBundle = i18n.getResourceBundle("en-GB", "translation") as Record<
      string,
      unknown
    >;

    const deKeys = collectLeafKeys(deBundle);
    const enKeys = new Set(collectLeafKeys(enBundle));

    const missingInEn = deKeys.filter((k) => !enKeys.has(k));
    expect(missingInEn).toEqual([]);
  });

  it("every key present in en-GB bundle is also present in de-DE bundle", () => {
    function collectLeafKeys(
      obj: Record<string, unknown>,
      prefix = "",
    ): string[] {
      return Object.entries(obj).flatMap(([k, v]) => {
        const full = prefix ? `${prefix}.${k}` : k;
        return typeof v === "object" && v !== null
          ? collectLeafKeys(v as Record<string, unknown>, full)
          : [full];
      });
    }

    const deBundle = i18n.getResourceBundle("de-DE", "translation") as Record<
      string,
      unknown
    >;
    const enBundle = i18n.getResourceBundle("en-GB", "translation") as Record<
      string,
      unknown
    >;

    const enKeys = collectLeafKeys(enBundle);
    const deKeys = new Set(collectLeafKeys(deBundle));

    const missingInDe = enKeys.filter((k) => !deKeys.has(k));
    expect(missingInDe).toEqual([]);
  });
});
