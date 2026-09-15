import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ModeSwitcher from './ModeSwitcher.vue'

describe('ModeSwitcher', () => {
  it('marks the active mode and emits only a different mode', async () => {
    const wrapper = mount(ModeSwitcher, { props: { contour: 'SOURCE' } })
    const buttons = wrapper.findAll('button')

    expect(buttons).toHaveLength(2)
    expect(buttons[0]!.text()).toBe('Отправка')
    expect(buttons[0]!.attributes('aria-pressed')).toBe('true')
    expect(buttons[0]!.attributes()).toHaveProperty('disabled')
    expect(buttons[1]!.attributes('aria-pressed')).toBe('false')

    await buttons[1]!.trigger('click')
    expect(wrapper.emitted('switch')).toEqual([['TARGET']])
  })

  it('disables both options while a switch request is running', () => {
    const wrapper = mount(ModeSwitcher, { props: { contour: 'SOURCE', busy: true } })

    expect(wrapper.findAll('button').every((button) => button.attributes('disabled') !== undefined)).toBe(true)
  })

  it('shows a stable busy explanation without changing the selected mode', () => {
    const wrapper = mount(ModeSwitcher, {
      props: { contour: 'SOURCE', errorCode: 'runtime_mode_busy' },
    })

    expect(wrapper.text()).toContain('Дождитесь завершения текущей операции')
    expect(wrapper.findAll('button')[0]!.attributes('aria-pressed')).toBe('true')
  })
})
