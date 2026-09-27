import { expect, type Locator, type Page } from '@playwright/test'

/** Records any mouse event that reaches the page. DoD 5 is "keyboard-only",
 *  not "keyboard also works".
 *
 *  Enter or Space on a focused <button> makes the browser dispatch a `click`
 *  with `detail === 0` — keyboard activation, not a pointer. Only a click with
 *  a non-zero click count, or any mousedown/mouseup/dblclick, is a mouse. */
export async function forbidMouse(page: Page) {
  await page.addInitScript(() => {
    const flag = () => {
      ;(window as unknown as { __mouseUsed?: boolean }).__mouseUsed = true
    }
    for (const type of ['mousedown', 'mouseup', 'dblclick']) {
      document.addEventListener(type, flag, true)
    }
    document.addEventListener('click', (e) => {
      if (e.detail !== 0) flag()
    }, true)
  })
}

export async function expectNoMouse(page: Page) {
  expect(await page.evaluate(
    () => (window as unknown as { __mouseUsed?: boolean }).__mouseUsed)).toBeUndefined()
}

/** Press Tab until `target` holds focus, the way a keyboard user gets there —
 *  counting stops rather than hard-coding a tab-order that any new control
 *  would silently shift. */
export async function tabTo(page: Page, target: Locator, maxPresses = 40) {
  await expect(target).toBeVisible()
  for (let i = 0; i < maxPresses; i++) {
    if (await target.evaluate((el) => el === document.activeElement)) return
    await page.keyboard.press('Tab')
  }
  throw new Error(`Tab never reached ${target} in ${maxPresses} presses`)
}
