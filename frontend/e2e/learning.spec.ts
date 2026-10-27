import { expect, test, type Page } from '@playwright/test'

function collectConsoleErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))
  return errors
}

test('record, review, validate, save and replay a learned invoice workflow', async ({ page }) => {
  const consoleErrors = collectConsoleErrors(page)
  await page.goto('/learning')
  await expect(page.getByRole('heading', { name: 'Learning Studio' })).toBeVisible()

  await page.getByLabel('Recording label').fill('Invoice lookup and validation')
  await page.getByRole('button', { name: 'Start recording' }).click()
  await expect(page.getByRole('heading', { name: 'Controlled demo app' })).toBeVisible()

  const demo = page.frameLocator('iframe[title="Northwind invoice demo"]')
  await expect(demo.getByRole('heading', { name: 'Find an invoice to review' })).toBeVisible()

  await page.getByRole('button', { name: 'Pause' }).click()
  await expect(page.getByRole('button', { name: 'Resume' })).toBeVisible()
  await page.getByRole('button', { name: 'Resume' }).click()
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()
  await demo.getByLabel('Supplier ID').fill('SUP-77')
  await demo.getByLabel('Invoice ID').fill('INV-1001')
  await demo.getByRole('button', { name: 'Load invoice' }).click()
  await expect(demo.getByTestId('invoice-summary')).toHaveAttribute('data-invoice-id', 'INV-1001')
  await demo.getByRole('button', { name: 'Validate invoice' }).click()
  await expect(demo.getByTestId('validation-result')).toHaveAttribute('data-state', 'VALID')

  await expect(page.getByText('Validate invoice').first()).toBeVisible()
  await page.getByRole('button', { name: 'Stop and review' }).click()
  await expect(page.getByRole('heading', { name: 'Captured action graph' })).toBeVisible()
  await expect(page.getByText('Supplier ID').first()).toBeVisible()
  await expect(page.getByText('Load invoice').first()).toBeVisible()

  await page.getByRole('button', { name: 'Validate selectors' }).click()
  await expect(page.getByText(/recorded steps resolved to a unique element/)).toBeVisible({
    timeout: 30_000,
  })

  await page.getByLabel('Workflow name').fill('Learned invoice lookup')
  await page.getByRole('button', { name: 'Save workflow' }).click()
  await expect(page.getByRole('heading', { name: 'Saved workflow' })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('Learned invoice lookup').first()).toBeVisible()

  await page.getByLabel('Invoice Id').fill('INV-1003')
  await page.getByRole('button', { name: 'Execute replay' }).click()
  await expect(page.getByText('Replay verified', { exact: true })).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText('Succeeded').first()).toBeVisible()
  await expect(page.locator('.evidence-thumb').first()).toBeVisible()

  await page.getByLabel('Demo structure').selectOption('changed')
  await page.getByRole('button', { name: 'Execute replay' }).click()
  await expect(page.getByText('Replay failed safely', { exact: true })).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText('SELECTOR_NOT_FOUND').first()).toBeVisible()

  // The same published version is also runnable from Workflow Studio with
  // declared inputs; it uses the same playwright-v1 engine path.
  await page.getByRole('link', { name: 'Open saved workflow' }).click()
  await expect(
    page.getByRole('heading', { name: 'Learned invoice lookup', level: 1 }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Run workflow' }).click()
  const runDialog = page.getByRole('dialog')
  await expect(runDialog.getByText('playwright-v1')).toBeVisible()
  await runDialog.getByLabel('Supplier Id').fill('SUP-77')
  await runDialog.getByLabel('Invoice Id').fill('INV-1003')
  await runDialog.getByRole('button', { name: 'Start execution' }).click()
  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/, { timeout: 60_000 })
  await expect(page.getByText('Succeeded').first()).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText('Verified').first()).toBeVisible()

  expect(consoleErrors).toEqual([])
})
