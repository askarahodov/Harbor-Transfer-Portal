import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
  retry_of_operation_id: null,
  failure_policy: null,
  created_at: '2026-09-14T05:00:00Z',
  started_at: '2026-09-14T05:01:00Z',
  finished_at: '2026-09-14T05:02:00Z',
  error_code: null,
  error_message: null,
  total_artifacts: 0,
  successful_artifacts: 0,
  failed_artifacts: 0,
  skipped_artifacts: 0,
  conflict_artifacts: 0,
  bundle: null,
}

const detail: Operation = {
  id: 7,
  delivery_id: 'DELIVERY-7',
  type: 'EXPORT',
  status: 'COMPLETED',
  actor_username: 'operator',
  comment: null,
  retry_of_operation_id: null,
  failure_policy: null,
  started_at: '2026-09-14T05:01:00Z',
  finished_at: '2026-09-14T05:02:00Z',
  error_code: null,
  error_message: null,
  cancel_requested: false,
  bundle: null,
  progress: {
    total_artifacts: 0,
    completed_artifacts: 0,
    running_artifacts: 0,
    successful_artifacts: 0,
    failed_artifacts: 0,
    skipped_artifacts: 0,
    conflict_artifacts: 0,
    progress_current: 0,
    progress_total: 0,
    current_phase: 'COMPLETED',
    running_artifact_ids: [],
  },
  artifacts: [],
}

beforeEach(() => {
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.initialized = true
  auth.user = { id: 3, username: 'viewer', role: 'viewer', is_active: true }
})

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('HistoryView accessibility', () => {
  it('renders the shared accessible empty state', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [], total: 0, limit: 25, offset: 0,
    })

    const wrapper = mount(HistoryView)
    await flushPromises()

    const state = wrapper.get('.state-placeholder')
    expect(state.attributes('role')).toBe('status')
    expect(state.attributes('aria-live')).toBe('polite')
    expect(state.text()).toContain('Операции не найдены')
  })

  it('moves focus into the detail dialog and restores it to the opener', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [summary], total: 1, limit: 25, offset: 0,
    })
    vi.spyOn(historyApi, 'getOperation').mockResolvedValue(detail)

    const wrapper = mount(HistoryView, { attachTo: document.body })
    await flushPromises()

    const opener = wrapper.get('.link-button')
    ;(opener.element as HTMLElement).focus()
    expect(document.activeElement).toBe(opener.element)

    await opener.trigger('click')
    await flushPromises()

    const close = wrapper.get('[aria-label="Закрыть детали"]')
    expect(document.activeElement).toBe(close.element)

    await close.trigger('click')
    await flushPromises()

    expect(document.activeElement).toBe(opener.element)
    wrapper.unmount()
  })
})
