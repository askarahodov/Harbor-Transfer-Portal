<script setup lang="ts">
import type { PortalContour } from '@/stores/runtime'

const props = defineProps<{
  contour: PortalContour
  busy?: boolean
  errorCode?: string | null
  cancelledOperationIds?: number[]
}>()

const emit = defineEmits<{
  switch: [mode: PortalContour]
}>()

function select(mode: PortalContour): void {
  if (props.busy || props.contour === mode) return
  emit('switch', mode)
}

function errorMessage(code: string | null | undefined): string | null {
  if (!code) return null
  if (code === 'runtime_mode_busy') {
    return 'Не все незавершённые операции удалось безопасно остановить. Режим не изменён.'
  }
  if (code === 'runtime_mode_switch_in_progress') {
    return 'Другое переключение режима уже выполняется.'
  }
  if (code === 'runtime_mode_cancel_failed') {
    return 'Не удалось безопасно отменить одну из незавершённых операций. Режим не изменён.'
  }
  if (code === 'runtime_mode_switch_invalid' || code === 'runtime_mode_switch_stale') {
    return 'Состояние режима изменилось во время переключения. Обновите страницу и повторите.'
  }
  if (code === 'runtime_mode_invalid_response') {
    return 'Сервер не подтвердил новый режим. Текущий режим не изменён.'
  }
  return `Не удалось переключить режим (${code}). Текущий режим не изменён.`
}
</script>

<template>
  <div class="mode-control">
    <span class="mode-control__label">Режим</span>
    <div class="mode-switcher" role="group" aria-label="Режим работы Portal">
      <button
        type="button"
        class="mode-switcher__option"
        :class="{ 'mode-switcher__option--active': contour === 'SOURCE' }"
        :aria-pressed="contour === 'SOURCE'"
        :disabled="busy || contour === 'SOURCE'"
        @click="select('SOURCE')"
      >
        Отправка
      </button>
      <button
        type="button"
        class="mode-switcher__option"
        :class="{ 'mode-switcher__option--active': contour === 'TARGET' }"
        :aria-pressed="contour === 'TARGET'"
        :disabled="busy || contour === 'TARGET'"
        @click="select('TARGET')"
      >
        Приём
      </button>
    </div>
    <span v-if="errorMessage(errorCode)" class="mode-control__error" role="status" aria-live="polite">
      {{ errorMessage(errorCode) }}
    </span>
    <span
      v-else-if="cancelledOperationIds?.length"
      class="mode-control__notice"
      role="status"
      aria-live="polite"
    >
      Отменены незавершённые операции:
      {{ cancelledOperationIds.map((operationId) => `#${operationId}`).join(', ') }}.
    </span>
  </div>
</template>

<style scoped>
.mode-control { display: inline-flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.mode-control__label { color: var(--color-text-muted); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }
.mode-switcher { display: inline-grid; grid-template-columns: 1fr 1fr; padding: 3px; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.mode-switcher__option { min-height: 34px; border: 0; border-radius: calc(var(--radius-md) - 3px); padding: 0 var(--space-3); background: transparent; color: var(--color-text-muted); font: inherit; font-size: 13px; font-weight: 700; cursor: pointer; }
.mode-switcher__option:hover:not(:disabled), .mode-switcher__option:focus-visible { color: var(--color-text); }
.mode-switcher__option--active { background: var(--color-surface); color: var(--color-text); box-shadow: var(--shadow-control); cursor: default; }
.mode-switcher__option:disabled:not(.mode-switcher__option--active) { opacity: .55; cursor: wait; }
.mode-control__error { flex-basis: 100%; max-width: 420px; color: var(--color-danger-text); font-size: 12px; }
.mode-control__notice { flex-basis: 100%; max-width: 420px; color: var(--color-success-text); font-size: 12px; }
</style>
