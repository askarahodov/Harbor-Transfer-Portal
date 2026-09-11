import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  apiClient,
  clearAccessToken,
  readAccessToken,
  setUnauthorizedHandler,
  storeAccessToken,
} from './client'

beforeEach(() => {
  window.sessionStorage.clear()
  setUnauthorizedHandler(null)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('api client session transport', () => {
  it('keeps the bearer token in sessionStorage and attaches it to requests', async () => {
    storeAccessToken('session-token')
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => ({
      data: {},
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    }))

    await apiClient.get('/health', { adapter })

    expect(readAccessToken()).toBe('session-token')
    const request = adapter.mock.calls[0]?.[0]
    expect(request?.headers.get('Authorization')).toBe('Bearer session-token')
  })

  it('clears the session and invokes the handler once on authenticated 401', async () => {
    storeAccessToken('expired-token')
    const unauthorized = vi.fn()
    setUnauthorizedHandler(unauthorized)
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      const response = {
        data: {},
        status: 401,
        statusText: 'Unauthorized',
        headers: {},
        config,
      } as AxiosResponse
      throw new AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, undefined, response)
    })

    await expect(apiClient.get('/auth/me', { adapter })).rejects.toBeInstanceOf(AxiosError)

    expect(readAccessToken()).toBeNull()
    expect(unauthorized).toHaveBeenCalledTimes(1)
  })

  it('does not treat a tokenless login 401 as an expired session', async () => {
    clearAccessToken()
    const unauthorized = vi.fn()
    setUnauthorizedHandler(unauthorized)
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      const response = {
        data: {},
        status: 401,
        statusText: 'Unauthorized',
        headers: {},
        config,
      } as AxiosResponse
      throw new AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, undefined, response)
    })

    await expect(apiClient.post('/auth/login', {}, { adapter })).rejects.toBeInstanceOf(AxiosError)

    expect(unauthorized).not.toHaveBeenCalled()
  })
})
