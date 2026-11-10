import { apiClient, readAccessToken } from '@/api/client'

// A real browser interaction, not silent API polling, advances the inactivity window.
// 5-minute throttling avoids a request per mouse/keyboard event while active use
// continues to renew well before the server's 60-minute idle deadline.
const ACTIVITY_SYNC_INTERVAL_MS = 5 * 60 * 1000
const ACTIVITY_EVENTS = ['pointerdown', 'keydown', 'scroll', 'touchstart'] as const

export function installSessionActivityTracking(): () => void {
  let lastAttemptAt = Number.NEGATIVE_INFINITY
  let inFlight = false

  function onActivity(): void {
    if (!readAccessToken() || inFlight) return
    const now = Date.now()
    if (now >= lastAttemptAt && now - lastAttemptAt < ACTIVITY_SYNC_INTERVAL_MS) return
    lastAttemptAt = now
    inFlight = true
    // The shared interceptor stores X-New-Access-Token only if the session
    // is still the one that sent the request. A 401 clears the session.
    void apiClient.post('/auth/activity').catch(() => undefined).finally(() => {
      inFlight = false
    })
  }

  for (const name of ACTIVITY_EVENTS) {
    document.addEventListener(name, onActivity, { passive: true })
  }

  return () => {
    for (const name of ACTIVITY_EVENTS) document.removeEventListener(name, onActivity)
  }
}
