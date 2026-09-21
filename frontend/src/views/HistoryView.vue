<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Download,
  RefreshCw,
  Search,
  X,
} from 'lucide-vue-next'

import type { OperationArtifact, OperationStatus } from '@/api/exports'
import StatePlaceholder from '@/components/StatePlaceholder.vue'
import {
  formatDateTimeLocale as formatDate,
  shortDigest as formatShortDigest,
} from '@/presentation/format'
import { useAuthStore } from '@/stores/auth'
import { useHistoryStore } from '@/stores/history'
import { useRuntimeStore, type PortalContour } from '@/stores/runtime'

const history = useHistoryStore()
const auth = useAuthStore()
const runtime = useRuntimeStore()
const router = useRouter()
const detailCloseButton = ref<HTMLButtonElement | null>(null)
let detailTrigger: HTMLElement | null = null
const resumeError = ref<string | null>(null)
const terminalStatuses = new Set<OperationStatus>(['COMPLETED', 'FAILED', 'REJECTED', 'CANCELLED'])

const statuses: OperationStatus[] = [
  'CREATED',
  'VALIDATING',
  'RUNNING',
  'PACKAGING',
  'VERIFYING',
  'UPLOADED',
  'DISCOVERED',
  'READY',
  'IMPORTING',
  'VERIFYING_TARGET',
  'COMPLETED',
  'FAILED',
  'REJECTED',
  'CANCELLED',
]

const hasFilters = computed(() =>
  Boolean(
    history.filters.type ||
      history.filters.status ||
      history.filters.actor ||
      history.filters.search ||
      history.filters.createdFrom ||
      history.filters.createdTo,
  ),
)

function shortDigest(value: string | null | undefined): string {
  return formatShortDigest(value, { maxLength: 24, headLength: 16, tailLength: 8 })
}

function sourceArtifactLabel(item: OperationArtifact): string {
  const repository = item.source_repository ?? item.repository
  const reference = item.source_reference ?? item.source_version ?? item.reference ?? item.version
  if (item.artifact_type === 'helm-chart') {
    return `${repository}/${item.name ?? 'chart'}:${reference ?? '—'}`
  }
  return `${repository}:${reference ?? '—'}`
}

function targetArtifactLabel(item: OperationArtifact): string {
  if (item.target_reference) return item.target_reference
  if (!item.target_repository) return '—'
  const reference = item.reference ?? item.version
  return reference ? `${item.target_repository}:${reference}` : item.target_repository
}

function statusClass(status: OperationStatus): string {
  if (status === 'COMPLETED') return 'status status--success'
  if (['FAILED', 'REJECTED'].includes(status)) return 'status status--danger'
  if (status === 'CANCELLED') return 'status status--muted'
  if (['READY', 'VERIFYING', 'IMPORTING', 'VERIFYING_TARGET', 'RUNNING'].includes(status)) {
    return 'status status--active'
  }
  return 'status'
}

async function openDetail(item: Parameters<typeof history.openDetail>[0]): Promise<void> {
  detailTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const loading = history.openDetail(item)
  await nextTick()
  detailCloseButton.value?.focus()
  await loading
}

async function closeDetail(): Promise<void> {
  history.closeDetail()
  await nextTick()
  detailTrigger?.focus()
  detailTrigger = null
}

onMounted(() => history.load(true))
</script>

<template>
  <section class="history-page" aria-labelledby="history-title">
    <header class="page-header">
      <div>
        <p class="eyebrow">Operations</p>
        <h1 id="history-title">История операций</h1>
        <p>
          Persisted SOURCE/TARGET state из backend API. Экран не читает raw logs и не меняет
          состояние операций.
        </p>
      </div>
      <button class="button button--secondary" type="button" :disabled="history.loading" @click="history.load()">
        <RefreshCw :size="18" aria-hidden="true" />
        Обновить
      </button>
    </header>

    <form class="filters" aria-label="Фильтры истории" @submit.prevent="history.applyFilters">
      <label>
        <span>Тип</span>
        <select v-model="history.filters.type">
          <option value="">Все</option>
          <option value="EXPORT">EXPORT</option>
          <option value="IMPORT">IMPORT</option>
        </select>
      </label>
      <label>
        <span>Статус</span>
        <select v-model="history.filters.status">
          <option value="">Все</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label>
        <span>Actor</span>
        <input v-model.trim="history.filters.actor" type="search" placeholder="operator" autocomplete="off">
      </label>
      <label class="filter-search">
        <span>Delivery / поиск</span>
        <input v-model.trim="history.filters.search" type="search" placeholder="delivery id, comment, error" autocomplete="off">
      </label>
      <label>
        <span>С даты</span>
        <input v-model="history.filters.createdFrom" type="datetime-local">
      </label>
      <label>
        <span>По дату</span>
        <input v-model="history.filters.createdTo" type="datetime-local">
      </label>
      <div class="filter-actions">
        <button class="button button--primary" type="submit" :disabled="history.loading">
          <Search :size="18" aria-hidden="true" />
          Применить
        </button>
        <button v-if="hasFilters" class="button button--secondary" type="button" @click="history.clearFilters">
          Сбросить
        </button>
      </div>
    </form>

    <div v-if="history.error" class="notice notice--danger" role="alert">
      <AlertCircle :size="20" aria-hidden="true" />
      <div><strong>{{ history.error.code }}</strong><p>{{ history.error.message }}</p></div>
    </div>

    <section class="history-list" aria-label="Список операций">
      <span class="visually-hidden" role="status" aria-live="polite" aria-atomic="true">
        <template v-if="!history.loading">
          Показано {{ history.items.length }} из {{ history.total }} операций. Страница {{ history.currentPage }} из {{ history.pageCount }}.
        </template>
      </span>
      <StatePlaceholder
        v-if="history.loading"
        compact
        kind="loading"
        title="Загрузка истории"
      />
      <StatePlaceholder
        v-else-if="history.items.length === 0"
        compact
        kind="empty"
        title="Операции не найдены"
        description="Измените фильтры или дождитесь первой export/import операции."
      />
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID</th><th>Тип / статус</th><th>Delivery</th><th>Actor</th><th>Создано</th><th>Artifacts</th><th>Результат</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in history.items" :key="item.id">
              <td><button class="link-button" type="button" @click="openDetail(item)">#{{ item.id }}</button></td>
              <td><strong>{{ item.type }}</strong><br><span :class="statusClass(item.status)">{{ item.status }}</span></td>
              <td>{{ item.delivery_id ?? '—' }}</td>
              <td>{{ item.actor_username }}</td>
              <td>{{ formatDate(item.created_at) }}</td>
              <td>{{ item.total_artifacts }}</td>
              <td>
                <span v-if="item.retry_of_operation_id">retry of #{{ item.retry_of_operation_id }} · </span>
                <span v-if="item.error_code" class="safe-error">{{ item.error_code }}</span>
                <span v-else>{{ item.successful_artifacts }} ok · {{ item.failed_artifacts }} failed · {{ item.skipped_artifacts }} skipped</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <nav class="pagination" aria-label="Пагинация истории">
      <button class="button button--secondary" type="button" :disabled="!history.hasPrevious || history.loading" @click="history.previousPage">
        <ChevronLeft :size="18" aria-hidden="true" /> Предыдущая
      </button>
      <span>Страница {{ history.currentPage }} из {{ history.pageCount }} · всего {{ history.total }}</span>
      <button class="button button--secondary" type="button" :disabled="!history.hasNext || history.loading" @click="history.nextPage">
        Следующая <ChevronRight :size="18" aria-hidden="true" />
      </button>
    </nav>

    <div v-if="history.selectedSummary" class="drawer-backdrop" @click.self="closeDetail">
      <aside class="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="history-detail-title">
        <header class="drawer-header">
          <div>
            <p class="eyebrow">Operation #{{ history.selectedSummary.id }}</p>
            <h2 id="history-detail-title">{{ history.selectedSummary.type }} · {{ history.selectedSummary.status }}</h2>
          </div>
          <button ref="detailCloseButton" class="icon-button" type="button" aria-label="Закрыть детали" @click="closeDetail"><X :size="20" aria-hidden="true" /></button>
        </header>

        <StatePlaceholder
          v-if="history.detailLoading"
          compact
          kind="loading"
          title="Загрузка деталей"
        />
        <div v-else-if="history.detailError" class="notice notice--danger" role="alert">
          <AlertCircle :size="20" aria-hidden="true" />
          <div><strong>{{ history.detailError.code }}</strong><p>{{ history.detailError.message }}</p></div>
        </div>
        <template v-else-if="history.detail">
          <dl class="metadata-grid">
            <div><dt>Actor</dt><dd>{{ history.detail.actor_username }}</dd></div>
            <div><dt>Delivery ID</dt><dd>{{ history.detail.delivery_id ?? '—' }}</dd></div>
            <div><dt>Начало</dt><dd>{{ formatDate(history.detail.started_at) }}</dd></div>
            <div><dt>Завершение</dt><dd>{{ formatDate(history.detail.finished_at) }}</dd></div>
            <div v-if="history.detail.retry_of_operation_id"><dt>Retry of</dt><dd>#{{ history.detail.retry_of_operation_id }}</dd></div>
            <div v-if="history.detail.failure_policy"><dt>Failure policy</dt><dd>{{ history.detail.failure_policy }}</dd></div>
          </dl>

          <div v-if="history.detail.error_code" class="notice notice--danger">
            <AlertCircle :size="20" aria-hidden="true" />
            <div><strong>{{ history.detail.error_code }}</strong><p>{{ history.detail.error_message ?? 'Операция завершилась с ошибкой.' }}</p></div>
          </div>

          <article v-if="history.canPrepareSelectedRetry || history.retryPlan || history.retryError || history.retryStarted" class="retry-card">
            <h3>Безопасный retry</h3>
            <p>Создаётся новая import operation с тем же immutable destination plan. TARGET повторно проверяется до mutation; overwrite не переносится.</p>
            <button
              v-if="history.canPrepareSelectedRetry && !history.retryPlan"
              class="button button--secondary"
              type="button"
              :disabled="history.retryPreparing"
              @click="history.prepareSelectedRetry"
            >
              <RefreshCw :size="18" aria-hidden="true" />
              {{ history.retryPreparing ? 'Проверка TARGET…' : 'Retry и revalidation' }}
            </button>

            <div v-if="history.retryError" class="notice notice--danger" role="alert">
              <AlertCircle :size="20" aria-hidden="true" />
              <div><strong>{{ history.retryError.code }}</strong><p>{{ history.retryError.message }}</p></div>
            </div>

            <template v-if="history.retryPlan">
              <dl class="metadata-grid retry-meta">
                <div><dt>Новая operation</dt><dd>#{{ history.retryOperationId }}</dd></div>
                <div><dt>Plan ID</dt><dd :title="history.retryPlan.plan_id">{{ shortDigest(history.retryPlan.plan_id) }}</dd></div>
              </dl>

              <div v-if="history.retryHasConflicts" class="notice notice--danger" role="alert">
                <AlertCircle :size="20" aria-hidden="true" />
                <div><strong>Новый TARGET conflict</strong><p>Retry не будет запущен автоматически. Default deny: нужен отдельный явный разбор конфликта.</p></div>
              </div>

              <div class="table-wrap retry-plan-table">
                <table>
                  <thead><tr><th>Artifact</th><th>TARGET</th><th>Revalidation</th><th>Target digest</th></tr></thead>
                  <tbody>
                    <tr v-for="artifact in history.retryPlan.artifacts" :key="artifact.index">
                      <td>{{ artifact.source_repository }}</td>
                      <td>{{ artifact.final_reference ?? '—' }}</td>
                      <td>{{ artifact.classification }}</td>
                      <td :title="artifact.target_digest ?? undefined">{{ shortDigest(artifact.target_digest) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <button
                v-if="!history.retryStarted"
                class="button button--primary"
                type="button"
                :disabled="!history.canStartPreparedRetry"
                @click="history.startPreparedRetry"
              >
                {{ history.retryStarting ? 'Запуск…' : 'Запустить retry без overwrite' }}
              </button>
              <p v-else class="retry-started" role="status">Retry operation #{{ history.retryOperationId }} запущена. История обновлена.</p>
            </template>
          </article>

          <article v-if="history.canDownloadSelectedReport" class="report-card">
            <h3>Отчёты операции</h3>
            <p>CSV и PDF строятся из persisted operation/artifact data и доступны для terminal state.</p>
            <div class="download-actions">
              <button class="button button--secondary" type="button" @click="history.downloadSelectedReport('csv')">
                <Download :size="18" aria-hidden="true" /> Скачать CSV
              </button>
              <button class="button button--secondary" type="button" @click="history.downloadSelectedReport('pdf')">
                <Download :size="18" aria-hidden="true" /> Скачать PDF
              </button>
            </div>
          </article>

          <p v-if="history.downloadError" class="safe-error" role="alert">{{ history.downloadError }}</p>

          <article v-if="history.detail.bundle" class="bundle-card">
            <h3>Bundle metadata</h3>
            <dl class="metadata-grid">
              <div><dt>Файл</dt><dd>{{ history.detail.bundle.filename }}</dd></div>
              <div><dt>Размер</dt><dd>{{ history.detail.bundle.size_bytes.toLocaleString('ru-RU') }} B</dd></div>
              <div><dt>SHA-256</dt><dd :title="history.detail.bundle.sha256">{{ shortDigest(history.detail.bundle.sha256) }}</dd></div>
            </dl>
            <p>Metadata сохраняется в истории независимо от наличия package-файла на диске.</p>
            <button v-if="history.canDownloadSelectedExport" class="button button--secondary" type="button" @click="history.downloadSelectedExport">
              <Download :size="18" aria-hidden="true" /> Скачать через авторизованный ticket
            </button>
          </article>

          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Тип</th><th>SOURCE</th><th>TARGET</th><th>Статус</th><th>Source digest</th><th>Target digest</th><th>Ошибка</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="artifact in history.detail.artifacts" :key="artifact.id">
                  <td>{{ artifact.artifact_type }}</td>
                  <td>{{ sourceArtifactLabel(artifact) }}</td>
                  <td>{{ targetArtifactLabel(artifact) }}</td>
                  <td>{{ artifact.status }}</td>
                  <td :title="artifact.source_digest ?? undefined">{{ shortDigest(artifact.source_digest) }}</td>
                  <td :title="artifact.target_digest ?? undefined">{{ shortDigest(artifact.target_digest) }}</td>
                  <td>{{ artifact.error_code ?? '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <article v-if="history.detail.type === 'IMPORT'" class="receipt-card">
            <h3>Import receipt</h3>
            <StatePlaceholder
              v-if="history.receiptState === 'loading'"
              compact
              kind="loading"
              title="Загрузка receipt"
            />
            <template v-else-if="history.receipt">
              <dl class="metadata-grid">
                <div><dt>Результат</dt><dd>{{ history.receipt.result }}</dd></div>
                <div><dt>Actor</dt><dd>{{ history.receipt.actor_username }}</dd></div>
                <div><dt>SOURCE delivery</dt><dd>{{ history.receipt.source_delivery_id }}</dd></div>
                <div><dt>Bundle SHA-256</dt><dd :title="history.receipt.bundle_sha256">{{ shortDigest(history.receipt.bundle_sha256) }}</dd></div>
                <div><dt>Завершение</dt><dd>{{ formatDate(history.receipt.finished_at) }}</dd></div>
                <div v-if="history.receipt.retry_of_operation_id"><dt>Retry of</dt><dd>#{{ history.receipt.retry_of_operation_id }}</dd></div>
                <div v-if="history.receipt.failure_policy"><dt>Failure policy</dt><dd>{{ history.receipt.failure_policy }}</dd></div>
              </dl>
              <button v-if="history.canDownloadSelectedReceipt" class="button button--secondary" type="button" @click="history.downloadSelectedReceipt">
                <Download :size="18" aria-hidden="true" /> Скачать receipt JSON
              </button>
            </template>
            <StatePlaceholder
              v-else-if="history.receiptState === 'unavailable'"
              compact
              kind="empty"
              title="Receipt недоступен"
              description="Receipt недоступен текущей роли либо ещё не существует. История операции остаётся доступной."
            />
          </article>
        </template>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.history-page { display: grid; gap: var(--space-6); }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--space-4); }
.page-header h1, .drawer-header h2 { margin: 0; }
.page-header p { max-width: 780px; }
.eyebrow { margin: 0 0 var(--space-1); color: var(--color-action); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }
.filters { display: grid; grid-template-columns: repeat(6, minmax(130px, 1fr)); gap: var(--space-3); padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); }
.filters label { display: grid; gap: var(--space-1); font-size: 13px; font-weight: 600; }
.filters input, .filters select { min-width: 0; padding: 10px 12px; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); }
.filter-search { grid-column: span 2; }
.filter-actions { display: flex; align-items: end; gap: var(--space-2); grid-column: span 2; }
.button { display: inline-flex; align-items: center; justify-content: center; gap: var(--space-2); min-height: 40px; padding: 8px 14px; border: 1px solid transparent; border-radius: var(--radius-md); cursor: pointer; font: inherit; font-weight: 600; }
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
