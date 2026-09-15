import type { AxiosResponse } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'

import { useRuntimeStore } from './runtime'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}

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
    expect(store.version).toBeNull()
  })

  it('loads authoritative contour and release version from backend health', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { contour: 'TARGET', version: '1.0.0' },
    } as unknown as AxiosResponse<{ contour: 'TARGET'; version: string }>)
    setActivePinia(createPinia())

    const store = useRuntimeStore()
    await store.loadRuntime()

    expect(apiClient.get).toHaveBeenCalledWith('/health')
    expect(store.contour).toBe('TARGET')
    expect(store.version).toBe('1.0.0')
    expect(store.errorCode).toBeNull()
  })

  it('rejects health payload without a release version', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { contour: 'TARGET' },
    } as unknown as AxiosResponse<{ contour: 'TARGET' }>)
    setActivePinia(createPinia())

    const store = useRuntimeStore()
    await store.loadRuntime()

    expect(store.contour).toBeNull()
    expect(store.version).toBeNull()
    expect(store.errorCode).toBe('runtime_config_unavailable')
  })

  it('keeps injected contour if backend is temporarily unavailable', async () => {
    window.__HTP_CONFIG__ = { contour: 'SOURCE' }
    vi.spyOn(apiClient, 'get').mockRejectedValue(new Error('unavailable'))
    setActivePinia(createPinia())

    const store = useRuntimeStore()
    await store.loadRuntime()

    expect(store.contour).toBe('SOURCE')
    expect(store.version).toBeNull()
    expect(store.errorCode).toBeNull()
  })

  it('changes contour only after backend confirms the requested mode', async () => {
    vi.spyOn(apiClient, 'put').mockResolvedValue({
      data: { previous: 'SOURCE', current: 'TARGET', changed: true },
    } as unknown as AxiosResponse)
    setActivePinia(createPinia())
    const store = useRuntimeStore()
    store.setContour('SOURCE')

    expect(await store.switchMode('TARGET')).toBe(true)

    expect(apiClient.put).toHaveBeenCalledWith('/runtime/mode', { mode: 'TARGET' })
    expect(store.contour).toBe('TARGET')
    expect(store.switchErrorCode).toBeNull()
    expect(store.switching).toBe(false)
  })

  it('cycles SOURCE to TARGET to SOURCE without recreating the store', async () => {
    vi.spyOn(apiClient, 'put')
      .mockResolvedValueOnce({
        data: { previous: 'SOURCE', current: 'TARGET', changed: true },
      } as unknown as AxiosResponse)
      .mockResolvedValueOnce({
        data: { previous: 'TARGET', current: 'SOURCE', changed: true },
      } as unknown as AxiosResponse)
    setActivePinia(createPinia())
    const store = useRuntimeStore()
    store.setContour('SOURCE')

    expect(await store.switchMode('TARGET')).toBe(true)
    expect(store.contour).toBe('TARGET')

    expect(await store.switchMode('SOURCE')).toBe(true)
    expect(store.contour).toBe('SOURCE')
    expect(apiClient.put).toHaveBeenNthCalledWith(1, '/runtime/mode', { mode: 'TARGET' })
    expect(apiClient.put).toHaveBeenNthCalledWith(2, '/runtime/mode', { mode: 'SOURCE' })
  })

  it('preserves old contour and exposes runtime_mode_busy from FastAPI detail', async () => {
    vi.spyOn(apiClient, 'put').mockRejectedValue({
      isAxiosError: true,
      response: { data: { detail: { code: 'runtime_mode_busy', message: 'busy' } } },
    })
    setActivePinia(createPinia())
    const store = useRuntimeStore()
    store.setContour('SOURCE')

    expect(await store.switchMode('TARGET')).toBe(false)

    expect(store.contour).toBe('SOURCE')
    expect(store.switchErrorCode).toBe('runtime_mode_busy')
    expect(store.switching).toBe(false)
  })

  it('rejects a response that does not confirm the requested contour', async () => {
    vi.spyOn(apiClient, 'put').mockResolvedValue({
      data: { previous: 'SOURCE', current: 'SOURCE', changed: false },
    } as unknown as AxiosResponse)
    setActivePinia(createPinia())
    const store = useRuntimeStore()
    store.setContour('SOURCE')

    expect(await store.switchMode('TARGET')).toBe(false)

    expect(store.contour).toBe('SOURCE')
    expect(store.switchErrorCode).toBe('runtime_mode_invalid_response')
  })

  it('ignores an older out-of-order switch response', async () => {
    const older = deferred<AxiosResponse>()
    const latest = deferred<AxiosResponse>()
    vi.spyOn(apiClient, 'put')
      .mockImplementationOnce(() => older.promise)
      .mockImplementationOnce(() => latest.promise)
    setActivePinia(createPinia())
    const store = useRuntimeStore()
    store.setContour('SOURCE')

    const first = store.switchMode('TARGET')
    const second = store.switchMode('TARGET')
    latest.resolve({ data: { previous: 'SOURCE', current: 'TARGET', changed: true } } as AxiosResponse)
    expect(await second).toBe(true)
    expect(store.contour).toBe('TARGET')

    older.resolve({ data: { previous: 'SOURCE', current: 'TARGET', changed: true } } as AxiosResponse)
    expect(await first).toBe(false)
    expect(store.contour).toBe('TARGET')
  })
})
