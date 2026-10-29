import { test, expect } from "@playwright/test";
import { login } from "./fixtures";

// US-009: Journeys for Shift Handover Builder
// Note: AC-2 (incoming Shift Lead sees OEE delta in Command Center) and AC-3 (Plant Manager daily production review)
// are partially covered below; full assertion of OEE numeric values is not possible without seeded shift data,
// so we assert on structural elements and empty/default states as documented.

test("US-009: Shift Lead opens Handover Builder and sees pre-populated predictive Andon alert line items", async ({
  page,
}) => {
  await login(page, "cardholder");

  // Navigate to Handover Builder
  await page.getByRole("link", { name: /handover/i }).click();
  await expect(page.getByRole("heading", { name: /handover builder/i })).toBeVisible();

  // The form should be visible with a section for open predictive alerts
  await expect(
    page.getByRole("region", { name: /predictive alerts|open alerts|andon alerts/i })
  ).toBeVisible();

  // Each alert line item should have a disposition selector
  // If there are no open alerts, the section should display an empty state message
  const alertSection = page.getByRole("region", { name: /predictive alerts|open alerts|andon alerts/i });
  const alertItems = alertSection.getByRole("listitem");
  const alertCount = await alertItems.count();

  if (alertCount > 0) {
    // Each alert row should have a disposition control (resolved / escalated / monitoring)
    const firstAlertItem = alertItems.first();
    await expect(
      firstAlertItem.getByRole("combobox", { name: /disposition/i })
        .or(firstAlertItem.getByRole("radiogroup", { name: /disposition/i }))
    ).toBeVisible();

    // Disposition options should include the required values
    const dispositionSelect = firstAlertItem.getByRole("combobox", { name: /disposition/i });
    if (await dispositionSelect.isVisible()) {
      await dispositionSelect.click();
      await expect(page.getByRole("option", { name: /resolved/i })).toBeVisible();
      await expect(page.getByRole("option", { name: /escalated/i })).toBeVisible();
      await expect(page.getByRole("option", { name: /monitoring/i })).toBeVisible();
    }
  } else {
    // Empty state is acceptable when no alerts are open
    await expect(alertSection.getByText(/no open alerts|no predictive alerts/i)).toBeVisible();
  }

  // The handover form should also show a note field per alert (or a general notes field)
  await expect(
    page.getByRole("textbox", { name: /notes|disposition note/i }).first()
      .or(page.getByLabel(/notes|disposition note/i).first())
  ).toBeVisible();
});

test("US-009: Shift Lead completes and submits the handover form successfully", async ({
  page,
}) => {
  await login(page, "cardholder");

  // Navigate to Handover Builder
  await page.getByRole("link", { name: /handover/i }).click();
  await expect(page.getByRole("heading", { name: /handover builder/i })).toBeVisible();

  // Fill in required fields
  // Shift end notes / summary
  const summaryField = page
    .getByRole("textbox", { name: /summary|shift notes|handover notes/i })
    .or(page.getByLabel(/summary|shift notes|handover notes/i));
  await summaryField.first().fill("End of shift handover — automated e2e test submission.");

  // If there are open alert items, set a disposition for each
  const alertSection = page.getByRole("region", { name: /predictive alerts|open alerts|andon alerts/i });
  const alertItems = alertSection.getByRole("listitem");
  const alertCount = await alertItems.count();

  for (let i = 0; i < alertCount; i++) {
    const item = alertItems.nth(i);
    const dispositionSelect = item.getByRole("combobox", { name: /disposition/i });
    if (await dispositionSelect.isVisible()) {
      await dispositionSelect.selectOption({ label: /monitoring/i });
    }
    const noteField = item.getByRole("textbox", { name: /note/i });
    if (await noteField.isVisible()) {
      await noteField.fill("Monitoring — no immediate action required.");
    }
  }

  // Submit the handover
  await page.getByRole("button", { name: /submit handover|submit/i }).click();

  // Expect a success confirmation
  await expect(
    page
      .getByRole("alert", { name: /success|submitted/i })
      .or(page.getByText(/handover submitted|handover saved|submission successful/i))
  ).toBeVisible();
});

test("US-009: Incoming Shift Lead opens Command Center and sees submitted handover summary with OEE delta and alert dispositions", async ({
  page,
}) => {
  // The incoming shift lead reviews the handover in Command Center
  await login(page, "approver");

  // Navigate to Command Center
  await page.getByRole("link", { name: /command center/i }).click();
  await expect(page.getByRole("heading", { name: /command center/i })).toBeVisible();

  // The latest handover summary section should be visible
  await expect(
    page
      .getByRole("region", { name: /handover summary|latest handover|shift handover/i })
      .or(page.getByText(/handover summary|latest handover|shift handover/i))
  ).toBeVisible();

  // The summary should contain OEE delta information
  await expect(
    page.getByText(/oee delta|oee vs\. 30-day|oee vs 30.day/i)
  ).toBeVisible();

  // Alert dispositions section should be present
  await expect(
    page.getByText(/alert dispositions|open alerts|andon alerts/i)
  ).toBeVisible();

  // VINs flagged for quality review section should be present (may be empty)
  await expect(
    page
      .getByText(/vins? (held|flagged) for quality|quality review/i)
      .or(page.getByRole("region", { name: /quality review/i }))
  ).toBeVisible();
});

test("US-009: Plant Manager views daily production review and finds structured handover log entry", async ({
  page,
}) => {
  // AC-3: Plant Manager can see handover as a structured log entry
  await login(page, "program_admin");

  // Navigate to daily production review / reports
  await page
    .getByRole("link", { name: /production review|daily review|reports/i })
    .click();
  await expect(
    page.getByRole("heading", { name: /production review|daily review|report/i })
  ).toBeVisible();

  // Handover records should be listed
  await expect(
    page
      .getByRole("region", { name: /handover records|handover log|shift handovers/i })
      .or(page.getByText(/handover records|handover log|shift handovers/i))
  ).toBeVisible();

  // Find the most recent handover entry
  const handoverRows = page
    .getByRole("table", { name: /handover/i })
    .getByRole("row")
    .or(page.getByRole("listitem").filter({ hasText: /shift lead|handover/i }));

  const rowCount = await handoverRows.count();

  if (rowCount > 0) {
    const firstRow = handoverRows.first();

    // Each entry should show the submitting Shift Lead ID
    await expect(firstRow.getByText(/shift lead|submitted by/i)).toBeVisible();

    // Each entry should show shift start/end timestamps
    await expect(firstRow.getByText(/shift start|start time/i)).toBeVisible();
    await expect(firstRow.getByText(/shift end|end time/i)).toBeVisible();

    // Each entry should show linked alert IDs or indicate no alerts
    await expect(
      firstRow
        .getByText(/alert id|linked alerts|no alerts/i)
        .or(firstRow.getByRole("link", { name: /view alerts|alerts/i }))
    ).toBeVisible();
  } else {
    // Acceptable empty state — no handovers submitted yet in this seed
    await expect(
      page.getByText(/no handover records|no records found|no handovers/i)
    ).toBeVisible();
  }
});

test("US-009: Shift Lead can navigate from Handover Builder to view handover history list", async ({
  page,
}) => {
  // Validates that GET /handovers is surfaced in the UI
  await login(page, "cardholder");

  // Navigate to Handover Builder
  await page.getByRole("link", { name: /handover/i }).click();
  await expect(page.getByRole("heading", { name: /handover builder/i })).toBeVisible();

  // There should be a way to view past/submitted handovers
  const historyLink = page
    .getByRole("link", { name: /history|past handovers|view handovers/i })
    .or(page.getByRole("tab", { name: /history|past handovers/i }));
  await historyLink.click();

  // The handover list page or tab content should be visible
  await expect(
    page
      .getByRole("heading", { name: /handover history|past handovers|submitted handovers/i })
      .or(page.getByText(/handover history|past handovers|submitted handovers/i))
  ).toBeVisible();

  // The list should either have rows or an empty state
  const handoverList = page
    .getByRole("table")
    .or(page.getByRole("list", { name: /handovers/i }));

  const emptyState = page.getByText(/no handovers|no records|nothing to show/i);

  await expect(handoverList.or(emptyState)).toBeVisible();
});
