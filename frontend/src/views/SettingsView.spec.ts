import { flushPromises, mount } from '@vue/test-utils'
import type { AxiosResponse } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'

import SettingsView from './SettingsView.vue'

const GIB = 1024 ** 3

const safeSettings = {
  contour: 'SOURCE' as const,
  url: 'https://harbor.local',
  username: 'svc-transfer',
  verify_tls: true,
  credential_configured: true,
  custom_ca_configured: false,
}

const safeTransferSettings = {
  import_allow_overwrite: false,
  import_max_upload_bytes: 50 * GIB,
  bundle_max_archive_bytes: 50 * GIB,
  bundle_max_extracted_bytes: 100 * GIB,
  bundle_max_member_count: 100_000,
  operation_max_concurrent: 2,
  operation_max_concurrent_active: 2,
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
  vi.spyOn(apiClient, 'get').mockImplementation(async (url) => {
    if (url === '/settings/harbor') return response(safeSettings)
    if (url === '/settings/transfer') return response(safeTransferSettings)
    throw new Error(`Unexpected GET ${url}`)
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
  })

  it('saves only non-secret settings through PATCH', async () => {
    const wrapper = await mountSettings()
    const patch = vi.spyOn(apiClient, 'patch').mockResolvedValue(
      response({ ...safeSettings, url: 'https://new.harbor.local' }),
    )

    await wrapper.get('#harbor-url').setValue('https://new.harbor.local')
    await wrapper.findAll('form')[0].trigger('submit')
    await flushPromises()

    expect(patch).toHaveBeenCalledWith('/settings/harbor', {
      url: 'https://new.harbor.local',
      username: 'svc-transfer',
      verify_tls: true,
    })
    expect(JSON.stringify(patch.mock.calls[0]?.[1])).not.toContain('credential')
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
    expect(wrapper.find('.warning').exists()).toBe(false)

    await wrapper.get('#harbor-verify-tls').setValue(false)
    expect(wrapper.get('.warning').text()).toContain('Проверка TLS отключена явно')
  })

  it('sends only changed transfer fields and reports restart-required concurrency', async () => {
    const wrapper = await mountSettings()
    const patch = vi.spyOn(apiClient, 'patch').mockResolvedValue(
      response({
        ...safeTransferSettings,
        import_allow_overwrite: true,
        operation_max_concurrent: 4,
        operation_max_concurrent_active: 2,
        restart_required_fields: ['operation_max_concurrent'],
      }),
    )

    await wrapper.get('#transfer-overwrite').setValue(true)
    await wrapper.get('#transfer-concurrency').setValue(4)
    await wrapper.findAll('form')[1].trigger('submit')
    await flushPromises()

    expect(patch).toHaveBeenCalledWith('/settings/transfer', {
      import_allow_overwrite: true,
      operation_max_concurrent: 4,
    })
    expect(wrapper.text()).toContain('вступит в силу после рестарта backend')
    expect(wrapper.text()).toContain('Concurrency active: 2')
  })

  it('does not send transfer PATCH when drafts are unchanged', async () => {
    const wrapper = await mountSettings()
    const patch = vi.spyOn(apiClient, 'patch')

    await wrapper.findAll('form')[1].trigger('submit')
    await flushPromises()

    expect(patch).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Transfer policies не изменились.')
  })
})
