import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const source = readFileSync(new URL('../public/theme-control.js', import.meta.url), 'utf8')
const storageKey = 'htp.ui.theme.v1'

function installThemeControl(): void {
  runInNewContext(source, { window, document })
}

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  delete window.HtpTheme
})
afterEach(() => {
  window.localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  delete window.HtpTheme
  vi.restoreAllMocks()
})

describe('shared offline theme controller', () => {
  it('follows the OS by default and persists light and dark across navigation', () => {
    installThemeControl()
    expect(window.HtpTheme?.get()).toBe('system')
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false)

    window.HtpTheme?.set('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(window.localStorage.getItem(storageKey)).toBe('dark')

    window.HtpTheme?.set('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(window.localStorage.getItem(storageKey)).toBe('light')

    window.HtpTheme?.set('system')
    expect(window.localStorage.getItem(storageKey)).toBeNull()
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false)
  })

  it('restores a saved choice and synchronizes cross-tab storage events', () => {
    window.localStorage.setItem(storageKey, 'dark')
    installThemeControl()
    expect(window.HtpTheme?.get()).toBe('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')

    window.localStorage.setItem(storageKey, 'light')
    window.dispatchEvent(new StorageEvent('storage', { key: storageKey }))
    expect(window.HtpTheme?.get()).toBe('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')

    window.localStorage.removeItem(storageKey)
    window.dispatchEvent(new StorageEvent('storage', { key: storageKey }))
    expect(window.HtpTheme?.get()).toBe('system')
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false)
  })

  it('rejects invalid saved choices and safely tolerates disabled storage', () => {
    window.localStorage.setItem(storageKey, 'invalid')
    installThemeControl()
    expect(window.HtpTheme?.get()).toBe('system')
    const storage = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage disabled')
    })
    window.HtpTheme?.set('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    storage.mockRestore()
  })
})
