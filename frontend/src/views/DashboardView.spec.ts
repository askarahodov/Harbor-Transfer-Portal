import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as exportsApi from '@/api/exports'
import * as historyApi from '@/api/history'
import type { OperationSummary } from '@/api/history'
import { useAuthStore } from '@/stores/auth'
import { useRuntimeStore } from '@/stores/runtime'
import DashboardView from '@/views/DashboardView.vue'

const RouterLinkStub = defineComponent({
  props: { to: { type: String, required: true } },
  template: '<a :href="to"><slot /></a>',
})

const operation: OperationSummary = {
  id: 8,
  delivery_id: 'DELIVERY-8',
  type: 'EXPORT',
  status: 'COMPLETED',
  actor_username: 'operator',
  comment: null,
  created_at: '2026-09-14T06:00:00Z',
  started_at: '2026-09-14T06:01:00Z',
  finished_at: '2026-09-14T06:02:00Z',
  error_code: null,
  error_message: null,
  total_artifacts: 1,
  successful_artifacts: 1,
  failed_artifacts: 0,
  skipped_artifacts: 0,
  conflict_artifacts: 0,
  bundle: null,
}

function mockDashboardData(items: OperationSummary[] = [operation]): void {
  vi.spyOn(exportsApi, 'getHarborConnection').mockResolvedValue({
    connected: true,
    version: '2.14.0',
    auth_mode: 'basic',
  })
  vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
    items,
    total: items.length,
    limit: 5,
    offset: 0,
  })
}

function mountDashboard() {
  return mount(DashboardView, {
    global: {
      stubs: { RouterLink: RouterLinkStub },
    },
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
  setActivePinia(createPinia())
})

describe('DashboardView', () => {
  it('shows SOURCE identity and export action for an operator', async () => {
    mockDashboardData()
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 2, username: 'operator', role: 'operator', is_active: true }
    useRuntimeStore().setContour('SOURCE')

    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.text()).toContain('SOURCE')
    expect(wrapper.text()).toContain('Отправить артефакты')
    expect(wrapper.find('a[href="/export"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/import"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('DELIVERY-8')
    expect(wrapper.find('a[href="/history"]').exists()).toBe(true)
  })

  it('shows TARGET identity and import action for an operator', async () => {
    mockDashboardData()
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 2, username: 'operator', role: 'operator', is_active: true }
    useRuntimeStore().setContour('TARGET')

    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.text()).toContain('TARGET')
    expect(wrapper.text()).toContain('Принять пакет')
    expect(wrapper.find('a[href="/import"]').exists()).toBe(true)
    expect(wrapper.find('a[href="/export"]').exists()).toBe(false)
  })

  it('keeps viewer dashboard read-only while preserving history and Harbor status', async () => {
    mockDashboardData()
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 3, username: 'viewer', role: 'viewer', is_active: true }
    useRuntimeStore().setContour('TARGET')

    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.text()).toContain('Режим только для чтения')
    expect(wrapper.find('a[href="/import"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/export"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/history"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Подключение установлено')
  })

  it('renders actionable Harbor failure without hiding operation history', async () => {
    vi.spyOn(exportsApi, 'getHarborConnection').mockRejectedValue(new Error('harbor down'))
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [operation], total: 1, limit: 5, offset: 0,
    })
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 1, username: 'admin', role: 'admin', is_active: true }
    useRuntimeStore().setContour('SOURCE')

    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.text()).toContain('Не удалось проверить подключение к локальному Harbor')
    expect(wrapper.text()).toContain('Проверьте URL, credentials и TLS/CA в настройках')
    expect(wrapper.text()).toContain('DELIVERY-8')
    expect(wrapper.find('button').text()).toContain('Повторить')
  })

  it('shows the next valid action when history is empty', async () => {
    mockDashboardData([])
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 2, username: 'operator', role: 'operator', is_active: true }
    useRuntimeStore().setContour('SOURCE')

    const wrapper = mountDashboard()
    await flushPromises()

    expect(wrapper.text()).toContain('Операций пока нет')
    expect(wrapper.text()).toContain('Начните с действия «Отправить артефакты»')
  })
})
