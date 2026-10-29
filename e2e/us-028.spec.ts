import { test, expect } from "@playwright/test";
import { login } from "./fixtures";

// US-028 covers data residency, GDPR Article 30 inventory, and Article 17 deletion rights.
// Since seeded data is empty and the acceptance criteria are largely backend/infrastructure
// concerns, we test what IS visible in the browser:
//   AC1: Data residency — no browser-testable assertion (infrastructure-level concern); skipped.
//   AC2: GDPR Article 30 record of processing activities — test that the DPO/admin can
//        navigate to the data processing inventory and see the relevant structure/sections.
//   AC3: Article 17 deletion/pseudonymisation request — test that a worker (cardholder)
//        can submit a deletion request and see confirmation, and that a program_admin can
//        see the request in the queue.

// AC1: Cannot be verified in the browser — it requires infrastructure-level inspection
// (e.g. Azure region tags, network egress logs). Skipped with a comment.

test("US-028: program_admin can navigate to the GDPR data processing inventory page", async ({
  page,
}) => {
  await login(page, "program_admin");

  // Navigate to the data / compliance section via the main navigation
  await page.getByRole("link", { name: /compliance|data protection|gdpr/i }).click();

  // Expect a heading related to GDPR / data processing inventory
  await expect(
    page.getByRole("heading", { name: /data processing|article 30|processing activities/i })
  ).toBeVisible();
});

test("US-028: GDPR Article 30 record lists required sections (categories, purposes, retention, legal basis)", async ({
  page,
}) => {
  await login(page, "program_admin");

  // Navigate to compliance / GDPR section
  await page.getByRole("link", { name: /compliance|data protection|gdpr/i }).click();

  // Open or locate the Article 30 record of processing activities
  const inventoryLink = page.getByRole("link", {
    name: /article 30|record of processing|processing inventory/i,
  });
  await expect(inventoryLink).toBeVisible();
  await inventoryLink.click();

  // The record must contain the four required columns / sections per GDPR Art. 30
  await expect(page.getByText(/data categor/i)).toBeVisible();
  await expect(page.getByText(/purpose/i)).toBeVisible();
  await expect(page.getByText(/retention/i)).toBeVisible();
  await expect(page.getByText(/legal basis/i)).toBeVisible();
});

test("US-028: cardholder (worker) can submit a GDPR Article 17 personal data deletion request", async ({
  page,
}) => {
  await login(page, "cardholder");

  // Navigate to privacy / personal data settings
  await page.getByRole("link", { name: /privacy|personal data|my data|data rights/i }).click();

  // Find and click the "Request deletion" or "Right to erasure" option
  const deleteButton = page.getByRole("button", {
    name: /request deletion|right to erasure|delete my data/i,
  });
  await expect(deleteButton).toBeVisible();
  await deleteButton.click();

  // A confirmation dialog or form should appear
  const confirmButton = page.getByRole("button", { name: /confirm|submit|send request/i });
  await expect(confirmButton).toBeVisible();
  await confirmButton.click();

  // The worker should see a success / acknowledgement message
  await expect(
    page.getByText(/request received|deletion request submitted|we will process/i)
  ).toBeVisible();
});

test("US-028: program_admin can view pending GDPR deletion requests in the compliance queue", async ({
  page,
}) => {
  await login(page, "program_admin");

  // Navigate to compliance / data requests section
  await page.getByRole("link", { name: /compliance|data protection|gdpr/i }).click();

  const requestsLink = page.getByRole("link", {
    name: /deletion requests|data subject requests|erasure requests/i,
  });
  await expect(requestsLink).toBeVisible();
  await requestsLink.click();

  // The queue page should have a heading and display a table or list (even if empty)
  await expect(
    page.getByRole("heading", { name: /deletion requests|data subject requests/i })
  ).toBeVisible();

  // The page must show either a list/table of requests or an explicit empty-state message
  const hasTable = await page.getByRole("table").isVisible().catch(() => false);
  const hasEmptyState = await page
    .getByText(/no requests|no pending|empty/i)
    .isVisible()
    .catch(() => false);

  expect(hasTable || hasEmptyState).toBe(true);
});

test("US-028: finance persona cannot access GDPR deletion request administration (access control)", async ({
  page,
}) => {
  await login(page, "finance");

  // Finance users should not have access to the GDPR admin queue
  // Navigate directly to the compliance area
  await page.goto("/compliance/deletion-requests");

  // Expect either an access-denied message or a redirect away from the page
  const accessDenied = page.getByText(/access denied|forbidden|not authorised|not authorized|403/i);
  const redirectedHome = page.getByRole("heading", { name: /dashboard|home/i });

  // At least one of these must be visible
  await Promise.race([
    expect(accessDenied).toBeVisible(),
    expect(redirectedHome).toBeVisible(),
  ]);
});
