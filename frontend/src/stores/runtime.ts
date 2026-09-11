import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export type PortalContour = 'SOURCE' | 'TARGET'

function readInjectedContour(): PortalContour | null {
  if (typeof window === 'undefined') return null
  const contour = window.__HTP_CONFIG__?.contour
  return contour === 'SOURCE' || contour === 'TARGET' ? contour : null
}

export const useRuntimeStore = defineStore('runtime', () => {
  const contour = ref<PortalContour | null>(readInjectedContour())

  const contourLabel = computed(() => contour.value ?? '—')

  function setContour(value: PortalContour) {
    contour.value = value
  }

  return { contour, contourLabel, setContour }
})
