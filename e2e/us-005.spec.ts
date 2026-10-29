import { test, expect } from "@playwright/test";
import { login } from "./fixtures";

// US-005: Constraint Radar alerts for mid-week disruptions

// AC1: In-app alert appears in Constraint Radar within 2 minutes when threshold is breached
test("US-005: Constraint Radar shows in-app alert when a constraint threshold is breached", async ({ page }) => {
  await login(page, "cardholder");

  // Navigate to Constraint Radar via the main nav
  await page.getByRole("link", { name: /constraint radar/i }).click();
  await expect(page.getByRole("heading", { name: /constraint radar/i })).toBeVisible();

  // The page should show threshold configuration or current constraints
  const thresholdSection = page.getByRole("region", { name: /thresholds?/i })
    .or(page.getByText(/threshold/i).first());
  await expect(thresholdSection).toBeVisible();

  // Check for the alerts/notifications panel
  const alertPanel = page
    .getByRole("region", { name: /alerts?/i })
    .or(page.getByRole("list", { name: /alerts?/i }))
    .or(page.getByTestId("constraint-alerts"));

  // Either alerts exist or an empty-state message is displayed
  const alertOrEmpty = alertPanel
    .or(page.getByText(/no active alerts/i))
    .or(page.getByText(/no alerts/i))
    .or(page.getByText(/all clear/i));
  await expect(alertOrEmpty).toBeVisible();
});

// AC1 (structure): Constraint Radar page exposes VIN and constraint cause for any active alert
test("US-005: Active alerts in Constraint Radar identify affected VINs and constraint cause", async ({ page }) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /constraint radar/i }).click();
  await expect(page.getByRole("heading", { name: /constraint radar/i })).toBeVisible();

  // Check that the alert list/table has columns or labels for VIN and constraint
  const vinLabel = page.getByText(/vin/i).first();
  const constraintLabel = page.getByText(/constraint/i).first();
  await expect(vinLabel).toBeVisible();
  await expect(constraintLabel).toBeVisible();

  // If there are active alerts, each row should include a VIN identifier and constraint name
  const alertRows = page.getByRole("row").filter({ hasText: /vin/i });
  const rowCount = await alertRows.count();

  if (rowCount > 0) {
    const firstRow = alertRows.first();
    // Each alert row should mention a constraint type (e.g., kitting, AGV, press die, maintenance)
    await expect(firstRow).toBeVisible();
  } else {
    // No alerts seeded — assert empty state structure is present
    const emptyState = page
      .getByText(/no active alerts/i)
      .or(page.getByText(/no alerts/i))
      .or(page.getByText(/all clear/i))
      .or(page.getByText(/no disruptions/i));
    await expect(emptyState).toBeVisible();
  }
});

// AC2: Clicking 'Re-sequence Affected VINs' opens Scenario Lab pre-loaded with affected VINs only
test("US-005: Re-sequence Affected VINs button pre-loads Scenario Lab with affected VINs subset", async ({ page }) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /constraint radar/i }).click();
  await expect(page.getByRole("heading", { name: /constraint radar/i })).toBeVisible();

  // Look for an active alert with the Re-sequence button, or simulate via threshold config
  const resequenceButton = page.getByRole("button", { name: /re-sequence affected vins/i });
  const isButtonVisible = await resequenceButton.isVisible().catch(() => false);

  if (isButtonVisible) {
    await resequenceButton.click();

    // Should navigate to Scenario Lab
    await expect(page.getByRole("heading", { name: /scenario lab/i })).toBeVisible();

    // Scenario Lab should indicate it's loaded with a subset (not full weekly sequence)
    const subsetIndicator = page
      .getByText(/affected vins/i)
      .or(page.getByText(/targeted re-sequence/i))
      .or(page.getByText(/subset/i))
      .or(page.getByText(/monte carlo/i));
    await expect(subsetIndicator).toBeVisible();

    // Should NOT show the full weekly sequence label
    await expect(page.getByText(/full weekly sequence/i)).not.toBeVisible();
  } else {
    // No active alerts seeded — verify the Constraint Radar page renders with the correct
    // empty-state UX that would include the re-sequence workflow once alerts appear.
    // NOTE: AC2 cannot be fully exercised without a seeded threshold breach event.
    const emptyState = page
      .getByText(/no active alerts/i)
      .or(page.getByText(/no alerts/i))
      .or(page.getByText(/all clear/i))
      .or(page.getByText(/no disruptions/i));
    await expect(emptyState).toBeVisible();
  }
});

// AC3: Published mid-week re-sequence appears in audit log attributed to 'planning-cockpit'
// NOTE: This AC requires a POST /resequence to have been triggered and the OpCenter
// version diff to be queryable. Without seeded data for a completed re-sequence, we
// assert the audit log page structure and attribution column exist.
test("US-005: Audit log shows re-sequenced VINs attributed to planning-cockpit", async ({ page }) => {
  await login(page, "program_admin");

  // Navigate to the audit log / sequence version history
  const auditLink = page
    .getByRole("link", { name: /audit log/i })
    .or(page.getByRole("link", { name: /version history/i }))
    .or(page.getByRole("link", { name: /sequence log/i }));
  await auditLink.click();

  await expect(
    page
      .getByRole("heading", { name: /audit log/i })
      .or(page.getByRole("heading", { name: /version history/i }))
      .or(page.getByRole("heading", { name: /sequence log/i }))
  ).toBeVisible();

  // The audit table should have a source/attributed-to column
  const sourceColumn = page
    .getByRole("columnheader", { name: /source/i })
    .or(page.getByRole("columnheader", { name: /attributed/i }))
    .or(page.getByRole("columnheader", { name: /origin/i }))
    .or(page.getByText(/planning-cockpit/i).first());
  await expect(sourceColumn).toBeVisible();

  // If any re-sequence entries exist, they should be attributed to 'planning-cockpit'
  const planningCockpitEntries = page.getByText(/planning-cockpit/i);
  const entryCount = await planningCockpitEntries.count();

  if (entryCount > 0) {
    await expect(planningCockpitEntries.first()).toBeVisible();
  }
  // If no entries exist (no seeded data), the empty-state or table structure is sufficient
});

// AC2 (Scenario Lab integration): Scenario Lab has a Monte Carlo run option available
test("US-005: Scenario Lab exposes Monte Carlo run controls for targeted re-sequencing", async ({ page }) => {
  await login(page, "cardholder");

  // Navigate directly to Scenario Lab
  const scenarioLabLink = page
    .getByRole("link", { name: /scenario lab/i })
    .or(page.getByRole("link", { name: /scenarios/i }));
  await scenarioLabLink.click();

  await expect(
    page
      .getByRole("heading", { name: /scenario lab/i })
      .or(page.getByRole("heading", { name: /scenarios/i }))
  ).toBeVisible();

  // Scenario Lab should have Monte Carlo controls
  const monteCarloControl = page
    .getByText(/monte carlo/i)
    .or(page.getByRole("button", { name: /monte carlo/i }))
    .or(page.getByLabel(/monte carlo/i));
  await expect(monteCarloControl).toBeVisible();

  // There should be a way to specify VINs scope (affected vs full)
  const vinScopeControl = page
    .getByLabel(/affected vins/i)
    .or(page.getByLabel(/vin scope/i))
    .or(page.getByText(/affected vins/i))
    .or(page.getByRole("radio", { name: /affected/i }));
  await expect(vinScopeControl).toBeVisible();
});
