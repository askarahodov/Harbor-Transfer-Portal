import { flushPromises, mount } from '@vue/test-utils'
import type { AxiosResponse } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'

import SettingsView from './SettingsView.vue'

const MIB = 1024 ** 2

const safeSettings = {
  contour: 'SOURCE' as const,
  url: 'https://harbor.local',
  username: 'svc-transfer',
  verify_tls: true,
  credential_configured: true,
  custom_ca_configured: false,
}

const transferSettings = {
  import_allow_overwrite: false,
  import_max_upload_bytes: 50 * 1024 ** 3,
  bundle_max_archive_bytes: 50 * 1024 ** 3,
  bundle_max_extracted_bytes: 100 * 1024 ** 3,
  bundle_max_member_count: 100_000,
  operation_disk_reserve_bytes: 512 * MIB,
  operation_max_concurrent: 2,
  effective_operation_max_concurrent: 2,
  restart_required_fields: [] as string[],
}

function response<T>(data: T): AxiosResponse<T> {
  return {
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: { headers: {} } as AxiosResponse<T>['config'],
  }
}

async function mountSettings() {
  vi.spyOn(apiClient, 'get').mockImplementation((url) => {
    if (url === '/settings/transfer') return Promise.resolve(response(transferSettings))
    return Promise.resolve(response(safeSettings))
  })
  const wrapper = mount(SettingsView)
  await flushPromises()
  return wrapper
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Harbor settings view', () => {
  it('shows safe configuration without prefilling the credential', async () => {
    const wrapper = await mountSettings()

    expect(wrapper.get('#harbor-url').element).toHaveProperty('value', 'https://harbor.local')
    expect(wrapper.get('#harbor-username').element).toHaveProperty('value', 'svc-transfer')
    expect(wrapper.get('#harbor-credential').element).toHaveProperty('value', '')
    expect(wrapper.text()).toContain('Текущее значение: настроено')
    expect(wrapper.text()).toContain('SOURCE')
    expect(wrapper.get('#transfer-concurrency').element).toHaveProperty('value', '2')
  })

  it('saves only non-secret Harbor settings through PATCH', async () => {
    const wrapper = await mountSettings()
    const patch = vi.spyOn(apiClient, 'patch').mockResolvedValue(
      response({ ...safeSettings, url: 'https://new.harbor.local' }),
    )

    await wrapper.get('#harbor-url').setValue('https://new.harbor.local')
    await wrapper.find('form:not(.transfer-form)').trigger('submit')
    await flushPromises()

    expect(patch).toHaveBeenCalledWith('/settings/harbor', {
      url: 'https://new.harbor.local',
      username: 'svc-transfer',
      verify_tls: true,
    })
    expect(JSON.stringify(patch.mock.calls[0]?.[1])).not.toContain('credential')
  })

  it('saves transfer policies in bytes and exposes restart-required concurrency', async () => {
    const wrapper = await mountSettings()
    const patch = vi.spyOn(apiClient, 'patch').mockResolvedValue(
      response({
        ...transferSettings,
        import_allow_overwrite: true,
        import_max_upload_bytes: 2048 * MIB,
        operation_max_concurrent: 4,
        effective_operation_max_concurrent: 2,
        restart_required_fields: ['operation_max_concurrent'],
      }),
    )

    await wrapper.get('#transfer-overwrite').setValue(true)
    await wrapper.get('#transfer-upload-mib').setValue(2048)
    await wrapper.get('#transfer-concurrency').setValue(4)
    await wrapper.get('.transfer-form').trigger('submit')
    await flushPromises()

    expect(patch).toHaveBeenCalledWith('/settings/transfer', {
      import_allow_overwrite: true,
      import_max_upload_bytes: 2048 * MIB,
      bundle_max_archive_bytes: 50 * 1024 ** 3,
      bundle_max_extracted_bytes: 100 * 1024 ** 3,
      bundle_max_member_count: 100_000,
      operation_disk_reserve_bytes: 512 * MIB,
      operation_max_concurrent: 4,
    })
    expect(wrapper.text()).toContain('вступит в силу после перезапуска backend')
    expect(wrapper.text()).toContain('operation_max_concurrent')
  })

  it('rotates credential separately and clears the input afterwards', async () => {
    const wrapper = await mountSettings()
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue(
      response({ changed_fields: ['credential'] }),
    )

    await wrapper.get('#harbor-credential').setValue('new-credential-value')
    await wrapper.get('#credential-title').element.parentElement?.querySelector('button')?.click()
    await flushPromises()

    expect(put).toHaveBeenCalledWith('/settings/harbor/credential', {
      secret: 'new-credential-value',
    })
    expect(wrapper.get('#harbor-credential').element).toHaveProperty('value', '')
  })

  it('shows a strong warning only when TLS verification is disabled', async () => {
    const wrapper = await mountSettings()
    expect(wrapper.text()).not.toContain('Проверка TLS отключена явно')

    await wrapper.get('#harbor-verify-tls').setValue(false)
    expect(wrapper.text()).toContain('Проверка TLS отключена явно')
  })
})
