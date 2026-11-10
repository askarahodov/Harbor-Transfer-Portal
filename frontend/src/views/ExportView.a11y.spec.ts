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
    expect(wrapper.get('input[type="search"][aria-label="Версия / tag"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Выберите проект и репозиторий')
  })
})


describe('Nexus SOURCE selection accessibility', () => {
  it('shows type-aware actions and manual fallback without Harbor browse requests', async () => {
    vi.spyOn(exportsApi, 'listHarborProfiles').mockResolvedValue({
      items: [{
        id: 'nexus', name: 'Regional Nexus', url: 'https://nexus.example.test',
        registry_type: 'nexus', is_default: true,
      }],
    })
    const inventory = vi.spyOn(exportsApi, 'getNexusInventory').mockResolvedValue({
      available: false, items: [], message: 'Недостаточно прав на inventory',
    })
    vi.spyOn(exportsApi, 'listNexusRepositories').mockResolvedValue({
      available: false, items: [], message: 'Nexus catalog недоступен', truncated: false,
    })
    const connection = vi.spyOn(exportsApi, 'getHarborConnection')
    const projects = vi.spyOn(exportsApi, 'listHarborProjects')
    const repositories = vi.spyOn(exportsApi, 'listHarborRepositories')
    const artifacts = vi.spyOn(exportsApi, 'listHarborArtifacts')
    useRuntimeStore().setContour('SOURCE')
    const wrapper = mount(ExportView, {
      global: {
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()
    expect(inventory).toHaveBeenCalledWith('nexus')
    expect(wrapper.get('#export-harbor-profile').text()).toContain('Nexus · Docker v2')
    expect(wrapper.get('.nexus-inventory [role="status"]').text())
      .toContain('Недостаточно прав')
    expect(wrapper.get('form.nexus-exact-form input[placeholder="team/application"]').exists())
      .toBe(true)
    expect(wrapper.get('form.nexus-exact-form input[placeholder="1.0.0"]').exists())
      .toBe(true)
    expect(wrapper.find('[aria-label="Выбор артефакта Harbor"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Helm OCI не используются')
    expect(connection).not.toHaveBeenCalled()
    expect(projects).not.toHaveBeenCalled()
    expect(repositories).not.toHaveBeenCalled()
    expect(artifacts).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
