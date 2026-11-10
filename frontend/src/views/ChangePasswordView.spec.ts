import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

import { createAppRouter } from '@/router'
import { useAuthStore } from '@/stores/auth'

import ChangePasswordView from './ChangePasswordView.vue'

describe('first-login password change', () => {
  it('requires a new matching 12-character password and handles a successful change', async () => {
    const pinia = createPinia()
    const router = createAppRouter(createMemoryHistory())
    await router.push('/change-password')
    await router.isReady()
    const auth = useAuthStore(pinia)
    const change = vi.spyOn(auth, 'changePassword').mockResolvedValue(true)
    const wrapper = mount(ChangePasswordView, { global: { plugins: [pinia, router] } })

    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    await wrapper.get('#current-password').setValue('admin')
    await wrapper.get('#new-password').setValue('new-admin-password-123')
    await wrapper.get('#confirm-password').setValue('new-admin-password-123')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(change).toHaveBeenCalledWith('admin', 'new-admin-password-123')
    expect(router.currentRoute.value.path).toBe('/login')
  })
})
