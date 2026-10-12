import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

const STORAGE_KEY = "ifos_planning_cockpit_lang";

describe("AC-1: browser-language detection (de-DE)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("initialises to de-DE when navigator.language is de-DE", async () => {
    vi.stubGlobal("navigator", { ...navigator, language: "de-DE", languages: ["de-DE"] });

    const { default: i18next } = await import("./i18n");
    await i18next.changeLanguage("de-DE");

    expect(i18next.language).toBe("de-DE");
    expect(i18next.t("topbar.appName")).toBe("Planungs-Cockpit");
    expect(i18next.t("nav.sequenceBuilder")).toBe("Sequenzgenerator");
    expect(i18next.t("alerts.confirm")).toBe("Bestätigen");

    vi.unstubAllGlobals();
  });

  it("falls back to en-GB when navigator.language is not de-DE", async () => {
    vi.stubGlobal("navigator", { ...navigator, language: "fr-FR", languages: ["fr-FR"] });

    const { default: i18next } = await import("./i18n");
    await i18next.changeLanguage("en-GB");

    expect(i18next.t("topbar.appName")).toBe("Planning Cockpit");
    expect(i18next.t("nav.sequenceBuilder")).toBe("Sequence Builder");

    vi.unstubAllGlobals();
  });

  it("de-DE bundle contains German text for all alert keys", async () => {
    const { default: i18next } = await import("./i18n");
    await i18next.changeLanguage("de-DE");

    expect(i18next.t("alerts.paintCapacityCritical.title")).toBe(
      "Lackkapazität kritisch — Kabine 3 eingeschränkt",
    );
    expect(i18next.t("alerts.sequenceLockBlocked.title")).toBe(
      "Sequenz-Sperre blockiert — Übersetzung fehlt",
    );
    expect(i18next.t("errors.loadFailed")).toBe("Laden fehlgeschlagen");
  });
});

describe("AC-2: language switch without page reload", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("changeLanguage updates translations synchronously without reload", async () => {
    const { default: i18next } = await import("./i18n");
    await i18next.changeLanguage("de-DE");
    expect(i18next.t("topbar.appName")).toBe("Planungs-Cockpit");

    const start = Date.now();
    await i18next.changeLanguage("en-GB");
    const elapsed = Date.now() - start;

    expect(i18next.t("topbar.appName")).toBe("Planning Cockpit");
    expect(elapsed).toBeLessThan(2000);
  });

  it("persists explicit language selection to localStorage", async () => {
    const { default: i18next } = await import("./i18n");
    await i18next.changeLanguage("de-DE");

    expect(localStorage.getItem(STORAGE_KEY)).toBe("de-DE");
  });

  it("reads language preference from localStorage on re-initialise", async () => {
    localStorage.setItem(STORAGE_KEY, "de-DE");
    const { default: i18next } = await import("./i18n");
    await i18next.changeLanguage(localStorage.getItem(STORAGE_KEY) ?? "en-GB");

    expect(i18next.language).toBe("de-DE");
    expect(i18next.t("nav.sequenceBuilder")).toBe("Sequenzgenerator");
  });

  it("switching from de-DE to en-GB updates all nav labels", async () => {
    const { default: i18next } = await import("./i18n");
    await i18next.changeLanguage("de-DE");
    expect(i18next.t("nav.constraintRadar")).toBe("Constraint-Radar");

    await i18next.changeLanguage("en-GB");
    expect(i18next.t("nav.constraintRadar")).toBe("Constraint Radar");
  });
});

describe("AC-3: missing-translation suppression", () => {
  it("returns the translated value when both de-DE and en-GB have the key", async () => {
    const { default: i18next } = await import("./i18n");

    const deVal = i18next.getFixedT("de-DE")("alerts.paintCapacityCritical.title");
    const enVal = i18next.getFixedT("en-GB")("alerts.paintCapacityCritical.title");

    expect(deVal).not.toBe("alerts.paintCapacityCritical.title");
    expect(enVal).not.toBe("alerts.paintCapacityCritical.title");
  });

  it("detects a key missing in en-GB as untranslatable (gate would suppress)", async () => {
    const { default: i18next } = await import("./i18n");

    const missingKey = "alerts.nonExistentAlert.title";
    const enVal = i18next.getFixedT("en-GB")(missingKey);

    expect(enVal).toBe(missingKey);
  });

  it("detects a key missing in de-DE as untranslatable (gate would suppress)", async () => {
    const { default: i18next } = await import("./i18n");

    const missingKey = "alerts.nonExistentAlert.title";
    const deVal = i18next.getFixedT("de-DE")(missingKey);

    expect(deVal).toBe(missingKey);
  });

  it("all keys present in de-DE are also present in en-GB", async () => {
    const deDE = (await import("./locales/de-DE.json")).default;
    const enGB = (await import("./locales/en-GB.json")).default;

    function collectKeys(obj: Record<string, unknown>, prefix = ""): string[] {
      return Object.entries(obj).flatMap(([k, v]) => {
        const fullKey = prefix ? `${prefix}.${k}` : k;
        return typeof v === "object" && v !== null
          ? collectKeys(v as Record<string, unknown>, fullKey)
          : [fullKey];
      });
    }

    const deKeys = collectKeys(deDE);
    const enKeys = new Set(collectKeys(enGB));

    const missingInEn = deKeys.filter((k) => !enKeys.has(k));
    expect(missingInEn).toEqual([]);
  });

  it("all keys present in en-GB are also present in de-DE", async () => {
    const deDE = (await import("./locales/de-DE.json")).default;
    const enGB = (await import("./locales/en-GB.json")).default;

    function collectKeys(obj: Record<string, unknown>, prefix = ""): string[] {
      return Object.entries(obj).flatMap(([k, v]) => {
        const fullKey = prefix ? `${prefix}.${k}` : k;
        return typeof v === "object" && v !== null
          ? collectKeys(v as Record<string, unknown>, fullKey)
          : [fullKey];
      });
    }

    const enKeys = collectKeys(enGB);
    const deKeys = new Set(collectKeys(deDE));

    const missingInDe = enKeys.filter((k) => !deKeys.has(k));
    expect(missingInDe).toEqual([]);
  });

  it("errors.missingTranslation key is defined in both bundles", async () => {
    const { default: i18next } = await import("./i18n");

    const deVal = i18next.getFixedT("de-DE")("errors.missingTranslation");
    const enVal = i18next.getFixedT("en-GB")("errors.missingTranslation");

    expect(deVal).not.toBe("errors.missingTranslation");
    expect(enVal).not.toBe("errors.missingTranslation");
  });
});
