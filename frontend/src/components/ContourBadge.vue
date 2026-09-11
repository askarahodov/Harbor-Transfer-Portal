<script setup lang="ts">
import type { PortalContour } from '@/stores/runtime'

defineProps<{
  contour: PortalContour | null
}>()
</script>

<template>
  <span class="contour-badge" :class="contour ? `contour-badge--${contour.toLowerCase()}` : ''">
    <span class="contour-badge__dot" aria-hidden="true" />
    Контур: {{ contour ?? 'не определён' }}
  </span>
</template>

<style scoped>
.contour-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 6px 12px;
  border: 1px solid var(--color-mist);
  border-radius: var(--radius-full);
  background: var(--color-cloud-white);
  color: var(--color-deep-harbor);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.contour-badge__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-steel);
}

.contour-badge--source .contour-badge__dot { background: var(--color-transfer-green); }
.contour-badge--target .contour-badge__dot { background: var(--color-bridge-blue); }
</style>
