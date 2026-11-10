import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const clientMocks = vi.hoisted(() => ({
  readAccessToken: vi.fn(),
  post: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  readAccessToken: clientMocks.readAccessToken,
  apiClient: { post: clientMocks.post },
}))

import { installSessionActivityTracking } from './sessionActivity'

let cleanup: (() => void) | null = null

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-11-10T12:00:00Z'))
  vi.clearAllMocks()
  clientMocks.readAccessToken.mockReturnValue('active-token')
  clientMocks.post.mockResolvedValue({ status: 204 })
})

afterEach(() => {
  cleanup?.()
  cleanup = null
  vi.useRealTimers()
})

describe('interactive sliding session', () => {
  it('renews only on real user events, throttled to five minutes', async () => {
    cleanup = installSessionActivityTracking()
    document.dispatchEvent(new Event('pointerdown'))
    expect(clientMocks.post).toHaveBeenCalledExactlyOnceWith('/auth/activity')
    await Promise.resolve()
    await Promise.resolve()

    vi.advanceTimersByTime(4 * 60 * 1000)
    document.dispatchEvent(new Event('keydown'))
    expect(clientMocks.post).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(60 * 1000)
    document.dispatchEvent(new Event('scroll'))
    expect(clientMocks.post).toHaveBeenCalledTimes(2)
  })

  it('does not keep a session alive merely because the page remains open', () => {
    cleanup = installSessionActivityTracking()
    vi.advanceTimersByTime(70 * 60 * 1000)
    expect(clientMocks.post).not.toHaveBeenCalled()
  })

  it('does not renew while signed out', () => {
    clientMocks.readAccessToken.mockReturnValue(null)
    cleanup = installSessionActivityTracking()
    document.dispatchEvent(new Event('pointerdown'))
    document.dispatchEvent(new Event('keydown'))
    expect(clientMocks.post).not.toHaveBeenCalled()
  })

  it('detaches activity listeners on cleanup', () => {
    cleanup = installSessionActivityTracking()
    cleanup()
    cleanup = null
    document.dispatchEvent(new Event('pointerdown'))
    expect(clientMocks.post).not.toHaveBeenCalled()
  })
})
