<script setup lang="ts">
import type { PortalContour } from '@/stores/runtime'

const props = defineProps<{
  contour: PortalContour | null
  busy?: boolean
  errorCode?: string | null
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
    return 'Дождитесь завершения текущей операции перед переключением режима.'
  }
  if (code === 'runtime_mode_invalid_response') {
    return 'Сервер не подтвердил новый режим. Текущий режим не изменён.'
  }
  return 'Не удалось переключить режим. Попробуйте ещё раз.'
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
  </div>
</template>

<style scoped>
.mode-control { display: inline-flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.mode-control__label { color: var(--color-steel); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }
.mode-switcher { display: inline-grid; grid-template-columns: 1fr 1fr; padding: 3px; border: 1px solid var(--color-mist); border-radius: var(--radius-md); background: var(--color-cloud); }
.mode-switcher__option { min-height: 34px; border: 0; border-radius: calc(var(--radius-md) - 3px); padding: 0 var(--space-3); background: transparent; color: var(--color-steel); font: inherit; font-size: 13px; font-weight: 700; cursor: pointer; }
.mode-switcher__option:hover:not(:disabled), .mode-switcher__option:focus-visible { color: var(--color-deep-harbor); outline: 2px solid var(--color-bridge-blue); outline-offset: 1px; }
.mode-switcher__option--active { background: white; color: var(--color-deep-harbor); box-shadow: 0 1px 3px rgba(15, 23, 42, .12); cursor: default; }
.mode-switcher__option:disabled:not(.mode-switcher__option--active) { opacity: .55; cursor: wait; }
.mode-control__error { flex-basis: 100%; color: var(--color-danger); font-size: 12px; }
</style>
