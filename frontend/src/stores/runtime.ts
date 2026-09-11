import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { apiClient } from '@/api/client'

export type PortalContour = 'SOURCE' | 'TARGET'

type HealthPayload = {
  contour?: unknown
}

function isPortalContour(value: unknown): value is PortalContour {
  return value === 'SOURCE' || value === 'TARGET'
}

function readInjectedContour(): PortalContour | null {
  if (typeof window === 'undefined') return null
  const contour = window.__HTP_CONFIG__?.contour
  return isPortalContour(contour) ? contour : null
}

export const useRuntimeStore = defineStore('runtime', () => {
  const contour = ref<PortalContour | null>(readInjectedContour())
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
      contour.value = response.data.contour
      errorCode.value = null
    } catch {
      if (!contour.value) {
        errorCode.value = 'runtime_config_unavailable'
      }
    } finally {
      loading.value = false
    }
  }

  return { contour, contourLabel, loading, errorCode, setContour, loadRuntime }
})
