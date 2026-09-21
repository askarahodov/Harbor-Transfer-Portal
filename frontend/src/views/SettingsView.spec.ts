import { flushPromises, mount } from '@vue/test-utils'
import type { AxiosResponse } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import { useRuntimeStore } from '@/stores/runtime'

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
  destination_mapping_revision: 3,
  destination_container_image_project: 'docker-default',
  destination_helm_chart_project: 'helm-default',
  destination_project_mappings: { 'source-a': 'target-a' },
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
    if (url === '/harbor/connection') {
      return Promise.resolve(response({ connected: true, version: '2.13.0', auth_mode: 'basic' }))
    }
    if (url === '/settings/keys') {
      return Promise.resolve(
        response({
          contour: 'SOURCE',
          signing_key: { configured: true, fingerprint: `sha256:${'a'.repeat(64)}` },
          trusted_keys: [],
        }),
      )
    }
    return Promise.resolve(response(safeSettings))
  })
  const wrapper = mount(SettingsView)
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
})

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
    expect(wrapper.get('#destination-image-project').element).toHaveProperty('value', 'docker-default')
    expect(wrapper.get('#destination-helm-project').element).toHaveProperty('value', 'helm-default')
    expect(wrapper.get('#destination-project-mappings').element).toHaveProperty(
      'value',
      'source-a=target-a',
    )
    expect(wrapper.text()).toContain('Revision 3')
    expect(wrapper.text()).toContain('First-run readiness')
    expect(wrapper.text()).toContain('Harbor: доступен')
    expect(wrapper.text()).toContain('Signing identity: готова')
    expect(wrapper.text()).toContain('Trust package: можно скачать')
  })

  it('switches readiness to the live runtime contour without remounting', async () => {
    const runtime = useRuntimeStore()
    runtime.setContour('SOURCE')
    vi.spyOn(apiClient, 'get').mockImplementation((url) => {
      if (url === '/settings/transfer') return Promise.resolve(response(transferSettings))
      if (url === '/harbor/connection') {
        return Promise.resolve(response({ connected: true, version: '2.13.0', auth_mode: 'basic' }))
      }
      if (url === '/settings/keys') {
        return Promise.resolve(
          runtime.contour === 'TARGET'
            ? response({
                contour: 'TARGET',
                signing_key: null,
                trusted_keys: [{ fingerprint: `sha256:${'b'.repeat(64)}`, enabled: true }],
              })
            : response({
                contour: 'SOURCE',
                signing_key: { configured: true, fingerprint: `sha256:${'a'.repeat(64)}` },
                trusted_keys: [],
              }),
        )
      }
      return Promise.resolve(
        response({
          ...safeSettings,
          contour: runtime.contour ?? 'SOURCE',
        }),
      )
    })

    const wrapper = mount(SettingsView)
    await flushPromises()
    expect(wrapper.text()).toContain('Signing identity: готова')
    expect(wrapper.text()).toContain('Trust package: можно скачать')

    runtime.setContour('TARGET')
    await flushPromises()

    expect(wrapper.text()).toContain('TARGET')
    expect(wrapper.text()).toContain('SOURCE trust: 1 active key(s)')
    expect(wrapper.text()).toContain('Import readiness: готов')
    expect(wrapper.text()).not.toContain('Signing identity: готова')
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
      destination_container_image_project: 'docker-default',
      destination_helm_chart_project: 'helm-default',
      destination_project_mappings: { 'source-a': 'target-a' },
    })
    expect(wrapper.text()).toContain('вступит в силу после перезапуска backend')
    expect(wrapper.text()).toContain('operation_max_concurrent')
  })

  it('saves normalized destination defaults and project mappings', async () => {
    const wrapper = await mountSettings()
    const patch = vi.spyOn(apiClient, 'patch').mockResolvedValue(
      response({
        ...transferSettings,
        destination_mapping_revision: 4,
        destination_container_image_project: 'docker-next',
        destination_helm_chart_project: null,
        destination_project_mappings: {
          'source-a': 'target-next',
          'source-b': 'target-b',
        },
      }),
    )

    await wrapper.get('#destination-image-project').setValue(' docker-next ')
    await wrapper.get('#destination-helm-project').setValue('')
    await wrapper.get('#destination-project-mappings').setValue(
      'source-b=target-b\nsource-a=target-next',
    )
    await wrapper.get('.transfer-form').trigger('submit')
    await flushPromises()

    expect(patch).toHaveBeenCalledWith(
      '/settings/transfer',
      expect.objectContaining({
        destination_container_image_project: 'docker-next',
        destination_helm_chart_project: null,
        destination_project_mappings: {
          'source-b': 'target-b',
          'source-a': 'target-next',
        },
      }),
    )
    expect(wrapper.text()).toContain('Revision 4')
    expect(wrapper.text()).toContain('Mapping policy revision: 4')
  })

  it('rejects malformed mapping text before calling the backend', async () => {
    const wrapper = await mountSettings()
    const patch = vi.spyOn(apiClient, 'patch')

    await wrapper.get('#destination-project-mappings').setValue('source-a target-a')
    await wrapper.get('.transfer-form').trigger('submit')
    await flushPromises()

    expect(patch).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('используйте формат source-project=target-project')
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
