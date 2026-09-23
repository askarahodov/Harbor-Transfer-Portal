import { flushPromises, mount } from '@vue/test-utils'
import type { AxiosResponse } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'

import HarborProfilesPanel from './HarborProfilesPanel.vue'

type Profile = {
  id: string
  name: string
  url: string
  username: string | null
  verify_tls: boolean
  enabled: boolean
  credential_configured: boolean
  custom_ca_configured: boolean
  is_default: boolean
  is_active: boolean
}

function response<T>(data: T, status = 200): AxiosResponse<T> {
  return {
    data,
    status,
    statusText: status === 201 ? 'Created' : 'OK',
    headers: {},
    config: { headers: {} } as AxiosResponse<T>['config'],
  }
}

const defaultProfile: Profile = {
  id: 'default',
  name: 'Default Harbor',
  url: 'https://harbor-a.local',
  username: 'svc-a',
  verify_tls: true,
  enabled: true,
  credential_configured: true,
  custom_ca_configured: false,
  is_default: true,
  is_active: true,
}

const secondProfile: Profile = {
  id: 'b'.repeat(32),
  name: 'Harbor DC-2',
  url: 'https://harbor-b.local',
  username: 'svc-b',
  verify_tls: true,
  enabled: true,
  credential_configured: true,
  custom_ca_configured: false,
  is_default: false,
  is_active: false,
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('HarborProfilesPanel', () => {
  it('shows legacy fallback profile and switches by a simple selector', async () => {
    const get = vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(response({ items: [defaultProfile, secondProfile] }))
      .mockResolvedValueOnce(
        response({
          items: [
            { ...defaultProfile, is_active: false },
            { ...secondProfile, is_active: true },
          ],
        }),
      )
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue(
      response({ ...secondProfile, is_active: true }),
    )

    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()

    expect(wrapper.text()).toContain('Legacy fallback · Default Harbor')
    await wrapper.get('#active-harbor-profile').setValue(secondProfile.id)
    const activate = wrapper.findAll('button').find((button) => button.text() === 'Сделать fallback')
    if (!activate) throw new Error('Activate button not found')
    await activate.trigger('click')
    await flushPromises()

    expect(put).toHaveBeenCalledWith(
      `/settings/harbor/profiles/${secondProfile.id}/activate`,
    )
    expect(get).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('Legacy fallback · Harbor DC-2')
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })

  it('creates a profile and stores an optional credential separately', async () => {
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(response({ items: [defaultProfile] }))
      .mockResolvedValueOnce(response({ items: [defaultProfile, secondProfile] }))
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue(response(secondProfile, 201))
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue(
      response({ changed_fields: ['credential'] }),
    )

    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()

    await wrapper.find('input[placeholder="Harbor DC-2"]').setValue('Harbor DC-2')
    await wrapper.find('input[placeholder="https://harbor-dc2.local"]').setValue(
      'https://harbor-b.local',
    )
    const inputs = wrapper.findAll('input')
    const user = inputs.find((input) => input.attributes('autocomplete') === 'username')
    const credential = inputs.find(
      (input) => input.attributes('autocomplete') === 'new-password',
    )
    if (!user || !credential) throw new Error('Profile inputs not found')
    await user.setValue('svc-b')
    await credential.setValue('profile-secret-value')
    await wrapper.get('.create-form').trigger('submit')
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/settings/harbor/profiles', {
      name: 'Harbor DC-2',
      url: 'https://harbor-b.local',
      username: 'svc-b',
      verify_tls: true,
      enabled: true,
    })
    expect(put).toHaveBeenCalledWith(
      `/settings/harbor/profiles/${secondProfile.id}/credential`,
      { secret: 'profile-secret-value' },
    )
    expect(wrapper.text()).not.toContain('profile-secret-value')
  })

  it('edits an additional profile without reading the existing credential', async () => {
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(response({ items: [defaultProfile, secondProfile] }))
      .mockResolvedValueOnce(
        response({
          items: [
            defaultProfile,
            { ...secondProfile, name: 'Harbor DC-2 updated', url: 'https://harbor-b-new.local' },
          ],
        }),
      )
    const patch = vi.spyOn(apiClient, 'patch').mockResolvedValue(
      response({
        ...secondProfile,
        name: 'Harbor DC-2 updated',
        url: 'https://harbor-b-new.local',
      }),
    )
    const put = vi.spyOn(apiClient, 'put')

    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()

    const edit = wrapper.findAll('button').find((button) => button.text() === 'Изменить')
    if (!edit) throw new Error('Edit button not found')
    await edit.trigger('click')

    expect(wrapper.text()).toContain('Изменить Harbor profile')
    const credential = wrapper.findAll('input').find(
      (input) => input.attributes('autocomplete') === 'new-password',
    )
    if (!credential) throw new Error('Credential input not found')
    expect(credential.element).toHaveProperty('value', '')

    await wrapper.find('input[placeholder="Harbor DC-2"]').setValue('Harbor DC-2 updated')
    await wrapper.find('input[placeholder="https://harbor-dc2.local"]').setValue(
      'https://harbor-b-new.local',
    )
    await wrapper.get('.create-form').trigger('submit')
    await flushPromises()

    expect(patch).toHaveBeenCalledWith(
      `/settings/harbor/profiles/${secondProfile.id}`,
      {
        name: 'Harbor DC-2 updated',
        url: 'https://harbor-b-new.local',
        username: 'svc-b',
        verify_tls: true,
        enabled: true,
      },
    )
    expect(put).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Harbor profile обновлён')
  })

  it('tests a profile without exposing credentials', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue(
      response({ items: [defaultProfile, secondProfile] }),
    )
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue(
      response({
        ok: true,
        code: 'harbor_connection_ok',
        message: 'ok',
        version: '2.13.0',
      }),
    )

    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()

    const testButtons = wrapper.findAll('button').filter((button) => button.text() === 'Проверить')
    await testButtons[1]?.trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith(
      `/settings/harbor/profiles/${secondProfile.id}/test`,
    )
    expect(wrapper.text()).toContain('Harbor DC-2: подключение успешно · Harbor 2.13.0')
  })

  it('preserves disabled state while editing instead of enabling implicitly', async () => {
    const disabled = { ...secondProfile, enabled: false }
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(response({ items: [defaultProfile, disabled] }))
      .mockResolvedValueOnce(response({ items: [defaultProfile, disabled] }))
    const patch = vi.spyOn(apiClient, 'patch').mockResolvedValue(response(disabled))

    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()

    const edit = wrapper.findAll('button').find((button) => button.text() === 'Изменить')
    if (!edit) throw new Error('Edit button not found')
    await edit.trigger('click')
    await wrapper.get('.create-form').trigger('submit')
    await flushPromises()

    expect(patch).toHaveBeenCalledWith(
      \`/settings/harbor/profiles/\${secondProfile.id}\`,
      expect.objectContaining({ enabled: false }),
    )
  })

  it('enables and disables additional profiles through the existing PATCH contract', async () => {
    const get = vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(response({ items: [defaultProfile, secondProfile] }))
      .mockResolvedValueOnce(
        response({ items: [defaultProfile, { ...secondProfile, enabled: false }] }),
      )
    const patch = vi.spyOn(apiClient, 'patch').mockResolvedValue(
      response({ ...secondProfile, enabled: false }),
    )

    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()

    const disable = wrapper.findAll('button').find((button) => button.text() === 'Отключить')
    if (!disable) throw new Error('Disable button not found')
    await disable.trigger('click')
    await flushPromises()

    expect(patch).toHaveBeenCalledWith(
      \`/settings/harbor/profiles/\${secondProfile.id}\`,
      { enabled: false },
    )
    expect(get).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('disabled')
    expect(wrapper.text()).toContain('Профиль Harbor DC-2 отключён')
  })

  it('installs a replacement custom CA without reading existing CA material', async () => {
    const withCa = { ...secondProfile, custom_ca_configured: true }
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(response({ items: [defaultProfile, withCa] }))
      .mockResolvedValueOnce(response({ items: [defaultProfile, withCa] }))
    vi.spyOn(apiClient, 'patch').mockResolvedValue(response(withCa))
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue(
      response({ changed_fields: ['custom_ca'] }),
    )

    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()

    const edit = wrapper.findAll('button').find((button) => button.text() === 'Изменить')
    if (!edit) throw new Error('Edit button not found')
    await edit.trigger('click')
    expect(wrapper.text()).toContain('текущий CA не читается обратно')

    const caInput = wrapper.get('input[type="file"]')
    const pem = '-----BEGIN CERTIFICATE-----\\ntest-ca\\n-----END CERTIFICATE-----\\n'
    Object.defineProperty(caInput.element, 'files', {
      configurable: true,
      value: [{ name: 'harbor-ca.pem', text: () => Promise.resolve(pem) }],
    })
    await caInput.trigger('change')
    await wrapper.get('.create-form').trigger('submit')
    await flushPromises()

    expect(put).toHaveBeenCalledWith(
      \`/settings/harbor/profiles/\${secondProfile.id}/ca\`,
      { certificate_pem: pem },
    )
    expect(wrapper.text()).not.toContain(pem)
  })

  it('removes custom CA as a separate server-side action', async () => {
    const withCa = { ...secondProfile, custom_ca_configured: true }
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(response({ items: [defaultProfile, withCa] }))
      .mockResolvedValueOnce(response({ items: [defaultProfile, secondProfile] }))
    const remove = vi.spyOn(apiClient, 'delete').mockResolvedValue(
      response({ changed_fields: ['custom_ca'] }),
    )

    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()

    const removeCa = wrapper.findAll('button').find((button) => button.text() === 'Удалить CA')
    if (!removeCa) throw new Error('Remove CA button not found')
    await removeCa.trigger('click')
    await flushPromises()

    expect(remove).toHaveBeenCalledWith(
      \`/settings/harbor/profiles/\${secondProfile.id}/ca\`,
    )
    expect(wrapper.text()).toContain('Custom CA профиля Harbor DC-2 удалён')
  })

})
