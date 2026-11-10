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

  it('stores an activity renewal only for the currently authenticated request', async () => {
    storeAccessToken('before-renewal')
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => ({
      data: {},
      status: 204,
      statusText: 'No Content',
      headers: { 'x-new-access-token': 'after-renewal' },
      config,
    }))

    await apiClient.post('/auth/activity', undefined, { adapter })
    expect(readAccessToken()).toBe('after-renewal')
  })

  it('never restores a logged-out session from a delayed activity response', async () => {
    storeAccessToken('old-session')
    let resolveResponse: ((value: AxiosResponse) => void) | undefined
    const adapter = vi.fn(
      (config: InternalAxiosRequestConfig) =>
        new Promise<AxiosResponse>((resolve) => {
          resolveResponse = (response) => resolve({ ...response, config })
        }),
    )
    const request = apiClient.post('/auth/activity', undefined, { adapter })
    await vi.waitFor(() => expect(resolveResponse).toBeDefined())
    clearAccessToken()
    resolveResponse?.({
      data: {},
      status: 204,
      statusText: 'No Content',
      headers: { 'x-new-access-token': 'stale-new-session' },
    } as AxiosResponse)
    await request
    expect(readAccessToken()).toBeNull()
  })

  it('ignores a delayed 401 for the previous token after a renewal', async () => {
    storeAccessToken('old-session')
    const unauthorized = vi.fn()
    setUnauthorizedHandler(unauthorized)
    let rejectOldRequest: ((reason: AxiosError) => void) | undefined
    const adapter = vi.fn(
      (config: InternalAxiosRequestConfig) =>
        new Promise<AxiosResponse>((_resolve, reject) => {
          rejectOldRequest = (error) => reject(error)
        }),
    )
    const oldRequest = apiClient.get('/auth/me', { adapter })
    await vi.waitFor(() => expect(rejectOldRequest).toBeDefined())
    storeAccessToken('renewed-session')
    const config = adapter.mock.calls[0]?.[0]
    expect(config?.headers.get('Authorization')).toBe('Bearer old-session')
    rejectOldRequest?.(
      new AxiosError(
        'Unauthorized',
        'ERR_BAD_REQUEST',
        config,
        undefined,
        { data: {}, status: 401, statusText: 'Unauthorized', headers: {}, config } as AxiosResponse,
      ),
    )
    await expect(oldRequest).rejects.toBeInstanceOf(AxiosError)
    expect(readAccessToken()).toBe('renewed-session')
    expect(unauthorized).not.toHaveBeenCalled()
  })

  it('ignores a delayed tokenless 401 after a new login', async () => {
    const unauthorized = vi.fn()
    setUnauthorizedHandler(unauthorized)
    let rejectLogin: ((reason: AxiosError) => void) | undefined
    const adapter = vi.fn(
      (config: InternalAxiosRequestConfig) =>
        new Promise<AxiosResponse>((_resolve, reject) => {
          rejectLogin = (error) => reject(error)
        }),
    )
    const loginRequest = apiClient.post('/auth/login', {}, { adapter })
    await vi.waitFor(() => expect(rejectLogin).toBeDefined())
    storeAccessToken('new-login-session')
    const config = adapter.mock.calls[0]?.[0]
    expect(config?.headers.get('Authorization')).toBeFalsy()
    rejectLogin?.(
      new AxiosError(
        'Unauthorized',
        'ERR_BAD_REQUEST',
        config,
        undefined,
        { data: {}, status: 401, statusText: 'Unauthorized', headers: {}, config } as AxiosResponse,
      ),
    )
    await expect(loginRequest).rejects.toBeInstanceOf(AxiosError)
    expect(readAccessToken()).toBe('new-login-session')
    expect(unauthorized).not.toHaveBeenCalled()
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
