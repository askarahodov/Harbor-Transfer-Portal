import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as exportsApi from '@/api/exports'
import * as historyApi from '@/api/history'
import type { OperationSummary } from '@/api/history'
import { useDashboardStore } from '@/stores/dashboard'

const operation: OperationSummary = {
  id: 21,
  delivery_id: 'DELIVERY-21',
  type: 'IMPORT',
  status: 'COMPLETED',
  actor_username: 'operator',
  comment: null,
  created_at: '2026-09-14T06:00:00Z',
  started_at: '2026-09-14T06:01:00Z',
  finished_at: '2026-09-14T06:02:00Z',
  error_code: null,
  error_message: null,
  total_artifacts: 2,
  successful_artifacts: 2,
  failed_artifacts: 0,
  skipped_artifacts: 0,
  conflict_artifacts: 0,
  bundle: null,
}

beforeEach(() => {
  vi.restoreAllMocks()
  setActivePinia(createPinia())
})

describe('Dashboard store', () => {
  it('loads local Harbor and the five newest persisted operations without client-side filtering', async () => {
    const harbor = vi.spyOn(exportsApi, 'getHarborConnection').mockResolvedValue({
      connected: true,
      version: '2.14.0',
      auth_mode: 'basic',
    })
    const history = vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [operation],
      total: 1,
      limit: 5,
      offset: 0,
    })

    const store = useDashboardStore()
    await store.load()

    expect(harbor).toHaveBeenCalledOnce()
    expect(history).toHaveBeenCalledWith(
      {
        type: '',
        status: '',
        actor: '',
        search: '',
        createdFrom: '',
        createdTo: '',
      },
      5,
      0,
    )
    expect(store.harbor?.connected).toBe(true)
    expect(store.recentOperations).toEqual([operation])
    expect(store.harborError).toBeNull()
    expect(store.historyError).toBeNull()
  })

  it('keeps recent operations visible when the independent Harbor probe fails', async () => {
    vi.spyOn(exportsApi, 'getHarborConnection').mockRejectedValue(new Error('harbor down'))
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [operation],
      total: 1,
      limit: 5,
      offset: 0,
    })

    const store = useDashboardStore()
    await store.load()

    expect(store.harbor).toBeNull()
    expect(store.harborError?.code).toBe('unexpected_error')
    expect(store.recentOperations).toEqual([operation])
    expect(store.historyError).toBeNull()
  })

  it('keeps Harbor status available when history fails', async () => {
    vi.spyOn(exportsApi, 'getHarborConnection').mockResolvedValue({
      connected: true,
      version: null,
      auth_mode: null,
    })
    vi.spyOn(historyApi, 'listOperationHistory').mockRejectedValue(new Error('history down'))

    const store = useDashboardStore()
    await store.load()

    expect(store.harbor?.connected).toBe(true)
    expect(store.recentOperations).toEqual([])
    expect(store.historyError?.code).toBe('unexpected_error')
  })
})
