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

  it('creates a profile and stores credential/CA separately without reading secrets back', async () => {
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
    const ca = wrapper.get('#harbor-profile-ca')
    await ca.setValue('-----BEGIN CERTIFICATE-----\nTEST-CA\n-----END CERTIFICATE-----')
    await wrapper.get('.create-form').trigger('submit')
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/settings/harbor/profiles', {
      name: 'Harbor DC-2',
      url: 'https://harbor-b.local',
      username: 'svc-b',
      verify_tls: true,
      registry_type: 'harbor',
      enabled: true,
    })
    expect(put).toHaveBeenCalledWith(
      `/settings/harbor/profiles/${secondProfile.id}/credential`,
      { secret: 'profile-secret-value' },
    )
    expect(put).toHaveBeenCalledWith(
      `/settings/harbor/profiles/${secondProfile.id}/ca`,
      { certificate_pem: '-----BEGIN CERTIFICATE-----\nTEST-CA\n-----END CERTIFICATE-----' },
    )
    expect(wrapper.text()).not.toContain('profile-secret-value')
    expect(wrapper.text()).not.toContain('TEST-CA')
    expect(wrapper.get('#harbor-profile-ca').element).toHaveProperty('value', '')
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

    expect(wrapper.text()).toContain('Изменить профиль реестра')
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
        registry_type: 'harbor',
      },
    )
    expect(put).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Профиль обновлён.')
  })

  it('disables and re-enables an additional profile without changing the legacy fallback', async () => {
    const disabledProfile = { ...secondProfile, enabled: false }
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(response({ items: [defaultProfile, secondProfile] }))
      .mockResolvedValueOnce(response({ items: [defaultProfile, disabledProfile] }))
      .mockResolvedValueOnce(response({ items: [defaultProfile, secondProfile] }))
    const patch = vi.spyOn(apiClient, 'patch')
      .mockResolvedValueOnce(response(disabledProfile))
      .mockResolvedValueOnce(response(secondProfile))

    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()

    const disable = wrapper.findAll('button').find((button) => button.text() === 'Отключить')
    if (!disable) throw new Error('Disable button not found')
    await disable.trigger('click')
    await flushPromises()

    expect(patch).toHaveBeenNthCalledWith(
      1,
      `/settings/harbor/profiles/${secondProfile.id}`,
      { enabled: false },
    )
    expect(wrapper.text()).toContain('disabled')
    expect(wrapper.text()).toContain('больше не предлагается для новых transfer workflows')
    expect(wrapper.text()).toContain('Legacy fallback · Default Harbor')

    const enable = wrapper.findAll('button').find((button) => button.text() === 'Включить')
    if (!enable) throw new Error('Enable button not found')
    await enable.trigger('click')
    await flushPromises()

    expect(patch).toHaveBeenNthCalledWith(
      2,
      `/settings/harbor/profiles/${secondProfile.id}`,
      { enabled: true },
    )
    expect(wrapper.text()).toContain('снова доступен для новых transfer workflows')
  })

  it('removes an additional profile custom CA without exposing its PEM', async () => {
    const withCa = { ...secondProfile, custom_ca_configured: true }
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(response({ items: [defaultProfile, withCa] }))
      .mockResolvedValueOnce(response({ items: [defaultProfile, secondProfile] }))
    const remove = vi.spyOn(apiClient, 'delete').mockResolvedValue(
      response({ changed_fields: ['custom_ca'] }),
    )

    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()

    expect(wrapper.text()).toContain('custom CA есть')
    const removeCa = wrapper.findAll('button').find((button) => button.text() === 'Удалить CA')
    if (!removeCa) throw new Error('Remove CA button not found')
    await removeCa.trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledOnce()
    expect(remove).toHaveBeenCalledWith(
      `/settings/harbor/profiles/${secondProfile.id}/ca`,
    )
    expect(wrapper.text()).toContain('Custom CA для Harbor DC-2 удалён')
    expect(wrapper.text()).not.toContain('BEGIN CERTIFICATE')
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

    const testButtons = wrapper.findAll('button').filter((button) => button.text() === 'Проверить (Harbor API)')
    await testButtons[1]?.trigger('click')
    await flushPromises()

    expect(post).toHaveBeenCalledWith(
      `/settings/harbor/profiles/${secondProfile.id}/test`,
      {},
      { timeout: 15000 },
    )
    expect(wrapper.text()).toContain('Harbor DC-2: подключение успешно · Harbor 2.13.0')
  })

  it('shows validation feedback next to the Add Harbor button instead of silently blocking', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue(response({ items: [defaultProfile] }))
    const post = vi.spyOn(apiClient, 'post')
    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()

    await wrapper.find('input[placeholder="Harbor DC-2"]').setValue('Region Harbor')
    await wrapper.find('input[placeholder="https://harbor-dc2.local"]').setValue('not-a-url')
    expect(wrapper.get('.create-form').attributes()).toHaveProperty('novalidate')

    await wrapper.get('.create-form').trigger('submit')
    await flushPromises()

    expect(post).not.toHaveBeenCalled()
    expect(wrapper.get('.create-form [role="alert"]').text()).toContain('Неверный формат URL')
    wrapper.unmount()
  })

  it('explains protected active fallback and focuses editor when editing profile', async () => {
    const activeAdditional = { ...secondProfile, is_active: true }
    vi.spyOn(apiClient, 'get').mockResolvedValue(
      response({ items: [{ ...defaultProfile, is_active: false }, activeAdditional] }),
    )
    const wrapper = mount(HarborProfilesPanel, { attachTo: document.body })
    await flushPromises()

    expect(wrapper.text()).toContain('сначала')
    expect(wrapper.text()).toContain('выберите другой fallback')
    const disable = wrapper.findAll('button').find((button) => button.text() === 'Отключить')
    const remove = wrapper.findAll('button').find((button) => button.text() === 'Удалить profile')
    expect(disable?.attributes('disabled')).toBeDefined()
    expect(remove?.attributes('disabled')).toBeDefined()

    const edit = wrapper.findAll('button').find((button) => button.text() === 'Изменить')
    if (!edit) throw new Error('Edit button missing')
    await edit.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Изменить профиль реестра')
    expect(document.activeElement).toBe(wrapper.get('input[name="profile-name"]').element)
    wrapper.unmount()
  })

  it('sends modified registry type on save and confirms saved response in editor', async () => {
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(response({ items: [defaultProfile, secondProfile] }))
      .mockResolvedValueOnce(
        response({
          items: [
            defaultProfile,
            { ...secondProfile, registry_type: 'nexus' as const, name: 'Regional Nexus' },
          ],
        }),
      )
    const patch = vi.spyOn(apiClient, 'patch').mockResolvedValue(
      response({
        ...secondProfile,
        registry_type: 'nexus',
        name: 'Regional Nexus',
      }),
    )
    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()
    const edit = wrapper.findAll('button').find((button) => button.text() === 'Изменить')
    if (!edit) throw new Error('Edit button missing')
    await edit.trigger('click')

    await wrapper.get('input[name="profile-name"]').setValue('Regional Nexus')
    await wrapper.get('.create-form select').setValue('nexus')
    await wrapper.get('.create-form').trigger('submit')
    await flushPromises()

    expect(patch).toHaveBeenCalledWith(
      `/settings/harbor/profiles/${secondProfile.id}`,
      {
        name: 'Regional Nexus',
        url: 'https://harbor-b.local',
        username: 'svc-b',
        verify_tls: true,
        registry_type: 'nexus',
        nexus_format: 'docker',
      },
    )
    expect(wrapper.get('.create-form [role="status"]').text()).toContain('Профиль обновлён')
    expect(wrapper.text()).toContain('Regional Nexus')
    wrapper.unmount()
  })

  it('shows server error close to form after failed profile create', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue(response({ items: [defaultProfile] }))
    vi.spyOn(apiClient, 'post').mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 409,
        data: { error: { code: 'harbor_profile_name_conflict', message: 'Имя уже занято' } },
      },
    })
    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()
    await wrapper.get('input[name="profile-name"]').setValue('Default Harbor')
    await wrapper.find('input[placeholder="https://harbor-dc2.local"]').setValue(
      'https://harbor-b.local',
    )
    await wrapper.get('.create-form').trigger('submit')
    await flushPromises()
    const alert = wrapper.get('.create-form [role="alert"]')
    expect(alert.text()).toBeTruthy()
    wrapper.unmount()
  })

  it('only offers Docker Registry v2 to Nexus and never invokes Harbor API', async () => {
    const nexus = {
      ...secondProfile,
      registry_type: 'nexus' as const,
      name: 'Regional Nexus',
      url: 'https://nexus.example.test',
    }
    vi.spyOn(apiClient, 'get').mockResolvedValue(response({ items: [defaultProfile, nexus] }))
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue(response({
      ok: true,
      code: 'registry_connection_ok',
      message: 'Docker v2 connected',
      version: null,
    }))
    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()

    expect(wrapper.text()).toContain('Nexus · Docker v2')
    expect(wrapper.text()).toContain('Проверить Docker Registry v2')
    const nexusRow = wrapper.findAll('.profile-row').find((row) => row.text().includes('Regional Nexus'))
    if (!nexusRow) throw new Error('Nexus row not found')
    expect(nexusRow.text()).not.toContain('Проверить (Harbor API)')
    const testRegistry = nexusRow.findAll('button').find((button) =>
      button.text() === 'Проверить Docker Registry v2',
    )
    if (!testRegistry) throw new Error('Docker v2 button not found')
    await testRegistry.trigger('click')
    await flushPromises()
    expect(post).toHaveBeenCalledWith(
      `/settings/harbor/profiles/${nexus.id}/test-registry`,
      {},
      { timeout: 60000 },
    )
    expect(post.mock.calls.some(([path]) => String(path).endsWith('/test'))).toBe(false)
    wrapper.unmount()
  })

  it('treats legacy profiles without registry_type as Harbor', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue(response({ items: [defaultProfile] }))
    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()
    expect(wrapper.text()).toContain('Harbor')
    expect(wrapper.text()).toContain('Проверить (Harbor API)')
    wrapper.unmount()
  })


  it('creates a dedicated Helm HTTP profile and validates its exact repository', async () => {
    const helm = {
      ...secondProfile,
      name: 'Nexus Helm Team',
      url: 'https://nexus-ui.example.test',
      registry_type: 'nexus' as const,
      nexus_format: 'helm' as const,
      nexus_search_repository: 'helm-team',
      nexus_search_url: null,
      verify_tls: false,
    }
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(response({ items: [defaultProfile] }))
      .mockResolvedValueOnce(response({ items: [defaultProfile, helm] }))
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue(response(helm, 201))
    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()

    await wrapper.get('input[name="profile-name"]').setValue('Nexus Helm Team')
    await wrapper.get('.create-form select').setValue('nexus')
    await wrapper.get('select[aria-label="Формат Nexus repository"]').setValue('helm')
    await wrapper.get('input[type="url"]').setValue('https://nexus-ui.example.test')
    await wrapper.get('.create-form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('Укажите точное имя Helm HTTP repository')
    expect(post).not.toHaveBeenCalled()

    const repository = wrapper.findAll('input').find(item =>
      item.attributes('placeholder') === 'helm-hosted',
    )
    if (!repository) throw new Error('Helm repository field missing')
    await repository.setValue('helm-team')
    const tls = wrapper.findAll('.create-form input[type="checkbox"]').find(item =>
      item.element instanceof HTMLInputElement,
    )
    if (!tls) throw new Error('TLS checkbox missing')
    await tls.setValue(false)
    await wrapper.get('.create-form').trigger('submit')
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/settings/harbor/profiles',
      expect.objectContaining({
        registry_type: 'nexus',
        nexus_format: 'helm',
        url: 'https://nexus-ui.example.test',
        nexus_search_url: null,
        nexus_search_repository: 'helm-team',
        verify_tls: false,
      }),
    )
    expect(wrapper.text()).toContain('Nexus · Helm HTTP')
    wrapper.unmount()
  })

  it('checks a saved Helm profile through HTTP index, never Harbor or Docker v2', async () => {
    const helm = {
      ...secondProfile,
      name: 'Helm Team',
      registry_type: 'nexus' as const,
      nexus_format: 'helm' as const,
      nexus_search_repository: 'helm-team',
      url: 'https://nexus-ui.example.test',
    }
    vi.spyOn(apiClient, 'get').mockResolvedValue(response({ items: [defaultProfile, helm] }))
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue(response({
      ok: true, code: 'helm_repository_connection_ok',
      message: 'Helm HTTP index.yaml доступен', version: null,
    }))
    const wrapper = mount(HarborProfilesPanel)
    await flushPromises()
    const row = wrapper.findAll('.profile-row').find(item => item.text().includes('Helm Team'))
    if (!row) throw new Error('Helm row missing')

    expect(row.text()).toContain('Nexus · Helm HTTP')
    expect(row.text()).not.toContain('Проверить (Harbor API)')
    expect(row.text()).not.toContain('Проверить Docker Registry v2')
    const check = row.findAll('button').find(item => item.text() === 'Проверить Helm HTTP index.yaml')
    if (!check) throw new Error('Helm probe button missing')
    await check.trigger('click')
    await flushPromises()
    expect(post).toHaveBeenCalledWith(
      `/settings/harbor/profiles/${helm.id}/test-registry`, {}, { timeout: 60000 },
    )
    expect(wrapper.text()).toContain('Helm HTTP index.yaml доступен')
    wrapper.unmount()
  })


})
