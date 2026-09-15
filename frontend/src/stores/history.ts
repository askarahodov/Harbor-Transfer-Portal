import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  apiErrorInfo,
  createExportDownloadTicket,
  downloadImportReceiptFile,
  downloadOperationReport,
  getImportReceipt,
  getOperation,
  listOperationHistory,
  type ApiErrorInfo,
  type HistoryFilters,
  type ImportReceipt,
  type Operation,
  type OperationReportFormat,
  type OperationSummary,
} from '@/api/history'
import {
  executeImport,
  prepareImportRetry,
  type ImportDestinationPlan,
} from '@/api/imports'
import { useAuthStore } from '@/stores/auth'

const PAGE_SIZE = 25
const REPORT_READY_STATUSES = ['COMPLETED', 'FAILED', 'REJECTED', 'CANCELLED'] as const

function emptyFilters(): HistoryFilters {
  return {
    type: '',
    status: '',
    actor: '',
    search: '',
    createdFrom: '',
    createdTo: '',
  }
}

export const useHistoryStore = defineStore('history', () => {
  const auth = useAuthStore()
  const filters = reactive<HistoryFilters>(emptyFilters())
  const items = ref<OperationSummary[]>([])
  const total = ref(0)
  const offset = ref(0)
  const loading = ref(false)
  const error = ref<ApiErrorInfo | null>(null)

  const selectedSummary = ref<OperationSummary | null>(null)
  const detail = ref<Operation | null>(null)
  const detailLoading = ref(false)
  const detailError = ref<ApiErrorInfo | null>(null)
  const receipt = ref<ImportReceipt | null>(null)
  const receiptState = ref<'idle' | 'loading' | 'ready' | 'unavailable'>('idle')
  const downloadError = ref<string | null>(null)

  const retryPlan = ref<ImportDestinationPlan | null>(null)
  const retryOperationId = ref<number | null>(null)
  const retryPreparing = ref(false)
  const retryStarting = ref(false)
  const retryStarted = ref(false)
  const retryError = ref<ApiErrorInfo | null>(null)

  const currentPage = computed(() => Math.floor(offset.value / PAGE_SIZE) + 1)
  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
  const hasPrevious = computed(() => offset.value > 0)
  const hasNext = computed(() => offset.value + PAGE_SIZE < total.value)

  const canReadSelectedReceipt = computed(() => {
    if (!detail.value || detail.value.type !== 'IMPORT') return false
    if (!['COMPLETED', 'FAILED'].includes(detail.value.status)) return false
    if (auth.user?.role === 'admin') return true
    return auth.user?.role === 'operator' && auth.user.username === detail.value.actor_username
  })

  const canDownloadSelectedReport = computed(() => {
    if (!detail.value) return false
    return REPORT_READY_STATUSES.includes(
      detail.value.status as (typeof REPORT_READY_STATUSES)[number],
    )
  })

  const canDownloadSelectedReceipt = computed(
    () => canReadSelectedReceipt.value && receiptState.value === 'ready',
  )

  const canDownloadSelectedExport = computed(() => {
    if (!detail.value || detail.value.type !== 'EXPORT') return false
    if (detail.value.status !== 'COMPLETED' || !detail.value.bundle) return false
    if (auth.user?.role === 'admin') return true
    return auth.user?.role === 'operator' && auth.user.username === detail.value.actor_username
  })

  const selectedDestinationPlanId = computed(() => {
    if (receipt.value?.destination_plan_id) return receipt.value.destination_plan_id
    if (!detail.value) return null
    const ids = new Set(
      detail.value.artifacts
        .map((artifact) => artifact.destination_plan_id)
        .filter((value): value is string => Boolean(value)),
    )
    return ids.size === 1 ? [...ids][0] : null
  })

  const canPrepareSelectedRetry = computed(() => {
    const operation = detail.value
    if (
      !operation ||
      operation.type !== 'IMPORT' ||
      operation.status !== 'FAILED' ||
      operation.error_code !== 'import_partial_failure' ||
      !selectedDestinationPlanId.value
    ) {
      return false
    }
    if (auth.user?.role === 'admin') return true
    return auth.user?.role === 'operator' && auth.user.username === operation.actor_username
  })

  const retryHasConflicts = computed(
    () => retryPlan.value?.artifacts.some((item) => item.classification === 'CONFLICT') ?? false,
  )

  const retryHasUnresolved = computed(
    () =>
      retryPlan.value?.artifacts.some((item) =>
        ['CONFLICT', 'UNKNOWN', 'ERROR'].includes(item.classification),
      ) ?? false,
  )

  const canStartPreparedRetry = computed(
    () =>
      Boolean(
        retryPlan.value?.valid &&
          retryOperationId.value &&
          !retryHasUnresolved.value &&
          !retryPreparing.value &&
          !retryStarting.value &&
          !retryStarted.value,
      ),
  )

  function resetRetryState(): void {
    retryPlan.value = null
    retryOperationId.value = null
    retryPreparing.value = false
    retryStarting.value = false
    retryStarted.value = false
    retryError.value = null
  }

  function dateRangeInvalid(): boolean {
    if (!filters.createdFrom || !filters.createdTo) return false
    return new Date(filters.createdFrom).getTime() > new Date(filters.createdTo).getTime()
  }

  async function load(resetOffset = false): Promise<void> {
    if (dateRangeInvalid()) {
      error.value = {
        code: 'history_date_range_invalid',
        message: 'Начало периода должно быть раньше или равно окончанию.',
      }
      return
    }
    if (resetOffset) offset.value = 0
    loading.value = true
    error.value = null
    try {
      const page = await listOperationHistory(filters, PAGE_SIZE, offset.value)
      items.value = page.items
      total.value = page.total
      offset.value = page.offset
    } catch (requestError) {
      error.value = apiErrorInfo(requestError, 'Не удалось загрузить историю операций.')
    } finally {
      loading.value = false
    }
  }

  async function applyFilters(): Promise<void> {
    await load(true)
  }

  async function clearFilters(): Promise<void> {
    Object.assign(filters, emptyFilters())
    await load(true)
  }

  async function previousPage(): Promise<void> {
    if (!hasPrevious.value || loading.value) return
    offset.value = Math.max(0, offset.value - PAGE_SIZE)
    await load()
  }

  async function nextPage(): Promise<void> {
    if (!hasNext.value || loading.value) return
    offset.value += PAGE_SIZE
    await load()
  }

  async function loadReceiptIfAllowed(): Promise<void> {
    receipt.value = null
    receiptState.value = 'idle'
    if (!detail.value || !canReadSelectedReceipt.value) return
    receiptState.value = 'loading'
    try {
      receipt.value = await getImportReceipt(detail.value.id)
      receiptState.value = 'ready'
    } catch (requestError) {
      const info = apiErrorInfo(requestError, 'Receipt недоступен.')
      if ([403, 404, 409].includes(info.status ?? 0)) {
        receiptState.value = 'unavailable'
      } else {
        receiptState.value = 'unavailable'
        detailError.value = info
      }
    }
  }

  async function openDetail(summary: OperationSummary): Promise<void> {
    selectedSummary.value = summary
    detail.value = null
    receipt.value = null
    receiptState.value = 'idle'
    detailError.value = null
    downloadError.value = null
    resetRetryState()
    detailLoading.value = true
    try {
      detail.value = await getOperation(summary.id)
      await loadReceiptIfAllowed()
    } catch (requestError) {
      detailError.value = apiErrorInfo(requestError, 'Не удалось загрузить детали операции.')
    } finally {
      detailLoading.value = false
    }
  }

  function closeDetail(): void {
    selectedSummary.value = null
    detail.value = null
    detailError.value = null
    receipt.value = null
    receiptState.value = 'idle'
    downloadError.value = null
    resetRetryState()
  }

  async function prepareSelectedRetry(): Promise<void> {
    const operation = detail.value
    const planId = selectedDestinationPlanId.value
    if (!operation || !planId || !canPrepareSelectedRetry.value || retryPreparing.value) return
    resetRetryState()
    retryPreparing.value = true
    try {
      const prepared = await prepareImportRetry(operation.id, planId)
      retryOperationId.value = prepared.operation_id
      retryPlan.value = prepared.destination_plan
    } catch (requestError) {
      retryError.value = apiErrorInfo(requestError, 'Не удалось подготовить безопасный retry.')
    } finally {
      retryPreparing.value = false
    }
  }

  async function startPreparedRetry(): Promise<void> {
    if (
      !retryOperationId.value ||
      !retryPlan.value ||
      !canStartPreparedRetry.value ||
      retryStarting.value
    ) {
      return
    }
    retryStarting.value = true
    retryError.value = null
    try {
      await executeImport(retryOperationId.value, false, retryPlan.value.plan_id)
      retryStarted.value = true
      await load(true)
    } catch (requestError) {
      retryError.value = apiErrorInfo(requestError, 'Не удалось запустить retry import.')
    } finally {
      retryStarting.value = false
    }
  }

  async function downloadSelectedReport(format: OperationReportFormat): Promise<void> {
    if (!detail.value || !canDownloadSelectedReport.value) return
    downloadError.value = null
    try {
      await downloadOperationReport(detail.value.id, format)
    } catch (requestError) {
      downloadError.value = apiErrorInfo(
        requestError,
        `Не удалось скачать ${format.toUpperCase()}-отчёт операции.`,
      ).message
    }
  }

  async function downloadSelectedReceipt(): Promise<void> {
    if (!detail.value || !canDownloadSelectedReceipt.value) return
    downloadError.value = null
    try {
      await downloadImportReceiptFile(detail.value.id)
    } catch (requestError) {
      downloadError.value = apiErrorInfo(
        requestError,
        'Не удалось скачать canonical import receipt.',
      ).message
    }
  }

  async function downloadSelectedExport(): Promise<void> {
    if (!detail.value || !canDownloadSelectedExport.value) return
    downloadError.value = null
    try {
      const ticket = await createExportDownloadTicket(detail.value.id)
      window.location.assign(ticket.download_url)
    } catch (requestError) {
      downloadError.value = apiErrorInfo(
        requestError,
        'Bundle metadata сохранилась в history, но файл сейчас нельзя скачать.',
      ).message
    }
  }

  return {
    filters,
    items,
    total,
    offset,
    loading,
    error,
    selectedSummary,
    detail,
    detailLoading,
    detailError,
    receipt,
    receiptState,
    downloadError,
    retryPlan,
    retryOperationId,
    retryPreparing,
    retryStarting,
    retryStarted,
    retryError,
    currentPage,
    pageCount,
    hasPrevious,
    hasNext,
    canReadSelectedReceipt,
    canDownloadSelectedReport,
    canDownloadSelectedReceipt,
    canDownloadSelectedExport,
    selectedDestinationPlanId,
    canPrepareSelectedRetry,
    retryHasConflicts,
    retryHasUnresolved,
    canStartPreparedRetry,
    load,
    applyFilters,
    clearFilters,
    previousPage,
    nextPage,
    openDetail,
    closeDetail,
    prepareSelectedRetry,
    startPreparedRetry,
    downloadSelectedReport,
    downloadSelectedReceipt,
    downloadSelectedExport,
  }
})
