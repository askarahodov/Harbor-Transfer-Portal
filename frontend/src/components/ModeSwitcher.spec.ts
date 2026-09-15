import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ModeSwitcher from './ModeSwitcher.vue'

describe('ModeSwitcher', () => {
  it('shows both workspaces and marks the active mode', () => {
    const wrapper = mount(ModeSwitcher, { props: { contour: 'SOURCE' } })
    const buttons = wrapper.findAll('button')

    expect(buttons).toHaveLength(2)
    expect(buttons[0]!.text()).toBe('Отправка')
    expect(buttons[0]!.attributes('aria-pressed')).toBe('true')
    expect(buttons[0]!.attributes()).toHaveProperty('disabled')
    expect(buttons[1]!.text()).toBe('Приём')
    expect(buttons[1]!.attributes('aria-pressed')).toBe('false')
  })

  it('emits only the requested different mode', async () => {
    const wrapper = mount(ModeSwitcher, { props: { contour: 'SOURCE' } })

    await wrapper.findAll('button')[1]!.trigger('click')

    expect(wrapper.emitted('switch')).toEqual([['TARGET']])
  })

  it('disables switching while backend request is running', () => {
    const wrapper = mount(ModeSwitcher, { props: { contour: 'SOURCE', busy: true } })

    expect(wrapper.findAll('button').every((button) => button.attributes('disabled') !== undefined)).toBe(true)
  })

  it('renders an actionable busy error without changing the selected mode', () => {
    const wrapper = mount(ModeSwitcher, {
      props: { contour: 'SOURCE', errorCode: 'runtime_mode_busy' },
    })

    expect(wrapper.text()).toContain('Дождитесь завершения текущей операции')
    expect(wrapper.findAll('button')[0]!.attributes('aria-pressed')).toBe('true')
  })
})
