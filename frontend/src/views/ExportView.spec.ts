import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as exportsApi from '@/api/exports'
import { useRuntimeStore } from '@/stores/runtime'

import ExportView from './ExportView.vue'

const DIGEST = `sha256:${'a'.repeat(64)}`
let pinia = createPinia()

function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button').find((item) => item.text().includes(label))
  if (!found) throw new Error(`Button not found: ${label}`)
  return found
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
    expect(wrapper.get('#project-search').attributes('type')).toBe('search')

    await button(wrapper, 'team').trigger('click')
    await flushPromises()
    await button(wrapper, 'apps/demo').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('1.0.0')
    expect(wrapper.text()).toContain('1.0.1')
    expect(wrapper.text()).toContain('latest')
    const exactReferences = wrapper.findAll('input[type="checkbox"]')
    expect(exactReferences).toHaveLength(3)
    expect(exactReferences[0]!.element).toHaveProperty('checked', false)
    await exactReferences[0]!.setValue(true)
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

  it('searches automatically after debounce while explicit submit stays immediate', async () => {
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

    const projectsSpy = vi.mocked(exportsApi.listHarborProjects)
    projectsSpy.mockClear()
    const input = wrapper.get('#project-search')

    await input.setValue('rep')
    expect(projectsSpy).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(299)
    expect(projectsSpy).not.toHaveBeenCalled()

    await input.setValue('report')
    await vi.advanceTimersByTimeAsync(299)
    expect(projectsSpy).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()

    expect(projectsSpy).toHaveBeenCalledTimes(1)
    expect(projectsSpy).toHaveBeenLastCalledWith(1, 25, 'report')

    projectsSpy.mockClear()
    await input.setValue('team')
    const projectSearchForm = wrapper.findAll('form.search-row')[0]
    if (!projectSearchForm) throw new Error('Project search form not found')
    await projectSearchForm.trigger('submit')
    await flushPromises()

    expect(projectsSpy).toHaveBeenCalledTimes(1)
    expect(projectsSpy).toHaveBeenLastCalledWith(1, 25, 'team')
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()
    expect(projectsSpy).toHaveBeenCalledTimes(1)

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
    expect(wrapper.find('#project-search').exists()).toBe(false)
  })
})
