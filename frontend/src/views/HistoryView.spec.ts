import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as historyApi from '@/api/history'
import type { Operation, OperationSummary } from '@/api/history'
import { useAuthStore } from '@/stores/auth'
import HistoryView from '@/views/HistoryView.vue'

const summary: OperationSummary = {
  id: 7,
  delivery_id: 'DELIVERY-7',
  type: 'EXPORT',
  status: 'COMPLETED',
  actor_username: 'operator',
  comment: null,
  created_at: '2026-09-14T05:00:00Z',
  started_at: '2026-09-14T05:01:00Z',
  finished_at: '2026-09-14T05:02:00Z',
  error_code: null,
  error_message: null,
  total_artifacts: 1,
  successful_artifacts: 1,
  failed_artifacts: 0,
  skipped_artifacts: 0,
  conflict_artifacts: 0,
  bundle: { filename: 'delivery.htp.tar.gz', size_bytes: 1234, sha256: 'a'.repeat(64) },
}

const detail: Operation = {
  id: 7,
  delivery_id: 'DELIVERY-7',
  type: 'EXPORT',
  status: 'COMPLETED',
  actor_username: 'operator',
  comment: null,
  started_at: '2026-09-14T05:01:00Z',
  finished_at: '2026-09-14T05:02:00Z',
  error_code: null,
  error_message: null,
  cancel_requested: false,
  bundle: summary.bundle,
  progress: {
    total_artifacts: 1,
    completed_artifacts: 1,
    running_artifacts: 0,
    successful_artifacts: 1,
    failed_artifacts: 0,
    skipped_artifacts: 0,
    conflict_artifacts: 0,
    progress_current: 1,
    progress_total: 1,
    current_phase: 'COMPLETED',
    running_artifact_ids: [],
  },
  artifacts: [
    {
      id: 1,
      artifact_type: 'container-image',
      repository: 'project/app',
      name: null,
      reference: '1.0.0',
      version: null,
      source_digest: `sha256:${'b'.repeat(64)}`,
      target_digest: null,
      status: 'VERIFIED',
      error_code: null,
      error_message: null,
      size_bytes: 100,
      started_at: '2026-09-14T05:01:00Z',
      finished_at: '2026-09-14T05:02:00Z',
    },
  ],
}

beforeEach(() => {
  vi.restoreAllMocks()
  setActivePinia(createPinia())
})

describe('HistoryView', () => {
  it('renders server history, filters and operation detail without mutation controls', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [summary], total: 1, limit: 25, offset: 0,
    })
    vi.spyOn(historyApi, 'getOperation').mockResolvedValue(detail)
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 3, username: 'viewer', role: 'viewer', is_active: true }

    const wrapper = mount(HistoryView)
    await flushPromises()

    expect(wrapper.text()).toContain('История операций')
    expect(wrapper.text()).toContain('DELIVERY-7')
    expect(wrapper.find('select').exists()).toBe(true)

    await wrapper.get('.link-button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Bundle metadata')
    expect(wrapper.text()).toContain('project/app:1.0.0')
    expect(wrapper.text()).not.toContain('Отменить операцию')
    expect(wrapper.text()).not.toContain('Скачать через авторизованный ticket')
  })

  it('shows a useful empty state', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [], total: 0, limit: 25, offset: 0,
    })

    const wrapper = mount(HistoryView)
    await flushPromises()

    expect(wrapper.text()).toContain('Операции не найдены')
  })

  it('shows safe API failure state without raw logs', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockRejectedValue(new Error('network down'))

    const wrapper = mount(HistoryView)
    await flushPromises()

    expect(wrapper.text()).toContain('unexpected_error')
    expect(wrapper.text()).toContain('Не удалось загрузить историю операций')
  })
})
