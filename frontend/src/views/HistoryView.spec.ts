import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as historyApi from '@/api/history'
import type { ImportReceipt, Operation, OperationSummary } from '@/api/history'
import { useAuthStore } from '@/stores/auth'
import HistoryView from '@/views/HistoryView.vue'

const summary: OperationSummary = {
  id: 7,
  delivery_id: 'DELIVERY-7',
  type: 'EXPORT',
  status: 'COMPLETED',
  actor_username: 'operator',
  comment: null,
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
      destination_plan_id: 'plan-123',
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

function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((button) => button.text().includes(text))
}

beforeEach(() => {
  vi.restoreAllMocks()
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

  it('does not expose canonical receipt download to viewer', async () => {
    vi.spyOn(historyApi, 'listOperationHistory').mockResolvedValue({
      items: [importSummary], total: 1, limit: 25, offset: 0,
    })
    vi.spyOn(historyApi, 'getOperation').mockResolvedValue(importDetail)
    const auth = useAuthStore()
    auth.initialized = true
    auth.user = { id: 3, username: 'viewer', role: 'viewer', is_active: true }

    const wrapper = mount(HistoryView)
    await flushPromises()
    await wrapper.get('.link-button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Import receipt')
    expect(wrapper.text()).not.toContain('Скачать receipt JSON')
    expect(wrapper.text()).toContain('Скачать CSV')
    expect(wrapper.text()).toContain('Скачать PDF')
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
