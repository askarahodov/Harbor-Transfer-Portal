import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { useRuntimeStore } from '@/stores/runtime'

import PageShell from './PageShell.vue'

describe('PageShell release identity', () => {
  it('shows the authoritative backend release version', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const runtime = useRuntimeStore()
    runtime.version = '1.0.0'
    runtime.setContour('SOURCE')

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: { template: '<div />' } }],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(PageShell, {
      global: { plugins: [pinia, router] },
    })

    expect(wrapper.find('.release-version').text()).toBe('v1.0.0')
  })
})
