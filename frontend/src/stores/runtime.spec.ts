import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it } from 'vitest'

import { useRuntimeStore } from './runtime'

afterEach(() => {
  delete window.__HTP_CONFIG__
})

describe('runtime store', () => {
  it('reads contour from runtime configuration', () => {
    window.__HTP_CONFIG__ = { contour: 'SOURCE' }
    setActivePinia(createPinia())

    const store = useRuntimeStore()

    expect(store.contour).toBe('SOURCE')
  })

  it('keeps contour unknown when runtime configuration is absent', () => {
    setActivePinia(createPinia())

    const store = useRuntimeStore()

    expect(store.contour).toBeNull()
  })
})
