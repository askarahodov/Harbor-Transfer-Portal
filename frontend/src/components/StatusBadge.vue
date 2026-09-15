<script setup lang="ts">
type StatusTone = 'success' | 'warning' | 'error' | 'info'

defineProps<{
  label: string
  tone?: StatusTone
}>()
</script>

<template>
  <span class="status-badge" :class="`status-badge--${tone ?? 'info'}`">{{ label }}</span>
</template>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  font-size: 12px;
  font-weight: 600;
}
.status-badge--success { background: var(--color-success-surface); color: var(--color-success-text); }
.status-badge--warning { background: var(--color-warning-surface); color: var(--color-warning-text); }
.status-badge--error { background: var(--color-danger-surface); color: var(--color-danger-text); }
.status-badge--info { background: var(--color-info-surface); color: var(--color-info-text); }
</style>
