import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as historyApi from '@/api/history'
import type { ImportReceipt, Operation, OperationSummary } from '@/api/history'
import * as importsApi from '@/api/imports'
import type { ImportDestinationPlan } from '@/api/imports'
import { useAuthStore } from '@/stores/auth'
import { useRuntimeStore } from '@/stores/runtime'
import HistoryView from '@/views/HistoryView.vue'

const summary: OperationSummary = {
  id: 7,
  delivery_id: 'DELIVERY-7',
  type: 'EXPORT',
  status: 'COMPLETED',
  actor_username: 'operator',
  comment: null,
  retry_of_operation_id: null,
  failure_policy: null,
  created_at: '2026-09-14T05:00:00Z',
  started_at: '2026-09-14T05:01:00Z',
  finished_at: '2026-09-14T05:02:00Z',
  error_code: null,
  error_message: null,
  total_artifacts: 1,
  successful_artifacts: 1,
  failed_artifacts: 0,
  skipped_artifacts: 0,
  conflict_artifacts: 0,
  bundle: { filename: 'delivery.htp.tar.gz', size_bytes: 1234, sha256: 'a'.repeat(64) },
}

const detail: Operation = {
  id: 7,
  delivery_id: 'DELIVERY-7',
  type: 'EXPORT',
  status: 'COMPLETED',
  actor_username: 'operator',
  comment: null,
  retry_of_operation_id: null,
  failure_policy: null,
  started_at: '2026-09-14T05:01:00Z',
  finished_at: '2026-09-14T05:02:00Z',
  error_code: null,
  error_message: null,
  cancel_requested: false,
  bundle: summary.bundle,
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
      repository: 'project/app',
      name: null,
      reference: '1.0.0',
      version: null,
      source_digest: `sha256:${'b'.repeat(64)}`,
      target_digest: null,
      status: 'VERIFIED',
      error_code: null,
      error_message: null,
      size_bytes: 100,
      started_at: '2026-09-14T05:01:00Z',
      finished_at: '2026-09-14T05:02:00Z',
    },
  ],
}

const importSummary: OperationSummary = {
  ...summary,
  id: 9,
  delivery_id: null,
  type: 'IMPORT',
  actor_username: 'operator',
  bundle: null,
}

const importDetail: Operation = {
  ...detail,
  id: 9,
  delivery_id: null,
  type: 'IMPORT',
  actor_username: 'operator',
  bundle: null,
  artifacts: [
    {
      ...detail.artifacts[0],
      id: 2,
      repository: 'source-team/apps/api',
      reference: '1.4.2',
      source_repository: 'source-team/apps/api',
      source_reference: '1.4.2',
      target_project: 'docker-prod',
      target_repository: 'docker-prod/apps/api',
      target_reference: 'harbor-target.local/docker-prod/apps/api:1.4.2',
      destination_plan_id: 'e'.repeat(64),
      status: 'IMPORTED',
      target_digest: detail.artifacts[0].source_digest,
    },
  ],
}

const importReceipt: ImportReceipt = {
  operation_id: 9,
  source_delivery_id: 'SOURCE-DELIVERY-9',
  bundle_sha256: 'c'.repeat(64),
  actor_username: 'operator',
  started_at: '2026-09-14T05:01:00Z',
  finished_at: '2026-09-14T05:02:00Z',
  overwrite_conflicts: false,
  destination_plan_id: 'e'.repeat(64),
  result: 'COMPLETED',
  artifacts: [],
}

const failedImportSummary: OperationSummary = {
  ...importSummary,
  status: 'FAILED',
  error_code: 'import_partial_failure',
  error_message: 'Import завершён с ошибками отдельных артефактов; rollback не выполнялся',
  successful_artifacts: 1,
  failed_artifacts: 1,
}

const failedImportDetail: Operation = {
  ...importDetail,
  status: 'FAILED',
  error_code: 'import_partial_failure',
  error_message: 'Import завершён с ошибками отдельных артефактов; rollback не выполнялся',
  progress: {
    ...importDetail.progress,
    successful_artifacts: 1,
    failed_artifacts: 1,
    current_phase: 'FAILED',
  },
}

const failedReceipt: ImportReceipt = {
  ...importReceipt,
  result: 'FAILED',
}

function retryPlan(classifications: Array<'SAME' | 'NEW' | 'CONFLICT'>): ImportDestinationPlan {
  return {
    operation_id: 10,
    source_delivery_id: 'SOURCE-DELIVERY-9',
    actor_username: 'operator',
    bundle_sha256: 'c'.repeat(64),
    plan_id: 'e'.repeat(64),
    plan_hash: 'f'.repeat(64),
    mapping_policy_revision: 3,
    created_at: '2026-09-15T10:00:00Z',
    valid: true,
    artifacts: classifications.map((classification, index) => ({
      index,
      artifact_type: index === 0 ? 'container-image' : 'helm-chart',
      source_repository: index === 0 ? 'source-team/apps/api' : 'source-charts/platform',
      source_project: index === 0 ? 'source-team' : 'source-charts',
      name: index === 0 ? null : 'mis',
      reference: index === 0 ? '1.4.2' : null,
      version: index === 0 ? null : '4.88.6',
      expected_digest: `sha256:${String(index + 1).repeat(64)}`,
      payload_size: 100,
      target_project: index === 0 ? 'docker-prod' : 'helm-prod',
      target_repository: index === 0 ? 'docker-prod/apps/api' : 'helm-prod/platform',
      final_reference:
        index === 0
          ? 'harbor-target.local/docker-prod/apps/api:1.4.2'
          : 'oci://harbor-target.local/helm-prod/platform/mis:4.88.6',
      project_exists: true,
      write_allowed: true,
      target_digest: classification === 'NEW' ? null : `sha256:${'a'.repeat(64)}`,
      classification,
      error_code: null,
      message: null,
    })),
  }
}

function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((button) => button.text().includes(text))
}

beforeEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
  setActivePinia(createPinia())
})

describe('HistoryView', () => {
  it('renders read-only history and lets viewer download terminal CSV/PDF reports', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [summary], total: 1, limit: 25, offset: 0,
    })
    vi.spyOn(historyApi, 'getOperation').mockResolvedValue(detail)
    const reportDownload = vi.spyOn(historyApi, 'downloadOperationReport').mockResolvedValue()
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 3, username: 'viewer', role: 'viewer', is_active: true }

    const wrapper = mount(HistoryView)
    await flushPromises()

    expect(wrapper.text()).toContain('История операций')
    expect(wrapper.text()).toContain('DELIVERY-7')
    expect(wrapper.find('select').exists()).toBe(true)

    await wrapper.get('.link-button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Bundle metadata')
    expect(wrapper.text()).toContain('project/app:1.0.0')
    expect(wrapper.text()).toContain('Отчёты операции')
    expect(wrapper.text()).not.toContain('Отменить операцию')
    expect(wrapper.text()).not.toContain('Скачать через авторизованный ticket')

    await buttonByText(wrapper, 'Скачать CSV')!.trigger('click')
    await buttonByText(wrapper, 'Скачать PDF')!.trigger('click')
    await flushPromises()

    expect(reportDownload).toHaveBeenCalledWith(7, 'csv')
    expect(reportDownload).toHaveBeenCalledWith(7, 'pdf')
  })

  it('shows persisted source and target references for a mapped import', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [importSummary], total: 1, limit: 25, offset: 0,
    })
    vi.spyOn(historyApi, 'getOperation').mockResolvedValue(importDetail)
    vi.spyOn(historyApi, 'getImportReceipt').mockResolvedValue(importReceipt)
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 2, username: 'operator', role: 'operator', is_active: true }

    const wrapper = mount(HistoryView)
    await flushPromises()
    await wrapper.get('.link-button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('source-team/apps/api:1.4.2')
    expect(wrapper.text()).toContain('harbor-target.local/docker-prod/apps/api:1.4.2')
    expect(wrapper.text()).toContain('container-image')
  })

  it('lets the import owner download canonical receipt JSON', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [importSummary], total: 1, limit: 25, offset: 0,
    })
    vi.spyOn(historyApi, 'getOperation').mockResolvedValue(importDetail)
    vi.spyOn(historyApi, 'getImportReceipt').mockResolvedValue(importReceipt)
    const receiptDownload = vi.spyOn(historyApi, 'downloadImportReceiptFile').mockResolvedValue()
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 2, username: 'operator', role: 'operator', is_active: true }

    const wrapper = mount(HistoryView)
    await flushPromises()
    await wrapper.get('.link-button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Import receipt')
    expect(wrapper.text()).toContain('SOURCE-DELIVERY-9')
    const receiptButton = buttonByText(wrapper, 'Скачать receipt JSON')
    expect(receiptButton?.exists()).toBe(true)

    await receiptButton!.trigger('click')
    await flushPromises()
    expect(receiptDownload).toHaveBeenCalledWith(9)
  })

  it('does not expose canonical receipt download or retry to viewer', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [failedImportSummary], total: 1, limit: 25, offset: 0,
    })
    vi.spyOn(historyApi, 'getOperation').mockResolvedValue(failedImportDetail)
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 3, username: 'viewer', role: 'viewer', is_active: true }

    const wrapper = mount(HistoryView)
    await flushPromises()
    await wrapper.get('.link-button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Import receipt')
    expect(wrapper.text()).not.toContain('Скачать receipt JSON')
    expect(wrapper.text()).not.toContain('Retry и revalidation')
    expect(wrapper.text()).toContain('Скачать CSV')
    expect(wrapper.text()).toContain('Скачать PDF')
  })

  it('shows fresh retry conflicts and keeps execution default-denied', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [failedImportSummary], total: 1, limit: 25, offset: 0,
    })
    vi.spyOn(historyApi, 'getOperation').mockResolvedValue(failedImportDetail)
    vi.spyOn(historyApi, 'getImportReceipt').mockResolvedValue(failedReceipt)
    const plan = retryPlan(['SAME', 'CONFLICT'])
    const prepare = vi.spyOn(importsApi, 'prepareImportRetry').mockResolvedValue({
      operation_id: 10,
      retry_of_operation_id: 9,
      status: 'READY',
      failure_policy: 'continue-on-error',
      destination_plan: plan,
    })
    const execute = vi.spyOn(importsApi, 'executeImport').mockResolvedValue({
      operation_id: 10,
      status: 'READY',
    })
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 2, username: 'operator', role: 'operator', is_active: true }

    const wrapper = mount(HistoryView)
    await flushPromises()
    await wrapper.get('.link-button').trigger('click')
    await flushPromises()
    await buttonByText(wrapper, 'Retry и revalidation')!.trigger('click')
    await flushPromises()

    expect(prepare).toHaveBeenCalledWith(9, 'e'.repeat(64))
    expect(wrapper.text()).toContain('Новый TARGET conflict')
    expect(wrapper.text()).toContain('SAME')
    expect(wrapper.text()).toContain('CONFLICT')
    expect(wrapper.text()).not.toContain('rollback completed')
    const start = buttonByText(wrapper, 'Запустить retry без overwrite')
    expect(start?.attributes('disabled')).toBeDefined()
    expect(execute).not.toHaveBeenCalled()
  })

  it('starts a revalidated retry without carrying overwrite approval', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [failedImportSummary], total: 1, limit: 25, offset: 0,
    })
    vi.spyOn(historyApi, 'getOperation').mockResolvedValue(failedImportDetail)
    vi.spyOn(historyApi, 'getImportReceipt').mockResolvedValue(failedReceipt)
    const plan = retryPlan(['SAME', 'NEW'])
    vi.spyOn(importsApi, 'prepareImportRetry').mockResolvedValue({
      operation_id: 10,
      retry_of_operation_id: 9,
      status: 'READY',
      failure_policy: 'continue-on-error',
      destination_plan: plan,
    })
    const execute = vi.spyOn(importsApi, 'executeImport').mockResolvedValue({
      operation_id: 10,
      status: 'IMPORTING',
    })
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 2, username: 'operator', role: 'operator', is_active: true }

    const wrapper = mount(HistoryView)
    await flushPromises()
    await wrapper.get('.link-button').trigger('click')
    await flushPromises()
    await buttonByText(wrapper, 'Retry и revalidation')!.trigger('click')
    await flushPromises()
    await buttonByText(wrapper, 'Запустить retry без overwrite')!.trigger('click')
    await flushPromises()

    expect(execute).toHaveBeenCalledWith(10, false, plan.plan_id)
    expect(wrapper.text()).toContain('Retry operation #10 запущена')
  })

  it('resumes an existing READY import from history without creating a new operation', async () => {
    const readySummary: OperationSummary = {
      ...importSummary,
      id: 5,
      status: 'READY',
      finished_at: null,
      successful_artifacts: 0,
      skipped_artifacts: 0,
    }
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [readySummary], total: 1, limit: 25, offset: 0,
    })
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 1, username: 'admin', role: 'admin', is_active: true }
    const runtime = useRuntimeStore()
    runtime.setContour('TARGET')
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/history', component: { template: '<div />' } },
        { path: '/import', component: { template: '<div />' } },
        { path: '/export', component: { template: '<div />' } },
      ],
    })
    await router.push('/history')
    await router.isReady()

    const wrapper = mount(HistoryView, { global: { plugins: [router] } })
    await flushPromises()

    const resume = buttonByText(wrapper, 'Продолжить')
    expect(resume?.exists()).toBe(true)
    await resume!.trigger('click')
    await flushPromises()

    expect(sessionStorage.getItem('htp.import.operation-id')).toBe('5')
    expect(router.currentRoute.value.path).toBe('/import')
  })

  it('cancels READY import from history after explicit confirmation', async () => {
    const readySummary: OperationSummary = { ...importSummary, id: 5, status: 'READY', finished_at: null }
    const readyDetail: Operation = {
      ...importDetail,
      id: 5,
      status: 'READY',
      finished_at: null,
      progress: { ...importDetail.progress, current_phase: 'READY', completed_artifacts: 0 },
    }
    const cancelled: Operation = {
      ...readyDetail,
      status: 'CANCELLED',
      finished_at: '2026-09-21T14:31:00Z',
      error_code: 'operation_cancelled',
      error_message: 'Операция отменена пользователем',
      progress: { ...readyDetail.progress, current_phase: 'CANCELLED' },
    }
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [readySummary], total: 1, limit: 25, offset: 0,
    })
    const cancel = vi.spyOn(historyApi, 'cancelOperation').mockResolvedValue(cancelled)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 1, username: 'admin', role: 'admin', is_active: true }

    const wrapper = mount(HistoryView)
    await flushPromises()
    await buttonByText(wrapper, 'Отменить')!.trigger('click')
    await flushPromises()

    expect(cancel).toHaveBeenCalledWith(5)
    expect(wrapper.text()).toContain('CANCELLED')
    expect(wrapper.text()).not.toContain('Продолжить')
  })
  it('shows a useful empty state', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [], total: 0, limit: 25, offset: 0,
    })

    const wrapper = mount(HistoryView)
    await flushPromises()

    expect(wrapper.text()).toContain('Операции не найдены')
  })

  it('shows safe API failure state without raw logs', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockRejectedValue(new Error('network down'))

    const wrapper = mount(HistoryView)
    await flushPromises()

    expect(wrapper.text()).toContain('unexpected_error')
    expect(wrapper.text()).toContain('Не удалось загрузить историю операций')
  })
})
