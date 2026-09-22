<script setup lang="ts">
import { CheckCircle2, RefreshCw, XCircle } from 'lucide-vue-next'

import type { ImportPreview, Operation } from '@/api/imports'
import { formatBytes } from '@/presentation/format'

defineProps<{
  operation: Operation
  preview: ImportPreview | null
  phaseLabel: string
  activeFilename: string
  activeSize: number | null
  canCancel: boolean
  busy: boolean
}>()

const emit = defineEmits<{
  refresh: []
  cancel: []
}>()
</script>

<template>
  <article class="verification-card" aria-labelledby="verification-title">
    <div class="verification-card__title">
      <div>
        <p class="eyebrow">Операция #{{ operation.id }}</p>
        <h3 id="verification-title">{{ phaseLabel }}</h3>
      </div>
      <button
        class="icon-button"
        type="button"
        aria-label="Обновить состояние операции"
        @click="emit('refresh')"
      >
        <RefreshCw :size="18" aria-hidden="true" />
      </button>
    </div>

    <dl class="metadata-grid">
      <div><dt>Файл</dt><dd>{{ activeFilename }}</dd></div>
      <div><dt>Размер</dt><dd>{{ formatBytes(activeSize) }}</dd></div>
      <div><dt>Intake</dt><dd>{{ preview?.intake_mode ?? 'проверяется' }}</dd></div>
    </dl>

    <div
      class="verification-list"
      role="status"
      aria-live="polite"
      aria-label="Результаты проверки bundle"
    >
      <div class="verification-row">
        <CheckCircle2 v-if="preview?.checksum_verified" :size="20" aria-hidden="true" />
        <RefreshCw v-else-if="operation.status === 'VERIFYING'" :size="20" aria-hidden="true" />
        <XCircle v-else-if="operation.status === 'REJECTED'" :size="20" aria-hidden="true" />
        <span>SHA-256 integrity</span>
        <strong>{{ preview?.checksum_verified ? 'подтверждена' : operation.status === 'VERIFYING' ? 'проверяется' : 'не подтверждена' }}</strong>
      </div>
      <div class="verification-row">
        <CheckCircle2 v-if="preview?.schema_verified" :size="20" aria-hidden="true" />
        <RefreshCw v-else-if="operation.status === 'VERIFYING'" :size="20" aria-hidden="true" />
        <XCircle v-else-if="operation.status === 'REJECTED'" :size="20" aria-hidden="true" />
        <span>Bundle v1 schema/canonical manifest</span>
        <strong>{{ preview?.schema_verified ? 'совместима' : operation.status === 'VERIFYING' ? 'проверяется' : 'не подтверждена' }}</strong>
      </div>
      <div class="verification-row">
        <CheckCircle2 v-if="preview?.signature_verified" :size="20" aria-hidden="true" />
        <RefreshCw v-else-if="operation.status === 'VERIFYING'" :size="20" aria-hidden="true" />
        <XCircle v-else-if="operation.status === 'REJECTED'" :size="20" aria-hidden="true" />
        <span>Ed25519 signature trust</span>
        <strong>{{ preview?.signature_verified ? 'подпись доверена' : operation.status === 'VERIFYING' ? 'проверяется' : 'не подтверждена' }}</strong>
      </div>
    </div>

    <button
      v-if="canCancel"
      class="button button--danger"
      type="button"
      :disabled="busy"
      @click="emit('cancel')"
    >
      Отменить проверку
    </button>
  </article>
</template>

<style scoped>
.eyebrow { margin: 0 0 var(--space-1); color: var(--color-action); font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
h3 { margin-top: 0; }
.verification-card { padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); }
.verification-card__title { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-4); }
.metadata-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-3); margin: 0; }
.metadata-grid div { min-width: 0; padding: var(--space-3); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
dt { color: var(--color-text-muted); font-size: 12px; }
dd { margin: var(--space-1) 0 0; overflow-wrap: anywhere; font-weight: 600; }
.verification-list { display: grid; gap: var(--space-2); margin: var(--space-4) 0; }
.verification-row { display: grid; grid-template-columns: auto 1fr auto; gap: var(--space-3); align-items: center; padding: var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.icon-button { min-width: 40px; min-height: 40px; display: grid; place-items: center; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); cursor: pointer; }
.button { min-height: 42px; display: inline-flex; align-items: center; justify-content: center; gap: var(--space-2); padding: 0 var(--space-4); border: 1px solid transparent; border-radius: var(--radius-md); font: inherit; font-weight: 600; text-decoration: none; cursor: pointer; }
.button:disabled { cursor: not-allowed; opacity: .55; }
.button--danger { background: var(--color-danger-text); color: var(--color-on-accent); }
@media (max-width: 900px) {
  .metadata-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 640px) {
  .metadata-grid { grid-template-columns: 1fr; }
  .verification-row { grid-template-columns: auto 1fr; }
  .verification-row strong { grid-column: 2; }
}
</style>
