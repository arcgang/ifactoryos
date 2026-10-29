import { test, expect } from "@playwright/test";
import { login } from "./fixtures";

// NOTE: No seeded data is available for US-015, so journeys assert on
// structural elements, empty states, and alert-flow UI rather than
// specific IDs or VIN values. AC-1's "within 5 minutes" real-time push
// cannot be fully verified in a browser smoke test without triggering
// the backend pipeline; we navigate to the alerts panel and assert the
// UI is ready to receive alerts.

test("US-015: AC-1 – Quality Studio alerts panel is visible and ready to receive cluster alerts", async ({
  page,
}) => {
  await login(page, "cardholder"); // Paint Process Engineer persona maps to cardholder role

  // Navigate to Quality Studio / Alerts section
  await page.getByRole("link", { name: /quality studio/i }).click();
  await expect(page.getByRole("heading", { name: /quality studio/i })).toBeVisible();

  // Locate the alerts panel or tab
  await page.getByRole("tab", { name: /alerts/i }).click();

  // The panel heading should confirm we are on the Alerts view
  await expect(
    page.getByRole("heading", { name: /alerts/i })
  ).toBeVisible();

  // Either a "no alerts" empty-state message or a list of alert rows is shown
  const emptyState = page.getByText(/no alerts/i);
  const alertList = page.getByRole("list", { name: /alerts/i });

  // At least one of these must be visible – the panel has loaded
  await expect(emptyState.or(alertList)).toBeVisible();
});

test("US-015: AC-2 – Opening a cluster alert shows required detail fields", async ({
  page,
}) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /quality studio/i }).click();
  await page.getByRole("tab", { name: /alerts/i }).click();

  // If there is at least one alert row, open it; otherwise verify the
  // empty-state message (structural smoke – no seeded alert available).
  const firstAlert = page.getByRole("listitem").first();
  const emptyState = page.getByText(/no alerts/i);

  const hasAlerts = await firstAlert.isVisible().catch(() => false);

  if (hasAlerts) {
    await firstAlert.click();

    // Detail drawer / modal must contain each required field
    await expect(page.getByText(/cluster id/i)).toBeVisible();
    await expect(page.getByText(/affected vins/i)).toBeVisible();
    await expect(page.getByText(/root.?cause signature/i)).toBeVisible();
    await expect(page.getByText(/confidence/i)).toBeVisible();

    // Must contain a link to Process Replay
    await expect(
      page.getByRole("link", { name: /process replay/i })
    ).toBeVisible();
  } else {
    // No live alerts in seeded data; confirm empty state renders correctly
    await expect(emptyState).toBeVisible();
  }
});

test("US-015: AC-3 – Acknowledging a cluster alert and assigning a corrective action updates status to In Progress", async ({
  page,
}) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /quality studio/i }).click();
  await page.getByRole("tab", { name: /alerts/i }).click();

  const firstAlert = page.getByRole("listitem").first();
  const emptyState = page.getByText(/no alerts/i);

  const hasAlerts = await firstAlert.isVisible().catch(() => false);

  if (hasAlerts) {
    await firstAlert.click();

    // Acknowledge the alert
    await page.getByRole("button", { name: /acknowledge/i }).click();

    // A corrective-action form / input should appear after acknowledgement
    const jiraField = page.getByLabel(/jira ticket/i).or(
      page.getByPlaceholder(/jira/i)
    );
    await expect(jiraField).toBeVisible();

    // Enter a Jira ticket ID (format only – no seeded ticket required)
    await jiraField.fill("QA-9001");

    await page.getByRole("button", { name: /assign corrective action/i }).click();

    // Status badge must update to In Progress
    await expect(
      page.getByRole("status").or(page.getByText(/in progress/i))
    ).toBeVisible();

    // Navigate to Defect Dashboard to verify cluster flag
    await page.getByRole("link", { name: /defect dashboard/i }).click();
    await expect(
      page.getByRole("heading", { name: /defect dashboard/i })
    ).toBeVisible();

    // The cluster should appear as flagged with a corrective action
    await expect(
      page.getByText(/QA-9001/).or(page.getByText(/in progress/i))
    ).toBeVisible();
  } else {
    // No live alerts; assert Defect Dashboard loads and is ready
    await page.getByRole("link", { name: /defect dashboard/i }).click();
    await expect(
      page.getByRole("heading", { name: /defect dashboard/i })
    ).toBeVisible();

    // Either clusters are listed or an empty state is shown
    const clusterEmptyState = page.getByText(/no clusters/i);
    const clusterList = page.getByRole("table").or(
      page.getByRole("list", { name: /clusters/i })
    );
    await expect(clusterEmptyState.or(clusterList)).toBeVisible();
  }
});

test("US-015: Defect Dashboard cluster list renders cluster ID and confidence score columns", async ({
  page,
}) => {
  await login(page, "cardholder");

  await page.getByRole("link", { name: /defect dashboard/i }).click();
  await expect(
    page.getByRole("heading", { name: /defect dashboard/i })
  ).toBeVisible();

  // The table (if present) should have cluster-specific columns
  const table = page.getByRole("table");
  const emptyState = page.getByText(/no clusters/i);

  const hasTable = await table.isVisible().catch(() => false);

  if (hasTable) {
    await expect(table.getByRole("columnheader", { name: /cluster id/i })).toBeVisible();
    await expect(
      table.getByRole("columnheader", { name: /confidence/i })
    ).toBeVisible();
  } else {
    await expect(emptyState).toBeVisible();
  }
});

test("US-015: Alert detail page is reachable via direct navigation and displays expected layout", async ({
  page,
}) => {
  await login(page, "cardholder");

  // Directly exercise the GET /alerts endpoint surface via the UI
  await page.goto("/quality-studio/alerts");

  await expect(
    page.getByRole("heading", { name: /alerts/i })
  ).toBeVisible();

  // Page must not show a generic error
  await expect(page.getByText(/500|internal server error/i)).not.toBeVisible();
  await expect(page.getByText(/404|not found/i)).not.toBeVisible();

  // Filtering / search controls should be present
  const filterControl = page
    .getByRole("searchbox")
    .or(page.getByLabel(/filter/i))
    .or(page.getByRole("combobox", { name: /status/i }));

  await expect(filterControl).toBeVisible();
});
