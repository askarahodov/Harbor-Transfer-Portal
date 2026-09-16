import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import StatePlaceholder from './StatePlaceholder.vue'

describe('StatePlaceholder', () => {
  it('uses a polite status for loading and empty states', () => {
    const wrapper = mount(StatePlaceholder, {
      props: {
        kind: 'loading',
        title: 'Загрузка',
        description: 'Подождите',
        compact: true,
      },
    })

    expect(wrapper.attributes('role')).toBe('status')
    expect(wrapper.attributes('aria-live')).toBe('polite')
    expect(wrapper.classes()).toContain('state-placeholder--compact')
    expect(wrapper.text()).toContain('Загрузка')
    expect(wrapper.text()).toContain('Подождите')
  })

  it('uses an assertive alert for error state', () => {
    const wrapper = mount(StatePlaceholder, {
      props: {
        kind: 'error',
        title: 'Ошибка',
      },
    })

    expect(wrapper.attributes('role')).toBe('alert')
    expect(wrapper.attributes('aria-live')).toBe('assertive')
  })
})
