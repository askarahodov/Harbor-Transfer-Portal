import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as exportsApi from '@/api/exports'
import { useRuntimeStore } from '@/stores/runtime'
import ExportView from '@/views/ExportView.vue'

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ExportView accessibility states', () => {
  it('uses the shared status component for empty browser lists', async () => {
    vi.spyOn(exportsApi, 'getHarborConnection').mockResolvedValue({
      connected: true,
      version: '2.14.0',
      auth_mode: 'db_auth',
    })
    vi.spyOn(exportsApi, 'listHarborProjects').mockResolvedValue({
      pagination: { page: 1, page_size: 25, total: 0 },
      items: [],
    })
    const runtime = useRuntimeStore()
    runtime.setContour('SOURCE')

    const wrapper = mount(ExportView, {
      global: {
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    const states = wrapper.findAll('.state-placeholder')
    expect(states.length).toBeGreaterThanOrEqual(1)
    expect(states.every((state) => state.attributes('role') === 'status')).toBe(true)
    expect(wrapper.get('input[role="combobox"][aria-label="Проект Harbor"]').exists()).toBe(true)
    expect(
      wrapper.get('input[role="combobox"][aria-label="Репозиторий Harbor"]').attributes('disabled'),
    ).toBeDefined()
    expect(wrapper.get('input[aria-label="Фильтр версии, tag или digest"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Выберите проект и репозиторий')
  })
})
