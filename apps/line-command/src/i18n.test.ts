import { beforeEach, describe, expect, it } from "vitest";
import i18n, { SUPPORTED_LOCALES, LOCALE_STORAGE_KEY } from "./i18n";

beforeEach(async () => {
  localStorage.clear();
  await i18n.changeLanguage("en-GB");
});

describe("i18n module — locale resolution and bundle completeness", () => {
  it("SUPPORTED_LOCALES contains exactly de-DE and en-GB", () => {
    expect(SUPPORTED_LOCALES).toContain("de-DE");
    expect(SUPPORTED_LOCALES).toContain("en-GB");
    expect(SUPPORTED_LOCALES).toHaveLength(2);
  });

  it("LOCALE_STORAGE_KEY is a non-empty string", () => {
    expect(typeof LOCALE_STORAGE_KEY).toBe("string");
    expect(LOCALE_STORAGE_KEY.length).toBeGreaterThan(0);
  });

  it("de-DE and en-GB bundles both exist", () => {
    const deDE = i18n.getResourceBundle("de-DE", "translation");
    const enGB = i18n.getResourceBundle("en-GB", "translation");
    expect(deDE).toBeTruthy();
    expect(enGB).toBeTruthy();
  });

  it("de-DE and en-GB bundles have identical top-level section keys", () => {
    const deKeys = Object.keys(i18n.getResourceBundle("de-DE", "translation") as object).sort();
    const enKeys = Object.keys(i18n.getResourceBundle("en-GB", "translation") as object).sort();
    expect(deKeys).toEqual(enKeys);
  });

  it("translates nav.settings to German when locale is de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("nav.settings")).toBe("Einstellungen");
  });

  it("translates nav.settings to English when locale is en-GB", async () => {
    await i18n.changeLanguage("en-GB");
    expect(i18n.t("nav.settings")).toBe("Settings");
  });

  it("falls back to en-GB string for an unknown locale fr-FR", async () => {
    await i18n.changeLanguage("fr-FR");
    expect(i18n.t("nav.settings")).toBe("Settings");
  });

  it("persists the chosen locale to localStorage via changeLanguage (AC-2)", async () => {
    await i18n.changeLanguage("de-DE");
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("de-DE");
  });
});
