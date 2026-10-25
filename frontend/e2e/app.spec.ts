import { expect, test } from '@playwright/test'

test('shows the Advance RAG workspace', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Advance RAG').first()).toBeVisible()
  await expect(page.getByText('Your documents, answered with proof.')).toBeVisible()
})

test('opens the RFP bid workspace', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'RFP bids' }).click()
  await expect(page.getByText('Import bid folders')).toBeVisible()
  await expect(page.getByText('Add documents to a bid')).toBeVisible()
})

