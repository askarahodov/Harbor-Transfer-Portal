import { createMemoryHistory } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { createAppRouter } from './index'

describe('router', () => {
  it.each(['/login', '/', '/export', '/import', '/history', '/settings'])('resolves %s', async (path) => {
    const router = createAppRouter(createMemoryHistory())
    await router.push(path)
    await router.isReady()
    expect(router.currentRoute.value.path).toBe(path)
  })
})
