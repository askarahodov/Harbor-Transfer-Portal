import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as historyApi from '@/api/history'
import type { Operation, OperationSummary } from '@/api/history'
import { useAuthStore } from '@/stores/auth'
import { useHistoryStore } from '@/stores/history'

const summary: OperationSummary = {
  id: 42,
  delivery_id: 'DELIVERY-42',
  type: 'IMPORT',
  status: 'COMPLETED',
  actor_username: 'operator',
  comment: 'offline delivery',
  created_at: '2026-09-14T05:00:00Z',
  started_at: '2026-09-14T05:01:00Z',
  finished_at: '2026-09-14T05:02:00Z',
  error_code: null,
  error_message: null,
  total_artifacts: 2,
  successful_artifacts: 1,
  failed_artifacts: 0,
  skipped_artifacts: 1,
  conflict_artifacts: 0,
  bundle: null,
}

const detail: Operation = {
  id: 42,
  delivery_id: 'DELIVERY-42',
  type: 'IMPORT',
  status: 'COMPLETED',
  actor_username: 'operator',
  comment: 'offline delivery',
  started_at: '2026-09-14T05:01:00Z',
  finished_at: '2026-09-14T05:02:00Z',
  error_code: null,
  error_message: null,
  cancel_requested: false,
  bundle: null,
  progress: {
    total_artifacts: 2,
    completed_artifacts: 2,
    running_artifacts: 0,
    successful_artifacts: 1,
    failed_artifacts: 0,
    skipped_artifacts: 1,
    conflict_artifacts: 0,
    progress_current: 2,
    progress_total: 2,
    current_phase: 'COMPLETED',
    running_artifact_ids: [],
  },
  artifacts: [],
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.restoreAllMocks()
})

describe('History store', () => {
  it('uses server pagination and resets offset when filters are applied', async () => {
    const list = vi.spyOn(historyApi, 'listOperationHistory')
    list
      .mockResolvedValueOnce({ items: [summary], total: 40, limit: 25, offset: 0 })
      .mockResolvedValueOnce({ items: [summary], total: 40, limit: 25, offset: 25 })
      .mockResolvedValueOnce({ items: [summary], total: 1, limit: 25, offset: 0 })

    const store = useHistoryStore()
    await store.load(true)
    expect(store.currentPage).toBe(1)
    expect(store.hasNext).toBe(true)

    await store.nextPage()
    expect(list).toHaveBeenLastCalledWith(store.filters, 25, 25)
    expect(store.currentPage).toBe(2)

    store.filters.status = 'COMPLETED'
    await store.applyFilters()
    expect(list).toHaveBeenLastCalledWith(store.filters, 25, 0)
    expect(store.offset).toBe(0)
  })

  it('loads terminal import receipt only when current backend role policy permits it', async () => {
    vi.spyOn(historyApi, 'getOperation').mockResolvedValue(detail)
    const receipt = vi.spyOn(historyApi, 'getImportReceipt').mockResolvedValue({
      operation_id: 42,
      source_delivery_id: 'DELIVERY-42',
      bundle_sha256: 'a'.repeat(64),
      actor_username: 'operator',
      started_at: '2026-09-14T05:01:00Z',
      finished_at: '2026-09-14T05:02:00Z',
      overwrite_conflicts: false,
      destination_plan_id: 'e'.repeat(64),
      destination_plan_hash: 'f'.repeat(64),
      result: 'COMPLETED',
      artifacts: [],
    })
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 2, username: 'operator', role: 'operator', is_active: true }

    const store = useHistoryStore()
    await store.openDetail(summary)

    expect(receipt).toHaveBeenCalledWith(42)
    expect(store.receiptState).toBe('ready')
    expect(store.receipt?.source_delivery_id).toBe('DELIVERY-42')
    expect(store.receipt?.destination_plan_id).toBe('e'.repeat(64))
    expect(store.receipt?.destination_plan_hash).toBe('f'.repeat(64))
  })

  it('keeps viewer history read-only and does not request restricted receipt', async () => {
    vi.spyOn(historyApi, 'getOperation').mockResolvedValue(detail)
    const receipt = vi.spyOn(historyApi, 'getImportReceipt')
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 3, username: 'viewer', role: 'viewer', is_active: true }

    const store = useHistoryStore()
    await store.openDetail(summary)

    expect(receipt).not.toHaveBeenCalled()
    expect(store.canReadSelectedReceipt).toBe(false)
    expect(store.canDownloadSelectedExport).toBe(false)
  })

  it('rejects invalid local date ranges before sending a backend request', async () => {
    const list = vi.spyOn(historyApi, 'listOperationHistory')
    const store = useHistoryStore()
    store.filters.createdFrom = '2026-09-14T12:00'
    store.filters.createdTo = '2026-09-14T10:00'

    await store.applyFilters()

    expect(list).not.toHaveBeenCalled()
    expect(store.error?.code).toBe('history_date_range_invalid')
  })
})
