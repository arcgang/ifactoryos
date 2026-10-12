import { renderHook } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import { createElement } from "react";
import type { ReactNode } from "react";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { useBilingualContent } from "./useBilingualContent";

const BOTH = {
  "de-DE": "Einstellungen",
  "en-GB": "Settings",
} as const;

const MISSING_EN = {
  "de-DE": "Nur Deutsch",
  "en-GB": "",
} as const;

const MISSING_DE = {
  "de-DE": "",
  "en-GB": "English only",
} as const;

function wrapper({ children }: { children: ReactNode }) {
  return createElement(I18nextProvider, { i18n }, children);
}

beforeEach(async () => {
  await i18n.changeLanguage("en-GB");
});

describe("useBilingualContent — AC-3 translation gate", () => {
  it("returns the en-GB string when active locale is en-GB and both translations exist", () => {
    const { result } = renderHook(() => useBilingualContent(BOTH), { wrapper });
    expect(result.current).toBe("Settings");
  });

  it("returns the de-DE string when active locale is de-DE and both translations exist", async () => {
    await i18n.changeLanguage("de-DE");
    const { result } = renderHook(() => useBilingualContent(BOTH), { wrapper });
    expect(result.current).toBe("Einstellungen");
  });

  it("returns null when en-GB translation is absent (AC-3: blocks display)", () => {
    const { result } = renderHook(() => useBilingualContent(MISSING_EN), { wrapper });
    expect(result.current).toBeNull();
  });

  it("returns null when de-DE translation is absent even when locale is de-DE (AC-3: blocks display)", async () => {
    await i18n.changeLanguage("de-DE");
    const { result } = renderHook(() => useBilingualContent(MISSING_DE), { wrapper });
    expect(result.current).toBeNull();
  });

  it("returns null when strings argument is null", () => {
    const { result } = renderHook(() => useBilingualContent(null), { wrapper });
    expect(result.current).toBeNull();
  });

  it("returns null when strings argument is undefined", () => {
    const { result } = renderHook(() => useBilingualContent(undefined), { wrapper });
    expect(result.current).toBeNull();
  });
});
