import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { apiClient } from '@/api/client'

export type PortalContour = 'SOURCE' | 'TARGET'

type HealthPayload = {
  contour?: unknown
  version?: unknown
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

export const useRuntimeStore = defineStore('runtime', () => {
  const contour = ref<PortalContour | null>(readInjectedContour())
  const version = ref<string | null>(null)
  const loading = ref(false)
  const errorCode = ref<string | null>(null)

  const contourLabel = computed(() => contour.value ?? '—')

  function setContour(value: PortalContour) {
    contour.value = value
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

  return { contour, contourLabel, version, loading, errorCode, setContour, loadRuntime }
})
