import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as usersApi from '@/api/users'
import type { ManagedUser } from '@/api/users'
import UsersView from '@/views/UsersView.vue'

const adminUser: ManagedUser = {
  id: 1,
  username: 'admin',
  role: 'admin',
  is_active: true,
  created_at: '2026-09-14T06:00:00Z',
  updated_at: '2026-09-14T06:00:00Z',
  last_login_at: '2026-09-14T06:30:00Z',
}

const operatorUser: ManagedUser = {
  id: 2,
  username: 'operator',
  role: 'operator',
  is_active: true,
  created_at: '2026-09-14T06:05:00Z',
  updated_at: '2026-09-14T06:05:00Z',
  last_login_at: null,
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(usersApi, 'listUsers').mockResolvedValue([adminUser, operatorUser])
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

describe('UsersView', () => {
  it('shows users, roles, status and operational metadata', async () => {
    const wrapper = mount(UsersView)
    await flushPromises()

    expect(wrapper.text()).toContain('admin')
    expect(wrapper.text()).toContain('operator')
    expect(wrapper.text()).toContain('Последний вход: Никогда')
    expect(usersApi.listUsers).toHaveBeenCalledOnce()
  })

  it('creates a user without retaining the initial password in the form', async () => {
    const create = vi.spyOn(usersApi, 'createUser').mockResolvedValue({
      ...operatorUser,
      id: 3,
      username: 'viewer-two',
      role: 'viewer',
    })
    const wrapper = mount(UsersView)
    await flushPromises()

    await wrapper.get('input[name="username"]').setValue(' viewer-two ')
    await wrapper.get('input[name="password"]').setValue('viewer-password-123')
    await wrapper.get('select[name="role"]').setValue('viewer')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(create).toHaveBeenCalledWith({
      username: 'viewer-two',
      password: 'viewer-password-123',
      role: 'viewer',
    })
    expect((wrapper.get('input[name="password"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('Пользователь viewer-two создан.')
  })

  it('requires confirmation and persists role/status changes', async () => {
    const update = vi.spyOn(usersApi, 'updateUser').mockResolvedValue({
      ...operatorUser,
      role: 'viewer',
      is_active: false,
    })
    const wrapper = mount(UsersView)
    await flushPromises()

    const row = wrapper
      .findAll('.user-row')
      .find((item) => item.get('.identity strong').text() === 'operator')
    expect(row).toBeTruthy()
    await row!.get('select').setValue('viewer')
    await row!.get('input[type="checkbox"]').setValue(false)
    const saveButton = row!.findAll('button').find((button) => button.text() === 'Сохранить доступ')
    await saveButton!.trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledOnce()
    expect(update).toHaveBeenCalledWith(2, { role: 'viewer', is_active: false })
    expect(wrapper.text()).toContain('Доступ пользователя operator обновлён.')
  })

  it('resets password only after validation and confirmation', async () => {
    const update = vi.spyOn(usersApi, 'updateUser').mockResolvedValue(operatorUser)
    const wrapper = mount(UsersView)
    await flushPromises()

    const row = wrapper
      .findAll('.user-row')
      .find((item) => item.get('.identity strong').text() === 'operator')
    expect(row).toBeTruthy()
    const password = row!.get('input[type="password"]')
    const reset = row!.findAll('button').find((button) => button.text() === 'Сменить пароль')

    await password.setValue('short')
    await reset!.trigger('click')
    expect(update).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('не менее 12 символов')

    await password.setValue('new-operator-password-456')
    await reset!.trigger('click')
    await flushPromises()

    expect(update).toHaveBeenCalledWith(2, { password: 'new-operator-password-456' })
    expect((password.element as HTMLInputElement).value).toBe('')
  })

  it('shows a safe load error state', async () => {
    vi.spyOn(usersApi, 'listUsers').mockRejectedValue(new Error('boom'))
    const wrapper = mount(UsersView)
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('Не удалось загрузить пользователей.')
  })
})
