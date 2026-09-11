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
.status-badge--success { background: var(--color-mint); color: #065f46; }
.status-badge--warning { background: var(--color-sand); color: #92400e; }
.status-badge--error { background: var(--color-rose); color: #991b1b; }
.status-badge--info { background: var(--color-sky); color: #1e40af; }
</style>
