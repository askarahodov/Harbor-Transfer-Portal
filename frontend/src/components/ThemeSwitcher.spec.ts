import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ThemeSwitcher from './ThemeSwitcher.vue'

let current: HtpThemeChoice

beforeEach(() => {
  current = 'system'
  vi.stubGlobal('HtpTheme', {
    get: () => current,
    set: (choice: HtpThemeChoice) => {
      current = choice
      window.dispatchEvent(new CustomEvent('htp-theme-change', { detail: { choice } }))
    },
  })
})
afterEach(() => vi.unstubAllGlobals())

describe('ThemeSwitcher', () => {
  it('provides three keyboard-accessible explicit theme controls', async () => {
    const wrapper = mount(ThemeSwitcher)
    const group = wrapper.get('[role="group"][aria-label="Тема оформления"]')
    const system = group.get('[aria-label="Тема: Системная"]')
    const light = group.get('[aria-label="Тема: Светлая"]')
    const dark = group.get('[aria-label="Тема: Тёмная"]')
    expect(group.findAll('button')).toHaveLength(3)
    expect(system.attributes('aria-pressed')).toBe('true')
    await dark.trigger('click')
    expect(current).toBe('dark')
    expect(dark.attributes('aria-pressed')).toBe('true')
    expect(light.attributes('aria-pressed')).toBe('false')
    await light.trigger('click')
    expect(current).toBe('light')
    expect(light.attributes('aria-pressed')).toBe('true')
    await system.trigger('click')
    expect(current).toBe('system')
    expect(system.attributes('aria-pressed')).toBe('true')
    wrapper.unmount()
  })

  it('reflects a theme change in a different tab/document without remounting', async () => {
    const wrapper = mount(ThemeSwitcher)
    current = 'dark'
    window.dispatchEvent(new CustomEvent('htp-theme-change', { detail: { choice: 'dark' } }))
    await wrapper.vm.$nextTick()
    expect(wrapper.get('[aria-label="Тема: Тёмная"]').attributes('aria-pressed')).toBe('true')
    wrapper.unmount()
  })
})
