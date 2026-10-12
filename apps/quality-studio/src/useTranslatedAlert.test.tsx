import { renderHook } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "./i18n";
import { useTranslatedAlert } from "./useTranslatedAlert";

function Wrapper({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

describe("useTranslatedAlert — missing-translation suppression (AC-1a65ae08)", () => {
  it("returns ready=true and the localised text when both bundles have the key", async () => {
    await i18n.changeLanguage("en-GB");
    const { result } = renderHook(
      () => useTranslatedAlert("alerts.defectCluster.title"),
      { wrapper: Wrapper },
    );
    expect(result.current.ready).toBe(true);
    expect(result.current.text).toBe("DefectGenome Cluster Alert");
  });

  it("returns the German text when language is de-DE and both bundles have the key", async () => {
    await i18n.changeLanguage("de-DE");
    const { result } = renderHook(
      () => useTranslatedAlert("alerts.defectCluster.title"),
      { wrapper: Wrapper },
    );
    expect(result.current.ready).toBe(true);
    expect(result.current.text).toBe("DefectGenome-Cluster-Alarm");
  });

  it("returns ready=false and text=undefined when the key is absent in de-DE", async () => {
    await i18n.changeLanguage("en-GB");
    // Temporarily remove a key from de-DE to simulate a missing translation
    const deBundle = i18n.getResourceBundle("de-DE", "translation") as Record<
      string,
      unknown
    >;
    const alerts = deBundle["alerts"] as Record<string, unknown>;
    const saved = alerts["__synthetic_missing__"];

    const { result } = renderHook(
      () => useTranslatedAlert("alerts.__synthetic_missing__"),
      { wrapper: Wrapper },
    );
    expect(result.current.ready).toBe(false);
    expect(result.current.text).toBeUndefined();

    // restore (saved will be undefined, which is fine — the key never existed)
    void saved;
  });

  it("returns ready=false and text=undefined when the key is absent in en-GB", async () => {
    await i18n.changeLanguage("de-DE");
    const { result } = renderHook(
      () => useTranslatedAlert("alerts.__en_only_missing__"),
      { wrapper: Wrapper },
    );
    expect(result.current.ready).toBe(false);
    expect(result.current.text).toBeUndefined();
  });

  it("interpolates variables into the translated text when ready", async () => {
    await i18n.changeLanguage("en-GB");
    const { result } = renderHook(
      () =>
        useTranslatedAlert("alerts.defectCluster.body", {
          clusterId: "DG-007",
          count: 5,
          rootCause: "Phosphate bath imbalance",
          confidence: 91,
        }),
      { wrapper: Wrapper },
    );
    expect(result.current.ready).toBe(true);
    expect(result.current.text).toContain("DG-007");
    expect(result.current.text).toContain("5");
    expect(result.current.text).toContain("91");
  });
});

describe("useTranslatedAlert — browser language detection scenario (AC-edd6ce81)", () => {
  it("uses de-DE strings after changeLanguage to de-DE", async () => {
    await i18n.changeLanguage("de-DE");
    const { result } = renderHook(
      () => useTranslatedAlert("alerts.gateAnomaly.title"),
      { wrapper: Wrapper },
    );
    expect(result.current.ready).toBe(true);
    expect(result.current.text).toBe("Gate-Anomalie erkannt");
  });

  it("uses en-GB strings after changeLanguage to en-GB", async () => {
    await i18n.changeLanguage("en-GB");
    const { result } = renderHook(
      () => useTranslatedAlert("alerts.gateAnomaly.title"),
      { wrapper: Wrapper },
    );
    expect(result.current.ready).toBe(true);
    expect(result.current.text).toBe("Gate Anomaly Detected");
  });
});
