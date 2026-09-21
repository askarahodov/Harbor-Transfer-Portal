import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import { listOperationHistory } from '@/api/history'

describe('history API', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('projects bounded pagination and filters to backend query parameters', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { items: [], total: 0, limit: 25, offset: 25 },
    })

    await listOperationHistory(
      {
        type: 'IMPORT',
        status: 'FAILED',
        actor: ' operator ',
        search: 'DELIVERY-42',
        createdFrom: '2026-09-01T10:00',
        createdTo: '2026-09-02T11:30',
      },
      25,
      25,
    )

    expect(get).toHaveBeenCalledOnce()
    const [, config] = get.mock.calls[0]
    expect(config?.params).toMatchObject({
      limit: 25,
      offset: 25,
      type: 'IMPORT',
      status: 'FAILED',
      actor: 'operator',
      search: 'DELIVERY-42',
    })
    expect(config?.params.created_from).toContain('2026-09-01')
    expect(config?.params.created_to).toContain('2026-09-02')
  })

  it('omits empty filters instead of sending ambiguous empty values', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { items: [], total: 0, limit: 25, offset: 0 },
    })

    await listOperationHistory(
      { type: '', status: '', actor: '', search: '', createdFrom: '', createdTo: '' },
      25,
      0,
    )

    expect(get.mock.calls[0][1]?.params).toEqual({ limit: 25, offset: 0 })
  })
})
