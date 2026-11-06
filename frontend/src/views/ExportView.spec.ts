import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import * as exportsApi from '@/api/exports'
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
  vi.spyOn(exportsApi, 'listHarborProfiles').mockResolvedValue({
    items: [
      {
        id: 'default',
        name: 'Default Harbor',
        url: 'https://harbor.local',
        is_default: true,
      },
      {
        id: 'profile-b',
        name: 'Harbor B',
        url: 'https://harbor-b.local',
        is_default: false,
      },
    ],
  })
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
    expect(wrapper.get('#export-harbor-profile').element).toBeInstanceOf(HTMLSelectElement)
    expect(wrapper.get('#export-harbor-profile').element).toHaveProperty('value', 'default')
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

  it('uses native ticket download from a completed Nexus SOURCE operation', async () => {
    mockHappyPath()
    vi.mocked(exportsApi.listHarborProfiles).mockResolvedValue({
      items: [{
        id: 'nexus-profile', name: 'Nexus hosted',
        url: 'https://nexus.example.test',
        registry_type: 'nexus', is_default: true,
      }],
    })
    const inventory = vi.spyOn(exportsApi, 'getNexusInventory').mockResolvedValue({
      available: false, items: [], message: 'REST inventory forbidden',
    })
    vi.spyOn(exportsApi, 'listNexusRepositories').mockResolvedValue({
      available: false, items: [], message: 'Docker catalog forbidden', truncated: false,
    })
    vi.spyOn(exportsApi, 'listNexusTags').mockResolvedValue({
      available: false, items: [], message: 'Docker tags forbidden', truncated: false,
    })
    vi.spyOn(exportsApi, 'inspectExactNexusImage').mockResolvedValue({
      repository: 'team/app', reference: 'v1', digest: DIGEST, media_type: null,
    })
    vi.mocked(exportsApi.previewExport).mockResolvedValue({
      artifacts: [{
        kind: 'container-image', project: 'team',
        repository: 'app', reference: 'v1',
        source_digest: DIGEST, size_bytes: null,
      }],
      estimated_payload_bytes: null,
    })
    const mint = vi.spyOn(exportsApi, 'createExportDownloadTicket').mockResolvedValue({
      download_url: '/api/exports/42/download', expires_in_seconds: 120,
    })
    const navigate = vi.spyOn(exportsApi, 'navigateToExportDownload').mockImplementation(() => {})
    const harborProjects = vi.spyOn(exportsApi, 'listHarborProjects')
    useRuntimeStore(pinia).setContour('SOURCE')
    const wrapper = mount(ExportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()
    expect(inventory).toHaveBeenCalledWith('nexus-profile')
    await wrapper.get('input[placeholder="team/application"]').setValue('team/app')
    await wrapper.get('input[placeholder="1.0.0"]').setValue('v1')
    await wrapper.get('form.nexus-exact-form').trigger('submit')
    await flushPromises()
    await button(wrapper, 'Добавить образ в выбор').trigger('click')
    await button(wrapper, 'Проверить выбранное').trigger('click')
    await flushPromises()
    await button(wrapper, 'Запустить экспорт').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Bundle готов к физическому переносу')
    await button(wrapper, 'Скачать bundle').trigger('click')
    await flushPromises()
    expect(mint).toHaveBeenCalledExactlyOnceWith(42)
    expect(navigate).toHaveBeenCalledExactlyOnceWith({
      download_url: '/api/exports/42/download', expires_in_seconds: 120,
    }, 42)
    expect(harborProjects).not.toHaveBeenCalled()
    wrapper.unmount()
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
    expect(artifactsSpy).toHaveBeenLastCalledWith('team', 'apps/demo', 1, 25, '1.0', 'default')
    wrapper.unmount()
  })

  it('routes Nexus exact-reference inspection without calling Harbor browse APIs', async () => {
    vi.spyOn(exportsApi, 'listHarborProfiles').mockResolvedValue({
      items: [
        {
          id: 'nexus-profile',
          name: 'Regional Nexus',
          url: 'https://nexus.example.test',
          registry_type: 'nexus',
          is_default: false,
        },
      ],
    })
    const helmCharts = vi.spyOn(exportsApi, 'listNexusHelmCharts').mockResolvedValue({
      available: true, code: null, message: null,
      items: [{ name: 'chart-demo', version: '1.0.0', digest: 'a'.repeat(64) }],
    })
    const inventory = vi.spyOn(exportsApi, 'getNexusInventory').mockResolvedValue({
      available: true,
      message: null,
      items: [
        { name: 'docker-source', format: 'docker', kind: 'hosted' },
        { name: 'docker-proxy', format: 'docker', kind: 'proxy' },
        { name: 'helm-team', format: 'helm', kind: 'hosted' },
      ],
    })
    const listNexusRepositories = vi.spyOn(exportsApi, 'listNexusRepositories').mockResolvedValue({
      available: true, items: ['0702/centos7', 'jaegertracing/jaeger'],
      message: null, truncated: false,
    })
    const listNexusTags = vi.spyOn(exportsApi, 'listNexusTags').mockResolvedValue({
      available: true, items: ['7.9.2009', 'latest'],
      message: null, truncated: false,
    })
    const connection = vi.spyOn(exportsApi, 'getHarborConnection')
    const projects = vi.spyOn(exportsApi, 'listHarborProjects')
    const repositories = vi.spyOn(exportsApi, 'listHarborRepositories')
    const artifacts = vi.spyOn(exportsApi, 'listHarborArtifacts')
    const inspect = vi.spyOn(exportsApi, 'inspectExactNexusImage').mockResolvedValue({
      repository: '0702/centos7',
      reference: '7.9.2009',
      digest: DIGEST,
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

    expect(wrapper.get('#export-harbor-profile').text()).toContain('Nexus · Docker v2')
    expect(inventory).toHaveBeenCalledWith('nexus-profile')
    expect(wrapper.get('.nexus-inventory').text()).toContain('docker-source')
    expect(wrapper.get('.nexus-inventory').text()).toContain('docker-proxy')
    expect(wrapper.get('.nexus-inventory').text()).toContain('helm-team')
    expect(wrapper.get('.nexus-inventory').text()).toContain('Helm HTTP (.tgz)')
    await button(wrapper, 'Показать charts').trigger('click')
    await flushPromises()
    expect(helmCharts).toHaveBeenCalledWith('nexus-profile', 'helm-team')
    expect(wrapper.text()).toContain('chart-demo')
    await button(wrapper, 'Добавить Helm chart').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('1 выбрано')
    expect(wrapper.text()).toContain('TARGET требует отдельной проверки прав')
    expect(wrapper.text()).toContain('Helm OCI не используются.')
    expect(wrapper.find('input[role="combobox"][aria-label="Проект Harbor"]').exists()).toBe(false)
    expect(connection).not.toHaveBeenCalled()
    expect(projects).not.toHaveBeenCalled()
    expect(repositories).not.toHaveBeenCalled()
    expect(artifacts).not.toHaveBeenCalled()

    await button(wrapper, 'Найти репозитории').trigger('click')
    await flushPromises()
    expect(listNexusRepositories).toHaveBeenCalledWith('nexus-profile', '')
    expect(wrapper.text()).toContain('jaegertracing/jaeger')
    await wrapper.get('input[placeholder="team/application"]').setValue('0702/centos7')
    await button(wrapper, 'Найти теги').trigger('click')
    await flushPromises()
    expect(listNexusTags).toHaveBeenCalledWith('nexus-profile', '0702/centos7', '')
    expect(wrapper.text()).toContain('latest')
    await wrapper.get('input[placeholder="1.0.0"]').setValue('7.9.2009')
    await wrapper.get('form.nexus-exact-form').trigger('submit')
    await flushPromises()
    expect(inspect).toHaveBeenCalledWith('nexus-profile', '0702/centos7', '7.9.2009')
    await button(wrapper, 'Добавить образ в выбор').trigger('click')
    expect(wrapper.text()).toContain('2 выбрано')
    expect(projects).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('browses a named Helm repository when inventory is forbidden; denied peer does not block it', async () => {
    vi.spyOn(exportsApi, 'listHarborProfiles').mockResolvedValue({
      items: [{
        id: 'nexus-profile',
        name: 'Regional Nexus',
        url: 'https://nexus.example.test',
        registry_type: 'nexus',
        is_default: false,
      }],
    })
    vi.spyOn(exportsApi, 'getNexusInventory').mockResolvedValue({
      available: false, items: [], message: 'Нет прав на список репозиториев Nexus',
    })
    vi.spyOn(exportsApi, 'listNexusRepositories').mockResolvedValue({
      available: false, items: [], message: 'Каталог Docker закрыт', truncated: false,
    })
    const helmCharts = vi.spyOn(exportsApi, 'listNexusHelmCharts')
      .mockResolvedValueOnce({
        available: false, items: [], code: 'helm_repository_denied',
        message: 'Нет прав на чтение Helm repository',
      })
      .mockResolvedValueOnce({
        available: true, code: null, message: null,
        items: [{ name: 'team-chart', version: '2.0.0', digest: 'b'.repeat(64) }],
      })
    const harborProjects = vi.spyOn(exportsApi, 'listHarborProjects')
    useRuntimeStore(pinia).setContour('SOURCE')
    const wrapper = mount(ExportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Нет прав на список репозиториев Nexus')

    const input = wrapper.get('#nexus-helm-repository')
    const open = button(wrapper, 'Проверить Helm-репозиторий')
    expect(open.attributes('disabled')).toBeDefined()
    await input.setValue('../unsafe')
    expect(open.attributes('disabled')).toBeDefined()
    expect(helmCharts).not.toHaveBeenCalled()

    await input.setValue('helm-private')
    await wrapper.get('form.nexus-helm-manual').trigger('submit')
    await flushPromises()
    expect(helmCharts).toHaveBeenNthCalledWith(1, 'nexus-profile', 'helm-private')
    expect(wrapper.text()).toContain('Нет прав на чтение Helm repository')
    expect(wrapper.findAll('.nexus-helm-catalog li')).toHaveLength(0)

    await input.setValue('helm-team')
    await wrapper.get('form.nexus-helm-manual').trigger('submit')
    await flushPromises()
    expect(helmCharts).toHaveBeenNthCalledWith(2, 'nexus-profile', 'helm-team')
    expect(wrapper.text()).not.toContain('Нет прав на чтение Helm repository')
    expect(wrapper.text()).toContain('team-chart')
    await button(wrapper, 'Добавить Helm chart').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('1 выбрано')
    expect(harborProjects).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('switches only to the configured Docker connector for each Nexus repository', async () => {
    const profiles = [
      {
        id: 'docker-a', name: 'Docker hosted', url: 'https://hosted.example.test',
        registry_type: 'nexus' as const, is_default: false,
        nexus_search_url: 'https://nexus-ui.example.test',
        nexus_search_repository: 'docker-hosted',
      },
      {
        id: 'docker-b', name: 'Docker group', url: 'https://group.example.test',
        registry_type: 'nexus' as const, is_default: false,
        nexus_search_url: 'https://nexus-ui.example.test',
        nexus_search_repository: 'docker-group',
      },
      {
        id: 'unrelated', name: 'Unrelated Nexus', url: 'https://other.example.test',
        registry_type: 'nexus' as const, is_default: false,
        nexus_search_url: 'https://other-ui.example.test',
        nexus_search_repository: 'docker-unmapped',
      },
    ]
    vi.spyOn(exportsApi, 'listHarborProfiles').mockResolvedValue({ items: profiles })
    const inventory = vi.spyOn(exportsApi, 'getNexusInventory').mockResolvedValue({
      available: true, message: null, items: [
        { name: 'docker-hosted', format: 'docker', kind: 'hosted' },
        { name: 'docker-group', format: 'docker', kind: 'group' },
        { name: 'docker-unmapped', format: 'docker', kind: 'proxy' },
        { name: 'helm-team', format: 'helm', kind: 'hosted' },
      ],
    })
    vi.spyOn(exportsApi, 'listNexusRepositories').mockResolvedValue({
      available: false, items: [], message: '403', truncated: false,
    })
    const inspect = vi.spyOn(exportsApi, 'inspectExactNexusImage').mockResolvedValue({
      repository: 'team/app', reference: 'v1', digest: DIGEST,
    })
    const harborProjects = vi.spyOn(exportsApi, 'listHarborProjects')
    useRuntimeStore(pinia).setContour('SOURCE')
    const wrapper = mount(ExportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()
    expect(wrapper.get('#export-harbor-profile').element).toHaveProperty('value', 'docker-a')
    expect(wrapper.text()).toContain('Для этого Docker repository отдельный connector-профиль ещё не настроен')
    const switchButton = button(wrapper, 'Выбрать Docker connector')
    await switchButton.trigger('click')
    await flushPromises()
    expect(wrapper.get('#export-harbor-profile').element).toHaveProperty('value', 'docker-b')
    expect(inventory).toHaveBeenCalledWith('docker-b')
    expect(wrapper.text()).toContain('Docker connector выбран')
    await wrapper.get('input[placeholder="team/application"]').setValue('team/app')
    await wrapper.get('input[placeholder="1.0.0"]').setValue('v1')
    await wrapper.get('form.nexus-exact-form').trigger('submit')
    await flushPromises()
    expect(inspect).toHaveBeenCalledWith('docker-b', 'team/app', 'v1')
    await button(wrapper, 'Добавить образ в выбор').trigger('click')
    expect(wrapper.text()).toContain('1 выбрано')
    expect(button(wrapper, 'Выбрать Docker connector').attributes('disabled')).toBeDefined()
    expect(harborProjects).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('does not show a stale inspected digest after Nexus repository or tag changes', async () => {
    vi.spyOn(exportsApi, 'listHarborProfiles').mockResolvedValue({
      items: [{
        id: 'nexus-profile', name: 'Nexus', url: 'https://nexus.example.test',
        registry_type: 'nexus', is_default: false,
      }],
    })
    vi.spyOn(exportsApi, 'getNexusInventory').mockResolvedValue({
      available: false, items: [], message: 'Нет доступа к inventory',
    })
    vi.spyOn(exportsApi, 'listNexusRepositories').mockResolvedValue({
      available: false, items: [], message: 'Каталог закрыт', truncated: false,
    })
    vi.spyOn(exportsApi, 'listNexusTags').mockResolvedValue({
      available: false, items: [], message: 'Теги закрыты', truncated: false,
    })
    let resolveInspection: ((value: exportsApi.ExactNexusInspection) => void) | undefined
    const inspect = vi.spyOn(exportsApi, 'inspectExactNexusImage').mockImplementation(
      () => new Promise((resolve) => { resolveInspection = resolve }),
    )
    const harborProjects = vi.spyOn(exportsApi, 'listHarborProjects')
    useRuntimeStore(pinia).setContour('SOURCE')
    const wrapper = mount(ExportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()
    await wrapper.get('input[placeholder="team/application"]').setValue('team/app')
    await wrapper.get('input[placeholder="1.0.0"]').setValue('v1')
    await wrapper.get('form.nexus-exact-form').trigger('submit')
    expect(inspect).toHaveBeenCalledWith('nexus-profile', 'team/app', 'v1')
    expect(resolveInspection).toBeDefined()

    // The operator changes the tag while the previous async digest probe is in flight.
    await wrapper.get('input[placeholder="1.0.0"]').setValue('v2')
    resolveInspection?.({
      repository: 'team/app', reference: 'v1', digest: DIGEST, media_type: null,
    })
    await flushPromises()
    expect(wrapper.text()).not.toContain('Найден team/app:v1')
    expect(wrapper.findAll('button').some((item) =>
      item.text().includes('Добавить образ в выбор'),
    )).toBe(false)
    expect(harborProjects).not.toHaveBeenCalled()
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