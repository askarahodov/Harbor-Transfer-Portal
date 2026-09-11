import type { AxiosResponse } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'

import { useRuntimeStore } from './runtime'

afterEach(() => {
  vi.restoreAllMocks()
  delete window.__HTP_CONFIG__
})

describe('runtime store', () => {
  it('reads contour from runtime configuration before backend bootstrap completes', () => {
    window.__HTP_CONFIG__ = { contour: 'SOURCE' }
    setActivePinia(createPinia())

    const store = useRuntimeStore()

    expect(store.contour).toBe('SOURCE')
  })

  it('loads the authoritative contour from backend health', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { contour: 'TARGET' },
    } as unknown as AxiosResponse<{ contour: 'TARGET' }>)
    setActivePinia(createPinia())

    const store = useRuntimeStore()
    await store.loadRuntime()

    expect(apiClient.get).toHaveBeenCalledWith('/health')
    expect(store.contour).toBe('TARGET')
    expect(store.errorCode).toBeNull()
  })

  it('keeps injected contour if backend is temporarily unavailable', async () => {
    window.__HTP_CONFIG__ = { contour: 'SOURCE' }
    vi.spyOn(apiClient, 'get').mockRejectedValue(new Error('unavailable'))
    setActivePinia(createPinia())

    const store = useRuntimeStore()
    await store.loadRuntime()

    expect(store.contour).toBe('SOURCE')
    expect(store.errorCode).toBeNull()
  })
})
