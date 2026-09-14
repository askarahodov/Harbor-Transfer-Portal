import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { getHarborConnection, type HarborConnection } from '@/api/exports'
import {
  apiErrorInfo,
  listOperationHistory,
  type ApiErrorInfo,
  type OperationSummary,
} from '@/api/history'

const RECENT_OPERATIONS_LIMIT = 5

export const useDashboardStore = defineStore('dashboard', () => {
  const harbor = ref<HarborConnection | null>(null)
  const recentOperations = ref<OperationSummary[]>([])
  const harborLoading = ref(false)
  const historyLoading = ref(false)
  const harborError = ref<ApiErrorInfo | null>(null)
  const historyError = ref<ApiErrorInfo | null>(null)

  const loading = computed(() => harborLoading.value || historyLoading.value)

  async function loadHarbor(): Promise<void> {
    harborLoading.value = true
    harborError.value = null
    try {
      harbor.value = await getHarborConnection()
    } catch (error) {
      harbor.value = null
      harborError.value = apiErrorInfo(error, 'Не удалось проверить подключение к локальному Harbor.')
    } finally {
      harborLoading.value = false
    }
  }

  async function loadRecentOperations(): Promise<void> {
    historyLoading.value = true
    historyError.value = null
    try {
      const page = await listOperationHistory(
        {
          type: '',
          status: '',
          actor: '',
          search: '',
          createdFrom: '',
          createdTo: '',
        },
        RECENT_OPERATIONS_LIMIT,
        0,
      )
      recentOperations.value = page.items
    } catch (error) {
      recentOperations.value = []
      historyError.value = apiErrorInfo(error, 'Не удалось загрузить последние операции.')
    } finally {
      historyLoading.value = false
    }
  }

  async function load(): Promise<void> {
    await Promise.all([loadHarbor(), loadRecentOperations()])
  }

  return {
    harbor,
    recentOperations,
    harborLoading,
    historyLoading,
    harborError,
    historyError,
    loading,
    loadHarbor,
    loadRecentOperations,
    load,
  }
})
