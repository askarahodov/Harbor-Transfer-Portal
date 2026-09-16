<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import {
  ArrowRight,
  CheckCircle2,
  Clock3,
  History,
  PackageCheck,
  RefreshCw,
  Server,
  ShieldAlert,
  UserRound,
} from 'lucide-vue-next'

import StatePlaceholder from '@/components/StatePlaceholder.vue'
import type { OperationStatus, OperationType } from '@/api/history'
import { useAuthStore } from '@/stores/auth'
import { useDashboardStore } from '@/stores/dashboard'
import { useRuntimeStore } from '@/stores/runtime'

const auth = useAuthStore()
const runtime = useRuntimeStore()
const dashboard = useDashboardStore()

const canStartTransfers = computed(() => auth.canStartTransfers && runtime.contour !== null)
const primaryRoute = computed(() => (runtime.contour === 'TARGET' ? '/import' : '/export'))
const primaryLabel = computed(() =>
  runtime.contour === 'TARGET' ? 'Принять пакет' : 'Отправить артефакты',
)
const primaryDescription = computed(() =>
  runtime.contour === 'TARGET'
    ? 'Проверить подпись и контрольную сумму пакета, затем импортировать в локальный Harbor.'
    : 'Выбрать артефакты локального Harbor и подготовить подписанный offline bundle.',
)
const contourDescription = computed(() => {
  if (runtime.contour === 'SOURCE') return 'Исходный контур: здесь создаются пакеты для физического переноса.'
  if (runtime.contour === 'TARGET') return 'Целевой контур: здесь проверяются и импортируются перенесённые пакеты.'
  return 'Не удалось определить контур установки.'
})

const statusLabels: Record<OperationStatus, string> = {
  CREATED: 'Создана',
  VALIDATING: 'Проверка',
  RUNNING: 'Выполняется',
  PACKAGING: 'Упаковка',
  VERIFYING: 'Верификация',
  UPLOADED: 'Загружен',
  DISCOVERED: 'Обнаружен',
  READY: 'Готово к импорту',
  IMPORTING: 'Импорт',
  VERIFYING_TARGET: 'Проверка TARGET',
  COMPLETED: 'Завершена',
  FAILED: 'Ошибка',
  REJECTED: 'Отклонена',
  CANCELLED: 'Отменена',
}

const typeLabels: Record<OperationType, string> = {
  EXPORT: 'Экспорт',
  IMPORT: 'Импорт',
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '—'
  return new Intl.DateTimeFormat('ru-RU', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(parsed)
}

function statusClass(status: OperationStatus): string {
  if (status === 'COMPLETED') return 'is-success'
  if (status === 'FAILED' || status === 'REJECTED') return 'is-danger'
  if (status === 'CANCELLED') return 'is-muted'
  return 'is-active'
}

async function loadDashboard(): Promise<void> {
  if (!runtime.contour) {
    await runtime.loadRuntime()
  }
  await dashboard.load()
}

onMounted(loadDashboard)
</script>

<template>
  <section class="dashboard" aria-labelledby="dashboard-title">
    <header class="dashboard-header">
      <div>
        <p class="eyebrow">Harbor Transfer Portal</p>
        <h1 id="dashboard-title">Главная</h1>
        <p class="welcome">
          <UserRound :size="18" aria-hidden="true" />
          {{ auth.user?.username ?? 'Пользователь' }} · {{ auth.user?.role ?? '—' }}
        </p>
      </div>
      <div class="contour-card" :class="`contour-${runtime.contour?.toLowerCase() ?? 'unknown'}`">
        <span>Текущий контур</span>
        <strong>{{ runtime.contourLabel }}</strong>
        <small>{{ contourDescription }}</small>
      </div>
    </header>

    <div v-if="runtime.errorCode" class="notice notice-danger" role="alert">
      <ShieldAlert :size="20" aria-hidden="true" />
      <div>
        <strong>Контур backend не подтверждён</strong>
        <p>Не запускайте перенос до восстановления связи с backend и проверки SOURCE/TARGET.</p>
      </div>
    </div>

    <div class="dashboard-grid">
      <article class="panel primary-panel">
        <div class="panel-icon"><PackageCheck :size="26" aria-hidden="true" /></div>
        <div>
          <p class="eyebrow">Основное действие</p>
          <h2>{{ primaryLabel }}</h2>
          <p>{{ primaryDescription }}</p>
        </div>
        <RouterLink v-if="canStartTransfers" class="primary-action" :to="primaryRoute">
          {{ primaryLabel }}
          <ArrowRight :size="18" aria-hidden="true" />
        </RouterLink>
        <div v-else class="read-only-note">
          <strong>Режим только для чтения</strong>
          <span>Роль viewer может просматривать состояние и историю, но не запускать переносы.</span>
        </div>
      </article>

      <article class="panel harbor-panel" aria-labelledby="harbor-status-title">
        <div class="panel-heading">
          <div>
            <p class="eyebrow">Локальная инфраструктура</p>
            <h2 id="harbor-status-title">Harbor</h2>
          </div>
          <Server :size="24" aria-hidden="true" />
        </div>

        <StatePlaceholder
          v-if="dashboard.harborLoading"
          kind="loading"
          title="Проверяем локальный Harbor"
        />
        <div v-else-if="dashboard.harborError" class="compact-error" role="alert">
          <strong>{{ dashboard.harborError.message }}</strong>
          <span v-if="auth.canManageSettings">Проверьте URL, credentials и TLS/CA в настройках.</span>
          <span v-else>Сообщите администратору портала.</span>
          <button type="button" class="secondary-button" @click="dashboard.loadHarbor">
            <RefreshCw :size="16" aria-hidden="true" /> Повторить
          </button>
        </div>
        <div v-else-if="dashboard.harbor" class="harbor-status">
          <span class="status-dot" :class="dashboard.harbor.connected ? 'is-connected' : 'is-disconnected'" />
          <div>
            <strong>{{ dashboard.harbor.connected ? 'Подключение установлено' : 'Harbor недоступен' }}</strong>
            <p>
              Версия: {{ dashboard.harbor.version ?? 'не определена' }}
              <span v-if="dashboard.harbor.auth_mode"> · auth: {{ dashboard.harbor.auth_mode }}</span>
            </p>
          </div>
          <CheckCircle2 v-if="dashboard.harbor.connected" :size="22" aria-label="Harbor доступен" />
          <ShieldAlert v-else :size="22" aria-label="Harbor недоступен" />
        </div>
      </article>
    </div>

    <section class="panel operations-panel" aria-labelledby="recent-operations-title">
      <div class="panel-heading operations-heading">
        <div>
          <p class="eyebrow">Persisted state</p>
          <h2 id="recent-operations-title">Последние операции</h2>
        </div>
        <RouterLink class="history-link" to="/history">
          <History :size="18" aria-hidden="true" /> Вся история
        </RouterLink>
      </div>

      <StatePlaceholder
        v-if="dashboard.historyLoading"
        kind="loading"
        title="Загружаем операции"
      />
      <div v-else-if="dashboard.historyError" class="compact-error" role="alert">
        <strong>{{ dashboard.historyError.message }}</strong>
        <button type="button" class="secondary-button" @click="dashboard.loadRecentOperations">
          <RefreshCw :size="16" aria-hidden="true" /> Повторить
        </button>
      </div>
      <StatePlaceholder
        v-else-if="dashboard.recentOperations.length === 0"
        kind="empty"
        title="Операций пока нет"
        :description="canStartTransfers ? `Начните с действия «${primaryLabel}».` : 'История появится после первого export/import.'"
      />
      <div v-else class="operation-list">
        <article v-for="operation in dashboard.recentOperations" :key="operation.id" class="operation-row">
          <div class="operation-main">
            <span class="operation-type">{{ typeLabels[operation.type] }}</span>
            <strong>{{ operation.delivery_id ?? `Операция #${operation.id}` }}</strong>
            <span class="operation-actor">{{ operation.actor_username }}</span>
          </div>
          <div class="operation-meta">
            <span class="status-badge" :class="statusClass(operation.status)">
              {{ statusLabels[operation.status] }}
            </span>
            <span><Clock3 :size="15" aria-hidden="true" /> {{ formatDate(operation.created_at) }}</span>
            <span>{{ operation.successful_artifacts }}/{{ operation.total_artifacts }} успешно</span>
            <span v-if="operation.failed_artifacts">{{ operation.failed_artifacts }} ошибок</span>
            <span v-if="operation.conflict_artifacts">{{ operation.conflict_artifacts }} конфликтов</span>
          </div>
        </article>
      </div>
    </section>
  </section>
</template>

<style scoped>
.dashboard { display: grid; gap: var(--space-6); }
.dashboard-header { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-6); }
.eyebrow { margin: 0 0 var(--space-1); color: var(--color-text-muted); font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.welcome { display: flex; align-items: center; gap: var(--space-2); margin: 0; }
.contour-card { width: min(360px, 100%); display: grid; gap: var(--space-1); padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); box-shadow: var(--shadow-sm); }
.contour-card span, .contour-card small { color: var(--color-text-muted); }
.contour-card strong { font-size: 22px; }
.contour-source { border-left: 5px solid var(--color-action); }
.contour-target { border-left: 5px solid var(--color-positive-accent); }
.contour-unknown { border-left: 5px solid var(--color-warning-accent); }
.notice { display: flex; gap: var(--space-3); padding: var(--space-4); border-radius: var(--radius-md); }
.notice p { margin: var(--space-1) 0 0; }
.notice-danger { background: var(--color-danger-surface); color: var(--color-danger-text); }
.dashboard-grid { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(300px, .65fr); gap: var(--space-6); }
.panel { padding: var(--space-6); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); box-shadow: var(--shadow-sm); }
.panel h2 { margin: 0; font-size: 19px; }
.panel p { margin: var(--space-2) 0 0; }
.primary-panel { display: grid; grid-template-columns: auto 1fr; align-items: start; gap: var(--space-4); }
.panel-icon { display: grid; place-items: center; width: 48px; height: 48px; border-radius: var(--radius-md); background: var(--color-info-surface); color: var(--color-action); }
.primary-action { grid-column: 1 / -1; display: inline-flex; align-items: center; justify-content: center; gap: var(--space-2); min-height: 44px; padding: 0 var(--space-4); border-radius: var(--radius-md); background: var(--color-action-surface); color: var(--color-on-accent); font-weight: 700; text-decoration: none; }
.read-only-note { grid-column: 1 / -1; display: grid; gap: var(--space-1); padding: var(--space-3); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.read-only-note span { color: var(--color-text-muted); }
.panel-heading { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); margin-bottom: var(--space-4); }
.harbor-status { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: var(--space-3); padding: var(--space-4); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.harbor-status p { margin: var(--space-1) 0 0; }
.status-dot { width: 12px; height: 12px; border-radius: var(--radius-full); }
.is-connected { background: var(--color-positive-accent); }
.is-disconnected { background: var(--color-negative-accent); }
.compact-error { display: grid; gap: var(--space-3); padding: var(--space-4); border-radius: var(--radius-md); background: var(--color-danger-surface); }
.compact-error span { color: var(--color-text-muted); }
.secondary-button { justify-self: start; display: inline-flex; align-items: center; gap: var(--space-2); min-height: 38px; padding: 0 var(--space-3); border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); cursor: pointer; }
.operations-heading { margin-bottom: var(--space-4); }
.history-link { display: inline-flex; align-items: center; gap: var(--space-2); color: var(--color-action); font-weight: 700; text-decoration: none; }
.operation-list { display: grid; gap: var(--space-2); }
.operation-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--space-4); padding: var(--space-3) 0; border-top: 1px solid var(--color-border); }
.operation-row:first-child { border-top: 0; }
.operation-main { display: flex; align-items: center; gap: var(--space-3); min-width: 0; }
.operation-main strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.operation-type { padding: 2px 8px; border-radius: var(--radius-full); background: var(--color-surface-subtle); font-size: 12px; font-weight: 700; }
.operation-actor { color: var(--color-text-muted); }
.operation-meta { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: var(--space-3); color: var(--color-text-muted); font-size: 13px; }
.operation-meta > span:not(.status-badge) { display: inline-flex; align-items: center; gap: var(--space-1); }
.status-badge { padding: 3px 9px; border-radius: var(--radius-full); font-weight: 700; }
.status-badge.is-success { background: var(--color-success-surface); color: var(--color-success-text); }
.status-badge.is-danger { background: var(--color-danger-surface); color: var(--color-danger-text); }
.status-badge.is-muted { background: var(--color-surface-subtle); color: var(--color-text-muted); }
.status-badge.is-active { background: var(--color-warning-surface); color: var(--color-warning-text); }
@media (max-width: 820px) {
  .dashboard-header { display: grid; }
  .contour-card { width: 100%; }
  .dashboard-grid { grid-template-columns: 1fr; }
  .operation-row { grid-template-columns: 1fr; }
  .operation-meta { justify-content: flex-start; }
}
@media (max-width: 560px) {
  .panel { padding: var(--space-4); }
  .primary-panel { grid-template-columns: 1fr; }
  .panel-icon { display: none; }
  .operation-main { align-items: flex-start; flex-wrap: wrap; }
  .operation-main strong { width: 100%; }
}
</style>