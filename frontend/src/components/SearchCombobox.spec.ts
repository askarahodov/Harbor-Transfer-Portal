import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import SearchCombobox from './SearchCombobox.vue'

const options = [
  { value: 'alpha', label: 'Alpha', description: 'private' },
  { value: 'beta', label: 'Beta', description: 'public' },
]

describe('SearchCombobox', () => {
  it('supports keyboard navigation and selection', async () => {
    const wrapper = mount(SearchCombobox, {
      props: { label: 'Проект Harbor', modelValue: '', search: '', options },
    })
    const input = wrapper.get('[role="combobox"]')
    await input.trigger('focus')
    expect(input.attributes('aria-expanded')).toBe('true')

    await input.trigger('keydown', { key: 'ArrowDown' })
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('select')?.[0]).toEqual(['beta'])
    expect(input.attributes('aria-expanded')).toBe('false')
  })

  it('emits search text and closes with Escape', async () => {
    const wrapper = mount(SearchCombobox, {
      props: { label: 'Репозиторий Harbor', modelValue: '', search: '', options },
    })
    const input = wrapper.get('[role="combobox"]')
    await input.trigger('focus')
    await input.setValue('bet')
    expect(wrapper.emitted('update:search')?.at(-1)).toEqual(['bet'])
    await input.trigger('keydown', { key: 'Escape' })
    expect(input.attributes('aria-expanded')).toBe('false')
  })

  it('exposes paged navigation without enabling unavailable directions', async () => {
    const wrapper = mount(SearchCombobox, {
      props: { label: 'Проект Harbor', modelValue: '', search: '', options, page: 2, total: 60, pageSize: 25 },
    })
    await wrapper.get('[role="combobox"]').trigger('focus')
    const pager = wrapper.get('[aria-label="Страницы результатов"]')
    const buttons = pager.findAll('button')
    expect(buttons[0]!.attributes('disabled')).toBeUndefined()
    expect(buttons[1]!.attributes('disabled')).toBeUndefined()
    await buttons[1]!.trigger('click')
    expect(wrapper.emitted('next')).toHaveLength(1)
  })
})
