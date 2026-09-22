import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import * as exportsApi from '@/api/exports'
import * as harborProfilesApi from '@/api/harborProfiles'
import { useAuthStore } from '@/stores/auth'
import { useRuntimeStore } from '@/stores/runtime'

import ExportView from './ExportView.vue'

const DIGEST = `sha256:${'a'.repeat(64)}`
let pinia = createPinia()

function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button').find((item) => item.text().includes(label))
  if (!found) throw new Error(`Button not found: ${label}`)
  return found
}

async function chooseOption(wrapper: VueWrapper, label: string, value: string): Promise<void> {
  const input = wrapper.get(`input[role="combobox"][aria-label="${label}"]`)
  await input.trigger('focus')
  const option = wrapper.findAll('[role="option"]').find((item) => item.text().includes(value))
  if (!option) throw new Error(`Option not found: ${value}`)
  await option.trigger('mousedown')
  await flushPromises()
}

async function selectVersion(wrapper: VueWrapper, value: string): Promise<void> {
  const row = wrapper.findAll('tbody tr').find((item) => item.text().includes(value))
  if (!row) throw new Error(`Version row not found: ${value}`)
  await row.get('input[type="checkbox"]').setValue(true)
  await flushPromises()
}

function mockHappyPath(): void {
  vi.spyOn(exportsApi, 'getHarborConnection').mockResolvedValue({
    connected: true,
    version: '2.14.0',
    auth_mode: 'db_auth',
  })
  vi.spyOn(exportsApi, 'listHarborProjects').mockResolvedValue({
    pagination: { page: 1, page_size: 25, total: 1 },
    items: [{ name: 'team', public: false }],
  })
  vi.spyOn(exportsApi, 'listHarborRepositories').mockResolvedValue({
    pagination: { page: 1, page_size: 25, total: 1 },
    items: [{ name: 'apps/demo', artifact_count: 1, pull_count: 0 }],
  })
  vi.spyOn(exportsApi, 'listHarborArtifacts').mockResolvedValue({
    pagination: { page: 1, page_size: 25, total: 1 },
    items: [
      {
        kind: 'container-image',
        project: 'team',
        repository: 'apps/demo',
        references: ['1.0.0', '1.0.1', 'latest'],
        digest: DIGEST,
        size: 4096,
        pushed_at: null,
        media_type: null,
        artifact_type: null,
      },
    ],
  })
  vi.spyOn(exportsApi, 'previewExport').mockResolvedValue({
    artifacts: [
      {
        kind: 'container-image',
        project: 'team',
        repository: 'apps/demo',
        reference: '1.0.0',
        source_digest: DIGEST,
        size_bytes: 4096,
      },
    ],
    estimated_payload_bytes: 4096,
  })
  vi.spyOn(exportsApi, 'startExport').mockResolvedValue({
    operation_id: 42,
    delivery_id: 'DELIVERY-20260911-ABCDEF',
    status: 'CREATED',
  })
  vi.spyOn(exportsApi, 'getOperation').mockResolvedValue({
    id: 42,
    delivery_id: 'DELIVERY-20260911-ABCDEF',
    type: 'EXPORT',
    status: 'COMPLETED',
    actor_username: 'operator',
    comment: null,
    started_at: '2026-09-11T20:00:00Z',
    finished_at: '2026-09-11T20:01:00Z',
    error_code: null,
    error_message: null,
    cancel_requested: false,
    bundle: {
      filename: 'DELIVERY-20260911-ABCDEF.htp.tar.gz',
      size_bytes: 8192,
      sha256: 'b'.repeat(64),
    },
    progress: {
      total_artifacts: 1,
      completed_artifacts: 1,
      running_artifacts: 0,
      successful_artifacts: 1,
      failed_artifacts: 0,
      skipped_artifacts: 0,
      conflict_artifacts: 0,
      progress_current: 1,
      progress_total: 1,
      current_phase: 'COMPLETED',
      running_artifact_ids: [],
    },
    artifacts: [
      {
        id: 1,
        artifact_type: 'container-image',
        repository: 'team/apps/demo',
        name: null,
        reference: '1.0.0',
        version: null,
        source_digest: DIGEST,
        target_digest: null,
        status: 'VERIFIED',
        error_code: null,
        error_message: null,
        size_bytes: 4096,
        started_at: '2026-09-11T20:00:01Z',
        finished_at: '2026-09-11T20:01:00Z',
      },
    ],
  })
  vi.spyOn(exportsApi, 'getExportBundle').mockResolvedValue({
    operation_id: 42,
    delivery_id: 'DELIVERY-20260911-ABCDEF',
    archive_name: 'DELIVERY-20260911-ABCDEF.htp.tar.gz',
    archive_size: 8192,
    sha256: 'b'.repeat(64),
    download_url: '/api/exports/42/download',
  })
}

beforeEach(() => {
  sessionStorage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
  vi.spyOn(harborProfilesApi, 'listHarborProfiles').mockResolvedValue([{
  id: 'default',
  name: 'Default',
  url: 'https://harbor.local',
  username: 'svc-transfer',
  verify_tls: true,
  enabled: true,
  credential_configured: true,
  custom_ca_configured: false,
  legacy_default: true,
}])
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  sessionStorage.clear()
})

describe('SOURCE export wizard view', () => {
  it('renders all exact Harbor references and completes export with selected version', async () => {
    mockHappyPath()
    const runtime = useRuntimeStore(pinia)
    runtime.setContour('SOURCE')
    const wrapper = mount(ExportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    expect(wrapper.get('h1').text()).toContain('Отправка артефактов')
    expect(wrapper.get('input[role="combobox"][aria-label="Проект Harbor"]').exists()).toBe(true)

    await chooseOption(wrapper, 'Проект Harbor', 'team')
    await chooseOption(wrapper, 'Репозиторий Harbor', 'apps/demo')

    await selectVersion(wrapper, '1.0.0')
    expect(wrapper.text()).toContain('1.0.0')
    expect(wrapper.text()).toContain('4.00 КиБ')
    await button(wrapper, 'Добавить').trigger('click')
    expect(wrapper.text()).toContain('1 выбрано')

    await button(wrapper, 'Проверить выбранное').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Параметры и финальная проверка')
    expect(wrapper.text()).toContain('sha256:aaaaaaaaaa')
    expect(wrapper.get('#export-comment').attributes('maxlength')).toBe('2000')

    await button(wrapper, 'Запустить экспорт').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Bundle готов к физическому переносу')
    expect(wrapper.text()).toContain('DELIVERY-20260911-ABCDEF.htp.tar.gz')
    expect(wrapper.text()).toContain('bbbbbbbbbbbbbbbb')
    expect(button(wrapper, 'Скачать bundle').attributes('type')).toBe('button')
    expect(button(wrapper, 'Скачать `.sha256`').attributes('type')).toBe('button')
  })

  it('lets an admin generate missing signing identity and continue export', async () => {
    mockHappyPath()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(exportsApi.startExport)
      .mockRejectedValueOnce({
        isAxiosError: true,
        response: {
          status: 409,
          data: {
            detail: {
              code: 'bundle_signing_key_not_configured',
              message: 'SOURCE signing identity не настроена',
            },
          },
        },
      })
      .mockResolvedValueOnce({
        operation_id: 42,
        delivery_id: 'DELIVERY-20260911-ABCDEF',
        status: 'CREATED',
      })
    const generate = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: { action: 'generated', fingerprint: `sha256:${'c'.repeat(64)}` },
      status: 201,
      statusText: 'Created',
      headers: {},
      config: { headers: {} },
    })

    const runtime = useRuntimeStore(pinia)
    runtime.setContour('SOURCE')
    const auth = useAuthStore(pinia)
    auth.user = {
      id: 1,
      username: 'admin',
      role: 'admin',
      is_active: true,
    }

    const wrapper = mount(ExportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    await chooseOption(wrapper, 'Проект Harbor', 'team')
    await chooseOption(wrapper, 'Репозиторий Harbor', 'apps/demo')
    await selectVersion(wrapper, '1.0.0')
    await button(wrapper, 'Добавить').trigger('click')
    await button(wrapper, 'Проверить выбранное').trigger('click')
    await flushPromises()

    await button(wrapper, 'Запустить экспорт').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('SOURCE signing identity не настроена')
    await button(wrapper, 'Создать identity и продолжить экспорт').trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledOnce()
    expect(generate).toHaveBeenCalledWith('/settings/keys/signing/generate')
    expect(exportsApi.startExport).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('Bundle готов к физическому переносу')
  })

  it('shows unknown OCI references for diagnostics without export controls', async () => {
    mockHappyPath()
    vi.mocked(exportsApi.listHarborArtifacts).mockResolvedValue({
      pagination: { page: 1, page_size: 25, total: 1 },
      items: [
        {
          kind: 'unknown-oci',
          project: 'team',
          repository: 'apps/demo',
          references: ['release-2026.09', 'latest'],
          digest: DIGEST,
          size: 2048,
          pushed_at: null,
          media_type: 'application/vnd.example.unknown',
          artifact_type: 'application/vnd.example.unknown',
        },
      ],
    })
    const runtime = useRuntimeStore(pinia)
    runtime.setContour('SOURCE')
    const wrapper = mount(ExportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    await chooseOption(wrapper, 'Проект Harbor', 'team')
    await chooseOption(wrapper, 'Репозиторий Harbor', 'apps/demo')

    expect(wrapper.text()).toContain('OCI (не поддерживается)')
    expect(wrapper.text()).toContain('Не поддерживается export v1')
    expect(wrapper.text()).toContain('release-2026.09')
    expect(wrapper.text()).toContain('latest')
    expect(wrapper.findAll('[role="option"]')).toHaveLength(0)
    expect(wrapper.text()).toContain('0 выбрано')
    expect(button(wrapper, 'Проверить выбранное').attributes('disabled')).toBeDefined()
  })

  it('filters artifacts automatically after debounce in the compact selector', async () => {
    vi.useFakeTimers()
    mockHappyPath()
    const runtime = useRuntimeStore(pinia)
    runtime.setContour('SOURCE')
    const wrapper = mount(ExportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    await chooseOption(wrapper, 'Проект Harbor', 'team')
    await chooseOption(wrapper, 'Репозиторий Harbor', 'apps/demo')
    const artifactsSpy = vi.mocked(exportsApi.listHarborArtifacts)
    artifactsSpy.mockClear()
    const input = wrapper.get('input[type="search"][aria-label="Версия / tag"]')

    await input.setValue('1.0')
    await vi.advanceTimersByTimeAsync(299)
    expect(artifactsSpy).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()

    expect(artifactsSpy).toHaveBeenCalledTimes(1)
    expect(artifactsSpy).toHaveBeenLastCalledWith('team', 'apps/demo', 1, 25, '1.0')
    wrapper.unmount()
  })

  it('renders an actionable TARGET fallback and does not browse Harbor', async () => {
    const connection = vi.spyOn(exportsApi, 'getHarborConnection')
    const runtime = useRuntimeStore(pinia)
    runtime.setContour('TARGET')
    const wrapper = mount(ExportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Экспорт доступен только в контуре SOURCE')
    expect(connection).not.toHaveBeenCalled()
    expect(wrapper.find('input[role="combobox"][aria-label="Проект Harbor"]').exists()).toBe(false)
  })
})