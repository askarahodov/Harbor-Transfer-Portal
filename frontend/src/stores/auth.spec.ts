import { AxiosError, type AxiosResponse } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const clientMocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  readAccessToken: vi.fn(),
  storeAccessToken: vi.fn(),
  clearAccessToken: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  apiClient: {
    get: clientMocks.get,
    post: clientMocks.post,
  },
  readAccessToken: clientMocks.readAccessToken,
  storeAccessToken: clientMocks.storeAccessToken,
  clearAccessToken: clientMocks.clearAccessToken,
  setUnauthorizedHandler: clientMocks.setUnauthorizedHandler,
}))

import { useAuthStore } from './auth'

const currentUser = {
  id: 7,
  username: 'operator',
  role: 'operator' as const,
  is_active: true,
}

beforeEach(() => {
  vi.clearAllMocks()
  clientMocks.readAccessToken.mockReturnValue(null)
  setActivePinia(createPinia())
})

describe('auth store', () => {
  it('does not call backend when no session token exists', async () => {
    const auth = useAuthStore()
    await auth.bootstrapSession()

    expect(auth.initialized).toBe(true)
    expect(auth.isAuthenticated).toBe(false)
    expect(clientMocks.get).not.toHaveBeenCalled()
  })

  it('restores a session by validating token through auth/me', async () => {
    clientMocks.readAccessToken.mockReturnValue('stored-token')
    clientMocks.get.mockResolvedValue({ data: currentUser })

    const auth = useAuthStore()
    await auth.bootstrapSession()

    expect(clientMocks.get).toHaveBeenCalledWith('/auth/me')
    expect(auth.user).toEqual(currentUser)
    expect(auth.isAuthenticated).toBe(true)
  })

  it('stores a successful login token only before validating auth/me', async () => {
    clientMocks.post.mockResolvedValue({
      data: { access_token: 'new-token', token_type: 'bearer' },
    })
    clientMocks.get.mockResolvedValue({ data: currentUser })

    const auth = useAuthStore()
    const success = await auth.login('operator', 'correct-password')

    expect(success).toBe(true)
    expect(clientMocks.post).toHaveBeenCalledWith('/auth/login', {
      username: 'operator',
      password: 'correct-password',
    })
    expect(clientMocks.storeAccessToken).toHaveBeenCalledWith('new-token')
    expect(auth.user).toEqual(currentUser)
  })

  it('maps a 401 to one generic invalid-credentials state', async () => {
    const response = { status: 401 } as AxiosResponse
    clientMocks.post.mockRejectedValue(
      new AxiosError('Unauthorized', 'ERR_BAD_REQUEST', undefined, undefined, response),
    )

    const auth = useAuthStore()
    const success = await auth.login('missing-or-existing', 'wrong-password')

    expect(success).toBe(false)
    expect(auth.loginErrorCode).toBe('invalid_credentials')
    expect(auth.user).toBeNull()
    expect(clientMocks.clearAccessToken).toHaveBeenCalled()
  })

  it('clears active state when the API client reports an authenticated 401', () => {
    const auth = useAuthStore()
    auth.user = currentUser
    auth.initialized = true

    const handler = clientMocks.setUnauthorizedHandler.mock.calls.at(-1)?.[0] as
      | (() => void)
      | undefined
    handler?.()

    expect(auth.user).toBeNull()
    expect(auth.isAuthenticated).toBe(false)
    expect(clientMocks.clearAccessToken).toHaveBeenCalled()
  })
})
