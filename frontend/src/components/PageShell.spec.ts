import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import { createAppRouter } from '@/router'
import { useAuthStore, type UserRole } from '@/stores/auth'
import { useRuntimeStore } from '@/stores/runtime'

import PageShell from './PageShell.vue'

let pinia: Pinia
let router: Router

async function mountShell(role: UserRole, contour: 'SOURCE' | 'TARGET', path = '/') {
  const auth = useAuthStore(pinia)
  auth.user = { id: 1, username: role, role, is_active: true }
  auth.initialized = true
  const runtime = useRuntimeStore(pinia)
  runtime.setContour(contour)
  await router.push(path)
  await router.isReady()

  return mount(PageShell, {
    global: { plugins: [pinia, router] },
    slots: { default: '<div>content</div>' },
  })
}

function navLabels(wrapper: VueWrapper): string[] {
  return wrapper.findAll('.nav-link').map((link) => link.text())
}

beforeEach(() => {
  vi.restoreAllMocks()
  pinia = createPinia()
  setActivePinia(pinia)
  router = createAppRouter(createMemoryHistory())
})

describe('PageShell release identity', () => {
  it('shows the authoritative backend release version', async () => {
    const localPinia = createPinia()
    setActivePinia(localPinia)
    const runtime = useRuntimeStore()
    runtime.version = '1.0.0'
    runtime.setContour('SOURCE')

    const localRouter = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: { template: '<div />' } }],
    })
    await localRouter.push('/')
    await localRouter.isReady()

    const wrapper = mount(PageShell, {
      global: { plugins: [localPinia, localRouter] },
    })

    expect(wrapper.find('.release-version').text()).toBe('v1.0.0')
  })
})

describe('PageShell runtime mode switching', () => {
  it('confirms switch, updates navigation and leaves an invalid live route', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({
      data: { previous: 'SOURCE', current: 'TARGET', changed: true },
    })
    const wrapper = await mountShell('operator', 'SOURCE', '/export')

    expect(navLabels(wrapper)).toContain('Отправка')
    expect(navLabels(wrapper)).not.toContain('Приём')

    await wrapper.findAll('.mode-switcher__option')[1]!.trigger('click')
    await flushPromises()

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('TARGET'))
    expect(put).toHaveBeenCalledWith('/runtime/mode', { mode: 'TARGET' })
    expect(useRuntimeStore(pinia).contour).toBe('TARGET')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.name).toBe('dashboard')
    })
    expect(navLabels(wrapper)).toContain('Приём')
    expect(navLabels(wrapper)).not.toContain('Отправка')
  })

  it('does not call backend when the operator cancels confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const put = vi.spyOn(apiClient, 'put')
    const wrapper = await mountShell('operator', 'SOURCE')

    await wrapper.findAll('.mode-switcher__option')[1]!.trigger('click')
    await flushPromises()

    expect(put).not.toHaveBeenCalled()
    expect(useRuntimeStore(pinia).contour).toBe('SOURCE')
  })

  it('shows current contour to viewer without exposing switch controls', async () => {
    const wrapper = await mountShell('viewer', 'TARGET')

    expect(wrapper.find('.mode-control').exists()).toBe(false)
    expect(wrapper.get('.contour-badge').text()).toContain('TARGET')
    expect(navLabels(wrapper)).not.toContain('Отправка')
    expect(navLabels(wrapper)).not.toContain('Приём')
  })
})
