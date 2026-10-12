import { describe, it, expect, beforeEach } from "vitest";
import i18n from "./i18n";

describe("i18n initialisation", () => {
  it("falls back to en-GB when no preference is stored", async () => {
    await i18n.changeLanguage("en-GB");
    expect(i18n.language).toBe("en-GB");
    expect(i18n.t("app.title")).toBe("Quality Studio");
  });

  it("resolves German labels when language is de-DE (AC-edd6ce81)", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("nav.defectDashboard")).toBe("Defekt-Dashboard");
    expect(i18n.t("nav.processReplay")).toBe("Prozess-Replay");
    expect(i18n.t("nav.gateCommand")).toBe("Gate Command");
    expect(i18n.t("settings.save")).toBe("Speichern");
  });

  it("resolves English labels when language is en-GB", async () => {
    await i18n.changeLanguage("en-GB");
    expect(i18n.t("nav.defectDashboard")).toBe("Defect Dashboard");
    expect(i18n.t("nav.processReplay")).toBe("Process Replay");
    expect(i18n.t("nav.gateCommand")).toBe("Gate Command");
    expect(i18n.t("settings.save")).toBe("Save");
  });

  it("switches all labels from German to English without reload (AC-e41c448a)", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("defectDashboard.runDefectGenome")).toBe(
      "DefectGenome starten",
    );

    await i18n.changeLanguage("en-GB");
    expect(i18n.t("defectDashboard.runDefectGenome")).toBe("Run DefectGenome");
  });

  it("switches all labels from English to German without reload (AC-e41c448a)", async () => {
    await i18n.changeLanguage("en-GB");
    expect(i18n.t("gateCommand.certify")).toBe("Certify");

    await i18n.changeLanguage("de-DE");
    expect(i18n.t("gateCommand.certify")).toBe("Zertifizieren");
  });

  it("interpolates defect cluster alert body in de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    const result = i18n.t("alerts.defectCluster.body", {
      clusterId: "DG-001",
      count: 3,
      rootCause: "Elektrotauchbeschichtung",
      confidence: 82,
    });
    expect(result).toContain("DG-001");
    expect(result).toContain("3");
    expect(result).toContain("82");
  });

  it("interpolates gate anomaly alert body in en-GB", async () => {
    await i18n.changeLanguage("en-GB");
    const result = i18n.t("alerts.gateAnomaly.body", {
      vin: "WBS3E2C08PAB00142",
      gate: "dim",
      actual: "102.4",
      low: "100",
      high: "102",
    });
    expect(result).toContain("WBS3E2C08PAB00142");
    expect(result).toContain("dim");
    expect(result).toContain("102.4");
  });

  it("error messages are localised in de-DE (AC-edd6ce81)", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.t("errors.unauthorized")).toBe(
      "Nicht autorisiert. Bitte erneut anmelden.",
    );
    expect(i18n.t("errors.networkError")).toBe(
      "Netzwerkfehler. Verbindung prüfen.",
    );
  });

  it("error messages are localised in en-GB", async () => {
    await i18n.changeLanguage("en-GB");
    expect(i18n.t("errors.unauthorized")).toBe(
      "Unauthorised. Please log in again.",
    );
    expect(i18n.t("errors.networkError")).toBe(
      "Network error. Please check your connection.",
    );
  });
});

describe("translation bundle completeness (AC-1a65ae08)", () => {
  const TOP_LEVEL_KEYS = [
    "app",
    "nav",
    "topbar",
    "settings",
    "language",
    "arWorkbench",
    "defectDashboard",
    "processReplay",
    "gateCommand",
    "certificateStudio",
    "alerts",
    "errors",
  ];

  it("de-DE bundle has all required top-level namespaces", () => {
    for (const key of TOP_LEVEL_KEYS) {
      expect(
        i18n.exists(key, { lng: "de-DE" }),
        `de-DE is missing top-level key "${key}"`,
      ).toBe(true);
    }
  });

  it("en-GB bundle has all required top-level namespaces", () => {
    for (const key of TOP_LEVEL_KEYS) {
      expect(
        i18n.exists(key, { lng: "en-GB" }),
        `en-GB is missing top-level key "${key}"`,
      ).toBe(true);
    }
  });

  it("de-DE and en-GB both have alerts.defectCluster.title (gate key for dynamic alerts)", () => {
    expect(i18n.exists("alerts.defectCluster.title", { lng: "de-DE" })).toBe(
      true,
    );
    expect(i18n.exists("alerts.defectCluster.title", { lng: "en-GB" })).toBe(
      true,
    );
  });

  it("de-DE and en-GB both have alerts.gateAnomaly.title", () => {
    expect(i18n.exists("alerts.gateAnomaly.title", { lng: "de-DE" })).toBe(
      true,
    );
    expect(i18n.exists("alerts.gateAnomaly.title", { lng: "en-GB" })).toBe(
      true,
    );
  });
});
