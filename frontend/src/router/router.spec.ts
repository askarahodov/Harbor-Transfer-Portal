import { createPinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { useAuthStore, type UserRole } from '@/stores/auth'
import { useRuntimeStore, type PortalContour } from '@/stores/runtime'

import { createAppRouter, installAuthGuards } from './index'

function guardedRouter(role?: UserRole, contour: PortalContour = 'SOURCE') {
  const pinia = createPinia()
  const auth = useAuthStore(pinia)
  const runtime = useRuntimeStore(pinia)
  auth.initialized = true
  auth.user = role ? { id: 1, username: role, role, is_active: true } : null
  runtime.setContour(contour)
  const router = createAppRouter(createMemoryHistory())
  installAuthGuards(router, pinia)
  return router
}

describe('router', () => {
  it.each(['/login', '/', '/export', '/import', '/history', '/users', '/keys', '/settings'])(
    'resolves %s',
    async (path) => {
      const router = createAppRouter(createMemoryHistory())
      await router.push(path)
      await router.isReady()
      expect(router.currentRoute.value.path).toBe(path)
    },
  )

  it('redirects an unauthenticated request to login once', async () => {
    const router = guardedRouter()
    await router.push('/history')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/history')
  })

  it('blocks viewer from transfer and admin routes', async () => {
    const router = guardedRouter('viewer')
    await router.push('/export')
    await router.isReady()
    expect(router.currentRoute.value.name).toBe('dashboard')

    await router.push('/users')
    expect(router.currentRoute.value.name).toBe('dashboard')

    await router.push('/keys')
    expect(router.currentRoute.value.name).toBe('dashboard')
  })

  it('allows SOURCE operator export and blocks TARGET-only import', async () => {
    const router = guardedRouter('operator', 'SOURCE')
    await router.push('/export')
    await router.isReady()
    expect(router.currentRoute.value.name).toBe('export')

    await router.push('/import')
    expect(router.currentRoute.value.name).toBe('dashboard')
  })

  it('allows TARGET operator import and blocks SOURCE-only export', async () => {
    const router = guardedRouter('operator', 'TARGET')
    await router.push('/import')
    await router.isReady()
    expect(router.currentRoute.value.name).toBe('import')

    await router.push('/export')
    expect(router.currentRoute.value.name).toBe('dashboard')
  })

  it('allows admin management routes and redirects authenticated login route', async () => {
    const router = guardedRouter('admin')
    await router.push('/users')
    await router.isReady()
    expect(router.currentRoute.value.name).toBe('users')

    await router.push('/keys')
    expect(router.currentRoute.value.name).toBe('keys')

    await router.push('/settings')
    expect(router.currentRoute.value.name).toBe('settings')

    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('dashboard')
  })
})
