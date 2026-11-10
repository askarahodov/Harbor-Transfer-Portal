import { apiClient, readAccessToken } from '@/api/client'

// A real browser interaction, not silent API polling, advances the inactivity window.
// The server permits idle windows as short as one minute. A 30-second
// upper bound keeps the activity proof ahead of even that smallest setting,
// without a continuous timer or renewal from background API polling.
const ACTIVITY_SYNC_INTERVAL_MS = 30 * 1000
const ACTIVITY_EVENTS = ['pointerdown', 'pointermove', 'keydown', 'scroll', 'touchstart'] as const

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
    document.addEventListener(name, onActivity, { passive: true, capture: true })
  }

  return () => {
    for (const name of ACTIVITY_EVENTS) document.removeEventListener(name, onActivity, true)
  }
}
