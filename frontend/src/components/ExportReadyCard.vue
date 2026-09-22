<script setup lang="ts">
import { AlertTriangle, Download, FileArchive, PackageCheck } from 'lucide-vue-next'

import type { ExportBundle, Operation } from '@/api/exports'
import { formatBytes } from '@/presentation/format'

defineProps<{
  bundle: ExportBundle | null
  operation: Operation | null
  downloadError: string | null
  printHandoffBusy: boolean
}>()

const emit = defineEmits<{
  downloadBundle: []
  downloadSidecar: []
  downloadHandoff: []
  printHandoff: []
  reset: []
}>()
</script>

<template>
  <section class="ready-card" aria-labelledby="ready-title">
    <div class="ready-icon" aria-hidden="true"><PackageCheck :size="36" /></div>
    <p class="eyebrow">Шаг 4 из 4</p>
    <h2 id="ready-title">Bundle готов к физическому переносу</h2>
    <p class="lead">
      Операция достигла <code>COMPLETED</code>; backend зафиксировал filename, размер и SHA-256 готового archive.
    </p>

    <div v-if="bundle" class="ready-grid">
      <div class="info-box"><span>Delivery ID</span><strong>{{ bundle.delivery_id }}</strong></div>
      <div class="info-box"><span>Файл</span><strong>{{ bundle.archive_name }}</strong></div>
      <div class="info-box"><span>Размер</span><strong>{{ formatBytes(bundle.archive_size) }}</strong></div>
      <div class="info-box info-box--wide"><span>SHA-256</span><code>{{ bundle.sha256 }}</code></div>
    </div>

    <div v-if="operation" class="ready-summary">
      <strong>
        Проверено artifacts: {{ operation.progress.successful_artifacts }} /
        {{ operation.progress.total_artifacts }}
      </strong>
      <span>Все готовые export artifacts имеют terminal verified state.</span>
    </div>

    <div class="notice notice--warning">
      <AlertTriangle :size="22" aria-hidden="true" />
      <div>
        <strong>На носитель нужно скопировать три файла.</strong>
        <p>
          Перенесите <code>.htp.tar.gz</code>, соответствующий <code>.sha256</code> и signed
          <code>.htp-handoff.json</code>. Handoff подтверждает состав физического носителя, но не
          заменяет Bundle v1 signature verification.
        </p>
      </div>
    </div>

    <p v-if="downloadError" class="download-error" role="alert">{{ downloadError }}</p>

    <div class="download-actions">
      <button class="primary-button" type="button" :disabled="!bundle" @click="emit('downloadBundle')">
        <Download :size="19" aria-hidden="true" /> Скачать bundle
      </button>
      <button class="secondary-button" type="button" :disabled="!bundle" @click="emit('downloadSidecar')">
        <Download :size="19" aria-hidden="true" /> Скачать <code>.sha256</code>
      </button>
      <button class="secondary-button" type="button" :disabled="!bundle" @click="emit('downloadHandoff')">
        <Download :size="19" aria-hidden="true" /> Скачать handoff
      </button>
      <button
        class="secondary-button"
        type="button"
        :disabled="!bundle || printHandoffBusy"
        @click="emit('printHandoff')"
      >
        <FileArchive :size="19" aria-hidden="true" />
        {{ printHandoffBusy ? 'Подготовка…' : 'Печатная ведомость' }}
      </button>
    </div>

    <div class="next-steps">
      <h3>Что дальше</h3>
      <ol>
        <li>Сверьте, что bundle и sidecar имеют одинаковое базовое имя, а handoff содержит тот же Delivery ID.</li>
        <li>Скопируйте все три файла на разрешённый физический носитель по вашей организационной процедуре.</li>
        <li>В TARGET откройте workflow «Приём» и загрузите/обнаружьте bundle. Не распаковывайте archive вручную.</li>
      </ol>
    </div>

    <button class="text-button" type="button" @click="emit('reset')">Создать ещё один export</button>
  </section>
</template>

<style scoped>
.ready-card { display: grid; justify-items: center; gap: var(--space-6); padding: var(--space-6); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); box-shadow: var(--shadow-sm); text-align: center; }
.eyebrow { margin: 0; color: var(--color-action); font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
h2, h3, p { margin-top: 0; }
h2 { margin-bottom: 0; font-size: 24px; }
h3 { margin-bottom: var(--space-2); font-size: 16px; }
.lead { max-width: 720px; margin-bottom: 0; color: var(--color-text-muted); line-height: 1.6; }
.ready-icon { display: grid; place-items: center; width: 72px; height: 72px; border-radius: 50%; background: var(--color-success-surface); color: var(--color-success-text); }
.ready-grid { width: 100%; display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-3); text-align: left; }
.info-box { display: grid; gap: var(--space-1); padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-subtle); min-width: 0; }
.info-box > span { color: var(--color-text-muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.info-box strong, .info-box code { overflow-wrap: anywhere; }
.info-box--wide { grid-column: 1 / -1; }
.ready-summary { width: 100%; display: flex; justify-content: space-between; gap: var(--space-3); padding: var(--space-4); border-radius: var(--radius-md); background: var(--color-success-surface); color: var(--color-success-text); text-align: left; }
.notice { display: flex; align-items: flex-start; gap: var(--space-3); padding: var(--space-4); border-radius: var(--radius-md); text-align: left; }
.notice p { margin: var(--space-1) 0 0; line-height: 1.5; }
.notice--warning { background: var(--color-warning-surface); color: var(--color-warning-text); }
.download-actions { display: flex; flex-wrap: wrap; justify-content: center; gap: var(--space-3); }
.download-error { color: var(--color-danger-text); }
.primary-button, .secondary-button { min-height: 44px; display: inline-flex; align-items: center; justify-content: center; gap: var(--space-2); border-radius: var(--radius-md); padding: 0 var(--space-4); font: inherit; font-weight: 700; cursor: pointer; }
.primary-button { border: 1px solid var(--color-action); background: var(--color-action-surface); color: var(--color-on-accent); }
.secondary-button { border: 1px solid var(--color-border-control); background: var(--color-surface); color: var(--color-text); }
button:disabled { cursor: not-allowed; opacity: .55; }
.text-button { border: 0; background: transparent; color: var(--color-action); cursor: pointer; text-decoration: underline; }
.next-steps { width: min(720px, 100%); text-align: left; }
.next-steps li { margin-bottom: var(--space-2); line-height: 1.5; }
@media (max-width: 860px) {
  .ready-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 620px) {
  .ready-grid { grid-template-columns: 1fr; }
  .ready-card { padding: var(--space-4); }
  .ready-summary { align-items: stretch; flex-direction: column; }
  .primary-button, .secondary-button { width: 100%; }
}
</style>
