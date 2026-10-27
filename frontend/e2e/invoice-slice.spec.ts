import { expect, test, type Page } from '@playwright/test'

function collectConsoleErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))
  return errors
}

test('invoice processing slice: live state, chaos, decision, regression, recovery', async ({
  page,
}) => {
  const consoleErrors = collectConsoleErrors(page)

  await test.step('slice page is built from live backend state', async () => {
    await page.goto('/slice')
    await expect(
      page.getByRole('heading', { name: 'Invoice processing slice' }),
    ).toBeVisible()
    await expect(page.getByText('Invoice Processing').first()).toBeVisible()
    await expect(page.getByText('1/4 scenarios run')).toBeVisible()

    const scenarioList = page.locator('.card', { hasText: 'Required chaos scenarios' }).first()
    await expect(scenarioList.getByText('PO API timeout')).toBeVisible()
    await expect(scenarioList.getByText('Failed')).toBeVisible()
    await expect(scenarioList.locator('.scenario-checklist').getByText('Not run yet')).toHaveCount(3)

    await expect(page.getByText('1 open')).toBeVisible()
    await expect(page.getByText('No recovery attempt')).toBeVisible()
  })

  await test.step('chaos lab runs the invoice-total mismatch', async () => {
    await page.goto('/chaos')
    await expect(page.getByRole('heading', { name: 'Chaos Lab' })).toBeVisible()

    const mismatch = page.locator('.scenario-card', { hasText: 'Invoice total mismatch' })
    await mismatch.getByRole('button', { name: 'Evaluate scenario' }).click()

    await expect(page.getByText(/Chaos report · Invoice total mismatch/)).toBeVisible()
    await expect(page.getByText(/Ordered fault-run events/)).toBeVisible()
    await expect(page.getByText(/decision/i).first()).toBeVisible()
  })

  await test.step('decision inbox asks only the discrepancy', async () => {
    await page.goto('/decisions')
    await expect(page.getByRole('heading', { name: 'Decision Inbox' })).toBeVisible()
    await page.locator('.decision-item', { hasText: 'variance' }).first().click()

    await expect(page.getByText('Invoice total (cents)')).toBeVisible()
    await expect(page.getByText('Purchase order total (cents)')).toBeVisible()
    await expect(page.getByText('Fail', { exact: true }).first()).toBeVisible()
    await expect(page.locator('.badge', { hasText: 'Conflicting evidence' }).first()).toBeVisible()
  })

  await test.step('finance approval resumes the run to a verified state', async () => {
    await page.selectOption('label.role-picker select', 'FINANCE_APPROVER')
    await page.getByRole('button', { name: 'Approve', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('button', { name: 'Re-check & resume' }).click()

    await expect(page.getByText(/Authorized by FINANCE_APPROVER/)).toBeVisible()
    const runLink = page.locator('.inbox-layout .kv a[href^="/runs/"]').first()
    await runLink.click()
    await expect(page.getByText('VERIFIED').first()).toBeVisible()
    await expect(page.getByText('SUCCEEDED').first()).toBeVisible()
  })

  await test.step('the PO-timeout failure becomes a regression test and recovers', async () => {
    await page.goto('/slice')

    const poRun = page
      .locator('.attempt-list li', { hasText: 'TIMEOUT_BEFORE_EXECUTION' })
      .first()
    await poRun.locator('a[href^="/runs/"]').click()

    await expect(page.getByText('TIMEOUT_BEFORE_EXECUTION').first()).toBeVisible()
    await page.getByRole('button', { name: 'Create regression' }).first().click()
    await expect(page.getByText(/Regression test .* created from this run/)).toBeVisible()

    await page.getByRole('button', { name: 'Recover', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Run recovery' }).click()
    await expect(page.getByText(/Recovery .* restored a checkpoint and the run verified/)).toBeVisible()
    await expect(page.getByText('VERIFIED').first()).toBeVisible()
  })

  await test.step('the saved regression test replays', async () => {
    await page.goto('/recovery')
    await expect(page.getByRole('heading', { name: 'Recovery & Audit' })).toBeVisible()

    const newest = page.locator('.regression-card').first()
    await expect(newest.getByText(/TIMEOUT_BEFORE_EXECUTION/).first()).toBeVisible()
    await newest.getByRole('button', { name: 'Replay' }).click()
    await expect(page.getByText(/Pinned replay finished/)).toBeVisible()
  })

  await test.step('no console errors were emitted', async () => {
    expect(consoleErrors).toEqual([])
  })
})