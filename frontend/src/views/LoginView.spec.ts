import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

import { createAppRouter } from '@/router'
import { useAuthStore } from '@/stores/auth'
import { useRuntimeStore } from '@/stores/runtime'

import LoginView from './LoginView.vue'

async function mountLogin() {
  const pinia = createPinia()
  const router = createAppRouter(createMemoryHistory())
  await router.push('/login')
  await router.isReady()
  useRuntimeStore(pinia).setContour('SOURCE')
  const auth = useAuthStore(pinia)
  const wrapper = mount(LoginView, {
    global: { plugins: [pinia, router] },
  })
  return { wrapper, auth, router }
}

describe('login view', () => {
  it('exposes the local documentation before authentication', async () => {
    const { wrapper } = await mountLogin()

    expect(wrapper.get('.login-docs').attributes('href')).toBe('/docs/#/docs/user-guide?id=login')
  })

  it('requires both username and password before authentication', async () => {
    const { wrapper, auth } = await mountLogin()
    const login = vi.spyOn(auth, 'login')

    await wrapper.find('form').trigger('submit')

    expect(login).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toBe('Введите имя пользователя и пароль.')
  })

  it('shows one generic message for invalid credentials', async () => {
    const { wrapper, auth } = await mountLogin()
    vi.spyOn(auth, 'login').mockImplementation(async () => {
      auth.loginErrorCode = 'invalid_credentials'
      return false
    })

    await wrapper.get('#username').setValue('operator')
    await wrapper.get('#password').setValue('wrong-password')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toBe('Неверное имя пользователя или пароль.')
  })

  it('allows keyboard-accessible password visibility toggle', async () => {
    const { wrapper } = await mountLogin()
    const password = wrapper.get('#password')
    const toggle = wrapper.get('.password-toggle')

    expect(password.attributes('type')).toBe('password')
    expect(toggle.attributes('aria-pressed')).toBe('false')
    await toggle.trigger('click')
    expect(password.attributes('type')).toBe('text')
    expect(toggle.attributes('aria-pressed')).toBe('true')
  })
})
