import axios from 'axios'
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { apiClient } from '@/api/client'

export type PortalContour = 'SOURCE' | 'TARGET'

type HealthPayload = {
  contour?: unknown
  version?: unknown
}

type RuntimeModeUpdatePayload = {
  previous?: unknown
  current?: unknown
  changed?: unknown
  cancelled_operation_ids?: unknown
}

type ApiErrorEnvelope = {
  error?: {
    code?: unknown
  }
  detail?: {
    code?: unknown
  }
}

function isPortalContour(value: unknown): value is PortalContour {
  return value === 'SOURCE' || value === 'TARGET'
}

function isReleaseVersion(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function readInjectedContour(): PortalContour | null {
  if (typeof window === 'undefined') return null
  const contour = window.__HTP_CONFIG__?.contour
  return isPortalContour(contour) ? contour : null
}

function cancelledOperationIds(value: unknown): number[] {
  if (!Array.isArray(value)) return []
  return value.filter(
    (item): item is number => typeof item === 'number' && Number.isInteger(item) && item > 0,
  )
}

function modeSwitchErrorCode(error: unknown): string {
  if (!axios.isAxiosError<ApiErrorEnvelope>(error)) return 'runtime_mode_unavailable'
  const payload = error.response?.data
  const code = payload?.error?.code ?? payload?.detail?.code
  return typeof code === 'string' && code ? code : 'runtime_mode_unavailable'
}

export const useRuntimeStore = defineStore('runtime', () => {
  const contour = ref<PortalContour | null>(readInjectedContour())
  const version = ref<string | null>(null)
  const loading = ref(false)
  const errorCode = ref<string | null>(null)
  const switching = ref(false)
  const switchErrorCode = ref<string | null>(null)
  const lastCancelledOperationIds = ref<number[]>([])
  let switchGeneration = 0

  const contourLabel = computed(() => contour.value ?? '—')

  function setContour(value: PortalContour) {
    contour.value = value
  }

  function clearSwitchError(): void {
    switchErrorCode.value = null
  }

  function clearSwitchNotice(): void {
    lastCancelledOperationIds.value = []
  }

  async function loadRuntime(): Promise<void> {
    loading.value = true
    try {
      const response = await apiClient.get<HealthPayload>('/health')
      if (!isPortalContour(response.data.contour)) {
        throw new Error('Backend returned an unsupported contour value')
      }
      if (!isReleaseVersion(response.data.version)) {
        throw new Error('Backend returned an invalid release version')
      }
      contour.value = response.data.contour
      version.value = response.data.version
      errorCode.value = null
    } catch {
      if (!contour.value) {
        errorCode.value = 'runtime_config_unavailable'
      }
    } finally {
      loading.value = false
    }
  }

  async function switchMode(target: PortalContour): Promise<boolean> {
    if (contour.value === target && !switching.value) {
      switchErrorCode.value = null
      return true
    }

    const generation = ++switchGeneration
    switching.value = true
    switchErrorCode.value = null
    lastCancelledOperationIds.value = []
    try {
      const response = await apiClient.put<RuntimeModeUpdatePayload>('/runtime/mode', {
        mode: target,
      })
      if (generation !== switchGeneration) return false
      if (!isPortalContour(response.data.current) || response.data.current !== target) {
        switchErrorCode.value = 'runtime_mode_invalid_response'
        return false
      }
      contour.value = response.data.current
      lastCancelledOperationIds.value = cancelledOperationIds(
        response.data.cancelled_operation_ids,
      )
      return true
    } catch (error: unknown) {
      if (generation === switchGeneration) {
        switchErrorCode.value = modeSwitchErrorCode(error)
      }
      return false
    } finally {
      if (generation === switchGeneration) {
        switching.value = false
      }
    }
  }

  return {
    contour,
    contourLabel,
    version,
    loading,
    errorCode,
    switching,
    switchErrorCode,
    lastCancelledOperationIds,
    setContour,
    clearSwitchError,
    clearSwitchNotice,
    loadRuntime,
    switchMode,
  }
})
