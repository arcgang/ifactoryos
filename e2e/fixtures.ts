import type { Page } from "@playwright/test";

/**
 * Sign in through the local mock OIDC server (docker-compose `oidc`, navikt/mock-oauth2-server).
 * Its login form takes any username and optional JSON claims; the persona becomes the subject and role.
 */
export async function login(page: Page, persona: "cardholder" | "program_admin" | "approver" | "finance"): Promise<void> {
  await page.goto("/");
  const user = page.getByRole("textbox", { name: /username|user/i }).first();
  if (await user.isVisible().catch(() => false)) {
    await user.fill(persona);
    const claims = page.getByRole("textbox", { name: /claims/i }).first();
    if (await claims.isVisible().catch(() => false)) await claims.fill(JSON.stringify({ roles: [persona], groups: [persona] }));
    await page.getByRole("button", { name: /sign in|login|submit/i }).first().click();
  }
  await page.waitForLoadState("networkidle");
}
