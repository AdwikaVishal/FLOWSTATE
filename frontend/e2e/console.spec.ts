import { expect, test, type Page } from '@playwright/test'

function collectConsoleErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))
  return errors
}

test.describe('FLOWSTATE console against a real backend', () => {
  test('a second browser session follows a background execution over SSE', async ({ page, browser }) => {
    await page.goto('/workflows')
    await page.getByRole('link', { name: 'Open Invoice Approval' }).click()
    await page.getByRole('button', { name: 'Run workflow' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Start execution' }).click()
    await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
    const runId = page.url().split('/').pop() as string

    const observerContext = await browser.newContext()
    const observer = await observerContext.newPage()
    await observer.goto(`${new URL(page.url()).origin}/runs/${runId}`)
    await expect(observer.getByText('Live updates: connected')).toBeVisible()
    await expect(observer.getByText('Awaiting Decision').first()).toBeVisible()
    await observer.getByRole('tab', { name: /Nodes/ }).click()
    await expect(observer.getByText('Passed').first()).toBeVisible()
    await observerContext.close()
  })

  test('seeded workspace, execution, approval and every primary screen', async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page)

    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Operations overview' })).toBeVisible()
    await expect(page.getByText('Demonstration workspace')).toBeVisible()
    await expect(page.getByText('demo-invoice-approval-v1')).toBeVisible()
    await expect(page.getByText('Invoice Approval').first()).toBeVisible()

    await page.getByRole('link', { name: 'Workflow Studio' }).click()
    await expect(page).toHaveURL(/\/workflows$/)
    await expect(page.getByRole('heading', { name: 'Workflow Studio' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Open Invoice Approval' })).toBeVisible()

    await page.getByRole('link', { name: 'Open Invoice Approval' }).click()
    await expect(page.getByRole('heading', { name: 'Invoice Approval', level: 1 })).toBeVisible()
    const workflowUrl = page.url()
    await expect(page.getByText('Version history')).toBeVisible()
    await expect(page.getByText('Open the invoice queue')).toBeVisible()

    await page.getByRole('tab', { name: /Invariants/ }).click()
    await expect(page.getByText('Invoice reaches PAID')).toBeVisible()
    await page.getByRole('tab', { name: /Actions/ }).click()

    await page.getByRole('button', { name: 'Run workflow' }).click()
    const runDialog = page.getByRole('dialog')
    await expect(runDialog).toBeVisible()
    await expect(runDialog.getByText('simulated-v1')).toBeVisible()
    await runDialog.getByRole('button', { name: 'Start execution' }).click()

    await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
    const runId = page.url().split('/').pop() as string
    expect(runId).toMatch(/^[0-9a-f-]{36}$/)
    await expect(page.getByRole('heading', { name: /Execution/ })).toBeVisible()
    await expect(page.getByText('Awaiting Decision').first()).toBeVisible()
    await expect(page.getByText('This execution is paused at an authorization-gated action.')).toBeVisible()

    await page.getByRole('link', { name: 'Decision Inbox' }).click()
    await expect(page.getByRole('heading', { name: 'Decision Inbox' })).toBeVisible()
    await expect(page.locator(`a[href="/runs/${runId}"]`).first()).toBeVisible()

    await page.getByRole('button', { name: 'Approve', exact: true }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await page.getByRole('button', { name: 'Re-check & resume' }).click()
    await expect(page.getByText(/Authorized by/)).toBeVisible()

    await page.goto(`/runs/${runId}`)
    await expect(page.getByText('Succeeded').first()).toBeVisible()
    await expect(page.getByText('Verified').first()).toBeVisible()
    await page.getByRole('button', { name: 'Verify chain' }).click()
    await expect(page.getByText('Hash chain verified')).toBeVisible()

    // The graph timeline is connected: node states are queryable and rendered.
    await page.getByRole('tab', { name: /Nodes/ }).click()
    await expect(page.getByText('__start__').first()).toBeVisible()
    await expect(page.getByText('Passed').first()).toBeVisible()
    await page.getByRole('tab', { name: /Timeline/ }).click()
    await expect(page.getByText('Run verified')).toBeVisible()

    // A second run can be cancelled safely from the console.
    await page.goto(workflowUrl)
    await page.getByRole('button', { name: 'Run workflow' }).click()
    await page.getByRole('button', { name: 'Start execution' }).click()
    await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
    await expect(page.getByText('Awaiting Decision').first()).toBeVisible()
    await page.getByRole('button', { name: 'Cancel run' }).click()
    await expect(page.getByText('Cancelled').first()).toBeVisible()
    await expect(page.getByText('Cancelled from the console', { exact: false }).first()).toBeVisible()

    await page.getByRole('link', { name: 'Chaos Lab' }).click()
    await expect(page.getByRole('heading', { name: 'Chaos Lab' })).toBeVisible()
    await expect(page.getByText('Payment gateway timeout')).toBeVisible()

    const duplicateCard = page.locator('.scenario-card', {
      hasText: 'Duplicated gateway response',
    })
    await duplicateCard.getByRole('button', { name: 'Evaluate scenario' }).click()
    const chaosDialog = page.getByRole('dialog')
    await expect(chaosDialog.getByText('Chaos report · Duplicated gateway response')).toBeVisible()
    await expect(chaosDialog.getByText('Exactly one ledger entry').first()).toBeVisible()
    await expect(chaosDialog.getByText('Fault detected by an invariant')).toBeVisible()
    await expect(chaosDialog.getByText('Failed safely', { exact: true })).toBeVisible()
    await expect(chaosDialog.getByRole('link', { name: 'Open fault timeline' })).toBeVisible()
    await chaosDialog.getByRole('button', { name: 'Save reproducible regression test' }).click()
    await expect(chaosDialog.getByText('Reproducible regression test saved')).toBeVisible()
    await chaosDialog.getByRole('button', { name: 'Close dialog' }).click()

    await page.getByRole('link', { name: 'Recovery & Audit' }).click()
    await expect(page.getByRole('heading', { name: 'Recovery & Audit' })).toBeVisible()
    await expect(page.getByText('Audit chain integrity')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Verify' }).first()).toBeVisible()

    await page.getByRole('link', { name: 'Settings & health' }).click()
    await expect(page.getByRole('heading', { name: 'Settings & system health' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Runtime configuration' })).toBeVisible()
    await expect(page.getByText('http://localhost:5173')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Demonstration workspace' })).toBeVisible()

    await page.getByRole('link', { name: 'Overview' }).click()
    await expect(page.getByRole('heading', { name: 'Operations overview' })).toBeVisible()

    expect(consoleErrors).toEqual([])
  })

  test('mobile layout navigates and does not overflow horizontally', async ({ page }) => {
    const consoleErrors = collectConsoleErrors(page)
    await page.setViewportSize({ width: 390, height: 844 })

    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Operations overview' })).toBeVisible()

    await page.getByRole('button', { name: 'Open navigation' }).click()
    await page.getByRole('link', { name: 'Workflow Studio' }).click()
    await expect(page.getByRole('heading', { name: 'Workflow Studio' })).toBeVisible()

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    )
    expect(overflow).toBeLessThanOrEqual(1)

    await page.getByRole('button', { name: 'Open navigation' }).click()
    await page.getByRole('link', { name: 'Decision Inbox' }).click()
    await expect(page.getByRole('heading', { name: 'Decision Inbox' })).toBeVisible()

    await page.getByRole('button', { name: 'Open navigation' }).click()
    await page.getByRole('link', { name: 'Learning Studio' }).click()
    await expect(page.getByRole('heading', { name: 'Learning Studio' })).toBeVisible()
    const learningOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    )
    expect(learningOverflow).toBeLessThanOrEqual(1)

    expect(consoleErrors).toEqual([])
  })
})