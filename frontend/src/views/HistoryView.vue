<script setup lang="ts">
import { Download, RefreshCw, RotateCcw, X } from 'lucide-vue-next'
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import type { ImportRetryPlanResponse } from '@/api/imports'
import StatePlaceholder from '@/components/StatePlaceholder.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatBytes, formatDateTimeMedium, shortDigest } from '@/presentation/format'
import { useAuthStore } from '@/stores/auth'
import { useHistoryStore } from '@/stores/history'
import { useRuntimeStore } from '@/stores/runtime'

const history = useHistoryStore()
const auth = useAuthStore()
const runtime = useRuntimeStore()
const closeButton = ref<HTMLButtonElement | null>(null)
let detailTrigger: HTMLElement | null = null

const canRetry = computed(() => auth.user?.role === 'admin' || auth.user?.role === 'operator')
const activeRetryPlan = computed<ImportRetryPlanResponse | null>(() => history.retryPlan)
const resultAnnouncement = computed(() => {
  const pages = Math.max(1, Math.ceil(history.total / history.pageSize))
  return `Показано ${history.operations.length} из ${history.total} операций. Страница ${history.page} из ${pages}.`
})

function formatDate(value: string | null | undefined): string {
  return formatDateTimeMedium(value)
}

function formatDigest(value: string | null | undefined): string {
  return shortDigest(value, { maxLength: 28, headLength: 18, tailLength: 8 })
}

function statusTone(status: string): 'success' | 'danger' | 'active' | 'muted' {
  if (status === 'COMPLETED') return 'success'
  if (['FAILED', 'REJECTED', 'CANCELLED'].includes(status)) return 'danger'
  if (['CREATED', 'VALIDATING', 'RUNNING', 'PACKAGING', 'VERIFYING', 'IMPORTING', 'VERIFYING_TARGET'].includes(status)) return 'active'
  return 'muted'
}

function statusText(status: string): string {
  const labels: Record<string, string> = {
    CREATED: 'Создана',
    VALIDATING: 'Проверка',
    RUNNING: 'Выполнение',
    PACKAGING: 'Сборка',
    VERIFYING: 'Проверка bundle',
    UPLOADED: 'Загружено',
    DISCOVERED: 'Обнаружено',
    READY: 'Готово',
    IMPORTING: 'Импорт',
    VERIFYING_TARGET: 'Проверка TARGET',
    COMPLETED: 'Завершено',
    FAILED: 'Ошибка',
    REJECTED: 'Отклонено',
    CANCELLED: 'Отменено',
  }
  return labels[status] ?? status
}

function applyFilters(): void {
  history.applyFilters()
}

function clearFilters(): void {
  history.resetFilters()
}

async function selectOperation(id: number, trigger?: EventTarget | null): Promise<void> {
  detailTrigger = trigger instanceof HTMLElement ? trigger : document.activeElement instanceof HTMLElement ? document.activeElement : null
  await history.selectOperation(id)
  if (history.detail) {
    await nextTick()
    closeButton.value?.focus()
  }
}

function closeDetail(): void {
  history.clearDetail()
}

async function changePage(page: number): Promise<void> {
  await history.goToPage(page)
}

async function downloadReceipt(): Promise<void> {
  if (!history.detail?.receipt) return
  const blob = new Blob([`${JSON.stringify(history.detail.receipt, null, 2)}\n`], {
    type: 'application/json;charset=utf-8',
  })
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = `import-${history.detail.receipt.operation_id}-receipt.json`
  anchor.click()
  URL.revokeObjectURL(href)
}

async function prepareRetry(): Promise<void> {
  if (!history.detail) return
  await history.prepareRetry(history.detail.operation.id)
}

async function executeRetry(): Promise<void> {
  await history.executeRetry()
}

watch(
  () => history.detail,
  async (detail, previous) => {
    if (!detail && previous && detailTrigger?.isConnected) {
      await nextTick()
      detailTrigger.focus()
      detailTrigger = null
    }
  },
)

onMounted(async () => {
  if (!runtime.contour) await runtime.loadRuntime()
  await history.load()
})
</script>

<template>
  <section class="history-page" aria-labelledby="history-title">
    <header class="page-header">
      <div>
        <p class="eyebrow">Operations</p>
        <h1 id="history-title">История операций</h1>
        <p class="lead">
          Persisted export/import операции, per-artifact результаты и immutable receipts.
        </p>
      </div>
      <button class="button button--secondary" type="button" :disabled="history.loading" @click="history.load()">
        <RefreshCw :size="17" aria-hidden="true" /> Обновить
      </button>
    </header>

    <form class="filters" aria-label="Фильтры истории" @submit.prevent="applyFilters">
      <label>
        Тип
        <select v-model="history.draft.type">
          <option value="">Все</option>
          <option value="EXPORT">EXPORT</option>
          <option value="IMPORT">IMPORT</option>
        </select>
      </label>
      <label>
        Статус
        <select v-model="history.draft.status">
          <option value="">Все</option>
          <option v-for="status in history.statusOptions" :key="status" :value="status">{{ statusText(status) }}</option>
        </select>
      </label>
      <label>
        С
        <input v-model="history.draft.dateFrom" type="date">
      </label>
      <label>
        По
        <input v-model="history.draft.dateTo" type="date">
      </label>
      <label class="filter-search">
        Поиск
        <input v-model="history.draft.search" type="search" placeholder="Delivery ID, actor, repository…">
      </label>
      <div class="filter-actions">
        <button class="button button--primary" type="submit">Применить</button>
        <button class="button button--secondary" type="button" @click="clearFilters">Сбросить</button>
      </div>
    </form>

    <p class="visually-hidden" role="status" aria-live="polite" aria-atomic="true">
      {{ resultAnnouncement }}
    </p>

    <StatePlaceholder
      v-if="history.loading"
      kind="loading"
      title="Загрузка истории"
      description="Получаем persisted операции и результаты."
    />
    <StatePlaceholder
      v-else-if="history.error"
      kind="error"
      title="Не удалось загрузить историю"
      :description="history.error.message"
    >
      <template #action>
        <button class="button button--secondary" type="button" @click="history.load()">Повторить</button>
      </template>
    </StatePlaceholder>
    <StatePlaceholder
      v-else-if="history.operations.length === 0"
      kind="empty"
      title="Операции не найдены"
      description="Измените фильтры или выполните новую передачу."
    />
    <template v-else>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Дата</th>
              <th>Тип</th>
              <th>Delivery</th>
              <th>Actor</th>
              <th>Статус</th>
              <th>Прогресс</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="operation in history.operations" :key="operation.id">
              <td>{{ formatDate(operation.created_at) }}</td>
              <td>{{ operation.type }}</td>
              <td>{{ operation.delivery_id ?? '—' }}</td>
              <td>{{ operation.actor_username }}</td>
              <td><StatusBadge :tone="statusTone(operation.status)">{{ statusText(operation.status) }}</StatusBadge></td>
              <td>{{ operation.progress.completed_artifacts }} / {{ operation.progress.total_artifacts }}</td>
              <td>
                <button class="link-button" type="button" @click="selectOperation(operation.id, $event.currentTarget)">
                  Подробнее
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <nav class="pagination" aria-label="Страницы истории">
        <button
          class="button button--secondary"
          type="button"
          :disabled="history.page <= 1"
          @click="changePage(history.page - 1)"
        >
          Назад
        </button>
        <span>Страница {{ history.page }} / {{ Math.max(1, Math.ceil(history.total / history.pageSize)) }}</span>
        <button
          class="button button--secondary"
          type="button"
          :disabled="history.page * history.pageSize >= history.total"
          @click="changePage(history.page + 1)"
        >
          Далее
        </button>
      </nav>
    </template>

    <Teleport to="body">
      <div
        v-if="history.detail"
        class="drawer-backdrop"
        role="presentation"
        @click.self="closeDetail"
        @keydown.esc="closeDetail"
      >
        <aside class="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="detail-title">
          <div class="drawer-header">
            <div>
              <p class="eyebrow">Operation #{{ history.detail.operation.id }}</p>
              <h2 id="detail-title">{{ history.detail.operation.type }} · {{ statusText(history.detail.operation.status) }}</h2>
            </div>
            <button ref="closeButton" class="icon-button" type="button" aria-label="Закрыть детали операции" @click="closeDetail">
              <X :size="18" aria-hidden="true" />
            </button>
          </div>

          <dl class="metadata-grid">
            <div><dt>Delivery ID</dt><dd>{{ history.detail.operation.delivery_id ?? '—' }}</dd></div>
            <div><dt>Actor</dt><dd>{{ history.detail.operation.actor_username }}</dd></div>
            <div><dt>Создана</dt><dd>{{ formatDate(history.detail.operation.created_at) }}</dd></div>
            <div><dt>Завершена</dt><dd>{{ formatDate(history.detail.operation.finished_at) }}</dd></div>
            <div><dt>Комментарий</dt><dd>{{ history.detail.operation.comment ?? '—' }}</dd></div>
            <div><dt>Ошибка</dt><dd>{{ history.detail.operation.error_code ?? '—' }}</dd></div>
          </dl>

          <p v-if="history.detail.operation.error_message" class="notice notice--danger" role="alert">
            {{ history.detail.operation.error_message }}
          </p>

          <section class="table-wrap" aria-labelledby="artifact-results-title">
            <table>
              <thead>
                <tr>
                  <th id="artifact-results-title">Артефакт</th>
                  <th>Статус</th>
                  <th>SOURCE digest</th>
                  <th>TARGET digest</th>
                  <th>Размер</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="artifact in history.detail.artifacts" :key="artifact.id">
                  <td>{{ artifact.repository }}{{ artifact.reference ? `:${artifact.reference}` : artifact.version ? `:${artifact.version}` : '' }}</td>
                  <td><StatusBadge :tone="statusTone(artifact.status)">{{ artifact.status }}</StatusBadge></td>
                  <td :title="artifact.source_digest ?? undefined">{{ formatDigest(artifact.source_digest) }}</td>
                  <td :title="artifact.target_digest ?? undefined">{{ formatDigest(artifact.target_digest) }}</td>
                  <td>{{ formatBytes(artifact.size_bytes) }}</td>
                </tr>
              </tbody>
            </table>
          </section>

          <article v-if="history.detail.bundle" class="bundle-card">
            <h3>Bundle</h3>
            <dl class="metadata-grid">
              <div><dt>Файл</dt><dd>{{ history.detail.bundle.filename }}</dd></div>
              <div><dt>Размер</dt><dd>{{ formatBytes(history.detail.bundle.size_bytes) }}</dd></div>
              <div><dt>SHA-256</dt><dd :title="history.detail.bundle.sha256">{{ formatDigest(history.detail.bundle.sha256) }}</dd></div>
              <div><dt>Signing key</dt><dd :title="history.detail.bundle.signing_key_fingerprint ?? undefined">{{ formatDigest(history.detail.bundle.signing_key_fingerprint) }}</dd></div>
            </dl>
          </article>

          <article v-if="history.detail.receipt" class="receipt-card">
            <h3>Immutable receipt</h3>
            <dl class="metadata-grid">
              <div><dt>Результат</dt><dd>{{ history.detail.receipt.result }}</dd></div>
              <div><dt>Actor</dt><dd>{{ history.detail.receipt.actor_username }}</dd></div>
              <div><dt>Завершён</dt><dd>{{ formatDate(history.detail.receipt.finished_at) }}</dd></div>
              <div><dt>Overwrite</dt><dd>{{ history.detail.receipt.overwrite_confirmed ? 'подтверждён' : 'нет' }}</dd></div>
            </dl>
            <button class="button button--secondary" type="button" @click="downloadReceipt">
              <Download :size="17" aria-hidden="true" /> Скачать receipt JSON
            </button>
          </article>

          <article v-if="history.detail.report" class="report-card">
            <h3>Отчёт</h3>
            <p>{{ history.detail.report.summary }}</p>
            <div class="download-actions">
              <a class="button button--secondary" :href="history.reportDownloadUrl(history.detail.operation.id, 'csv')">
                <Download :size="17" aria-hidden="true" /> CSV
              </a>
              <a class="button button--secondary" :href="history.reportDownloadUrl(history.detail.operation.id, 'pdf')">
                <Download :size="17" aria-hidden="true" /> PDF
              </a>
            </div>
          </article>

          <article v-if="history.detail.operation.type === 'IMPORT' && canRetry" class="retry-card">
            <h3>Повторить только неуспешные артефакты</h3>
            <p>
              Retry не откатывает уже успешные артефакты. Перед новым import backend заново проверяет TARGET и строит fresh plan.
            </p>
            <button
              v-if="!activeRetryPlan"
              class="button button--secondary"
              type="button"
              :disabled="history.retryLoading"
              @click="prepareRetry"
            >
              <RotateCcw :size="17" aria-hidden="true" />
              {{ history.retryLoading ? 'Подготовка…' : 'Подготовить retry plan' }}
            </button>

            <template v-else>
              <p class="retry-meta">
                Parent #{{ activeRetryPlan.parent_operation_id }} · plan {{ activeRetryPlan.destination_plan.plan_id.slice(0, 12) }}…
              </p>
              <div class="table-wrap retry-plan-table">
                <table>
                  <thead><tr><th>Артефакт</th><th>Класс</th><th>TARGET</th></tr></thead>
                  <tbody>
                    <tr v-for="item in activeRetryPlan.destination_plan.artifacts" :key="item.index">
                      <td>{{ item.repository }}{{ item.reference ? `:${item.reference}` : item.version ? `:${item.version}` : '' }}</td>
                      <td><StatusBadge :tone="statusTone(item.classification)">{{ item.classification }}</StatusBadge></td>
                      <td>{{ item.final_reference ?? '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p v-if="!activeRetryPlan.destination_plan.valid" class="notice notice--danger" role="alert">
                Fresh retry plan невалиден. Исправьте TARGET state до запуска.
              </p>
              <p v-if="activeRetryPlan.requires_overwrite" class="notice notice--danger" role="alert">
                Retry содержит CONFLICT и требует явного overwrite confirmation.
              </p>
              <button
                class="button button--primary"
                type="button"
                :disabled="!activeRetryPlan.destination_plan.valid || activeRetryPlan.requires_overwrite || history.retryLoading"
                @click="executeRetry"
              >
                Запустить безопасный retry
              </button>
              <p v-if="history.retryStartedOperationId" class="retry-started" role="status">
                Создана retry operation #{{ history.retryStartedOperationId }}.
              </p>
            </template>
          </article>
        </aside>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.history-page { display: grid; gap: var(--space-6); }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--space-4); }
.eyebrow { margin: 0 0 var(--space-1); color: var(--color-action); font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
h1, h2, h3, p { margin-top: 0; }
.lead { max-width: 760px; margin-bottom: 0; color: var(--color-text-muted); }
.filters { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-3); padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); }
.filters label { display: grid; gap: var(--space-1); font-size: 13px; font-weight: 700; }
.filters input, .filters select { min-height: 40px; width: 100%; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); padding: 0 var(--space-3); font: inherit; font-weight: 400; }
.filter-search { grid-column: span 2; }
.filter-actions { display: flex; align-items: end; gap: var(--space-2); }
.button { min-height: 40px; display: inline-flex; align-items: center; justify-content: center; gap: var(--space-2); padding: 0 var(--space-3); border: 1px solid transparent; border-radius: var(--radius-md); color: inherit; font: inherit; font-weight: 700; text-decoration: none; cursor: pointer; }
.button:disabled { opacity: .5; cursor: not-allowed; }
.button--primary { background: var(--color-action-surface); color: var(--color-on-accent); }
.button--secondary { background: var(--color-surface); border-color: var(--color-border-control); color: var(--color-text); }
.download-actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.table-wrap { overflow-x: auto; border: 1px solid var(--color-border); border-radius: var(--radius-lg); }
table { width: 100%; border-collapse: collapse; background: var(--color-surface); }
th, td { padding: 12px; border-bottom: 1px solid var(--color-border); text-align: left; vertical-align: top; }
th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--color-text-muted); }
.link-button { padding: 0; border: 0; background: transparent; color: var(--color-action); cursor: pointer; font: inherit; font-weight: 700; text-decoration: underline; }
.status { display: inline-flex; margin-top: 4px; padding: 2px 8px; border-radius: var(--radius-full); background: var(--color-surface-subtle); font-size: 12px; }
.status--success { color: var(--color-success-text); }
.status--danger, .safe-error { color: var(--color-danger); }
.status--active { color: var(--color-action); }
.status--muted { color: var(--color-text-muted); }
.notice { display: flex; gap: var(--space-3); padding: var(--space-3); border-radius: var(--radius-md); }
.notice p { margin: 4px 0 0; }
.notice--danger { background: color-mix(in srgb, var(--color-danger) 10%, transparent); color: var(--color-danger); }
.pagination { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.drawer-backdrop { position: fixed; inset: 0; z-index: 30; display: flex; justify-content: flex-end; background: var(--color-overlay-backdrop); }
.detail-drawer { width: min(900px, 96vw); height: 100%; overflow-y: auto; padding: var(--space-5); background: var(--color-background); box-shadow: var(--shadow-drawer); }
.drawer-header { display: flex; justify-content: space-between; gap: var(--space-3); margin-bottom: var(--space-5); }
.icon-button { display: grid; place-items: center; width: 40px; height: 40px; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); cursor: pointer; }
.metadata-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); margin: 0 0 var(--space-4); }
.metadata-grid div, .bundle-card, .receipt-card, .report-card, .retry-card { padding: var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); }
.metadata-grid dt { color: var(--color-text-muted); font-size: 12px; }
.metadata-grid dd { margin: 4px 0 0; overflow-wrap: anywhere; font-weight: 600; }
.bundle-card, .receipt-card, .report-card, .retry-card { margin-top: var(--space-4); margin-bottom: var(--space-4); }
.bundle-card h3, .receipt-card h3, .report-card h3, .retry-card h3 { margin-top: 0; }
.retry-card { display: grid; gap: var(--space-3); }
.retry-card > p { margin: 0; }
.retry-meta { margin: 0; }
.retry-plan-table { max-height: 300px; }
.retry-started { color: var(--color-success-text); font-weight: 600; }
@media (max-width: 1000px) { .filters { grid-template-columns: repeat(2, minmax(0, 1fr)); } .filter-search, .filter-actions { grid-column: span 2; } }
@media (max-width: 640px) { .page-header, .pagination { align-items: stretch; flex-direction: column; } .filters, .metadata-grid { grid-template-columns: 1fr; } .filter-search, .filter-actions { grid-column: auto; } .filter-actions { align-items: stretch; flex-direction: column; } }
</style>
